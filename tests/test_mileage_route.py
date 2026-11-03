# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mileage by route: places, road distance, return trip, route picture.

The map services are replaced by a fake answering what OSRM and Nominatim
would: the tests make no network request.
"""
import io
import json
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, common, tagged

from ..route import geo

# Toulouse (station) to Albi, as OSRM answers it: 75.4 km, 53 minutes.
OSRM = {"code": "Ok", "routes": [{"distance": 75415.7, "duration": 3190.8,
                                  "geometry": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"}]}


class FakeMap:
    """Answers of the map services, and the requests made."""

    def __init__(self, route=OSRM, places=None, down=False):
        self.route, self.places, self.down, self.urls = route, places or {}, down, []

    def __call__(self, url):
        self.urls.append(url)
        if self.down:
            raise OSError("service unreachable")
        if "/route/v1/" in url:
            return json.dumps(self.route).encode()
        for text, (lat, lon, label) in self.places.items():
            if text.replace(" ", "+") in url or text.replace(" ", "%20") in url:
                return json.dumps([{"lat": str(lat), "lon": str(lon), "display_name": label}]).encode()
        return b"[]"


class TestGeo(common.TransactionCase):

    def test_polyline(self):
        self.assertEqual(geo.decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@"),
                         [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)])

    def test_straight_line(self):
        paris, lyon = (48.8566, 2.3522), (45.7640, 4.8357)
        self.assertAlmostEqual(geo.haversine_km(paris, lyon), 392, delta=2)
        route = geo.find_route(FakeMap(down=True), "https://osrm.test",
                               geo.Point(*paris), geo.Point(*lyon))
        self.assertTrue(route.estimated)
        self.assertAlmostEqual(route.km, 392 * geo.ROAD_FACTOR, delta=3)

    def test_main_roads_and_towns(self):
        """The numbers of the roads followed, and the towns read along the path."""
        osrm = {"code": "Ok", "routes": [{"distance": 76000, "duration": 3200, "geometry": "_p~iF~ps|U_ulLnnqC",
                                          "legs": [{"steps": [
                                              {"distance": 60000, "ref": "A 68", "geometry": "_p~iF~ps|U_ulLnnqC"},
                                              {"distance": 14000, "ref": "D 988;D 999", "geometry": "_p~iF~ps|U"},
                                              {"distance": 2000, "ref": "D 1", "geometry": "_p~iF~ps|U"},
                                              {"distance": 500, "name": "Rue Basse"}]}]}]}
        route = geo.road_route(lambda url: json.dumps(osrm).encode(), "https://osrm.test",
                               geo.Point(38.5, -120.2), geo.Point(40.7, -120.95))
        self.assertEqual([ref for ref, _point in route.roads], ["A 68", "D 988"], "short stretches say nothing")

        def reverse(url):
            self.assertIn("/reverse?", url)
            return json.dumps({"name": "Saint-Sulpice", "address": {"town": "Saint-Sulpice-la-Pointe"}}).encode()
        towns = geo.towns_along(reverse, "https://nominatim.test", [(43.6, 1.44), (43.93, 2.15)])
        self.assertEqual([town.label for town in towns], ["Saint-Sulpice-la-Pointe"], "each town once")
        self.assertFalse(geo.towns_along(reverse, "https://nominatim.test", [(43.6, 1.44), (43.62, 1.45)]),
                         "a short route has none")
        png = geo.render_route(route, geo.Point(38.5, -120.2, "A"), geo.Point(40.7, -120.95, "B"),
                               ["A – B", "A", "B", "76 km"], "© OpenStreetMap", towns=towns)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_simplified_path(self):
        """The points the picture would not show go; the bends stay."""
        line = [(43.6 + i * 0.001, 1.44 + i * 0.001) for i in range(50)]
        self.assertEqual(geo.simplify(line), [line[0], line[-1]])
        bend = line + [(43.649, 1.489 + i * 0.001) for i in range(1, 50)]
        self.assertEqual(len(geo.simplify(bend)), 3)

    def test_map_background(self):
        """The tiles under the route, then the others; no map where the server has none."""
        from PIL import Image

        def png(colour, dot=False):
            tile = Image.new("RGB", (256, 256), colour)
            if dot:
                tile.putpixel((10, 10), (0, 0, 0))
            output = io.BytesIO()
            tile.save(output, format="PNG")
            return output.getvalue()

        start, end = geo.Point(43.6115, 1.4541, "Gare"), geo.Point(43.9289, 2.1480, "Albi")
        route = geo.Route(km=75.4, path=[(43.6115, 1.4541), (43.75, 1.8), (43.9289, 2.148)])
        asked = []

        def tiles(url):
            asked.append(url)
            return png((240, 240, 230), dot=True)
        background = geo.map_background(route, start, end, "https://tiles.test/{z}/{x}/{y}.png", tiles)
        self.assertEqual(background.size, (geo.WIDTH - 2 * geo.MARGIN, geo.HEIGHT - geo.MAP_TOP - geo.MARGIN))
        self.assertTrue(geo.render_route(route, start, end, ["A – B", "A", "B", "75 km"], "© IGN",
                                         background=background).startswith(b"\x89PNG"))

        def abroad(url):
            asked.append(url)
            return png((255, 255, 255))
        asked.clear()
        self.assertIsNone(geo.map_background(route, start, end, "https://tiles.test/{z}/{x}/{y}.png", abroad),
                          "an empty tile under the route: no map")
        self.assertEqual(len(asked), 1, "the tiles under the route are asked first")

        path, zoom = geo._frame(route, start, end)[:2]
        under = {(int(x // 256), int(y // 256)) for x, y in (geo._mercator(lat, lon, zoom) for lat, lon in path)}
        missing = []

        def missing_aside(url):
            _base, _zoom, x, y = url.rsplit("/", 3)
            if (int(x), int(y[:-4])) in under:
                return png((240, 240, 230), dot=True)
            missing.append(url)
            raise OSError("404")
        self.assertTrue(geo.map_background(route, start, end, "https://tiles.test/{z}/{x}/{y}.png", missing_aside),
                        "a missing tile away from the route leaves its square plain")
        self.assertTrue(missing)

    def test_the_national_map_of_the_country_of_the_route(self):
        """"auto" picks the map of the country that holds both ends; none between two countries."""
        from PIL import Image

        def tile(url):
            image = Image.new("RGB", (256, 256), (240, 240, 230))
            image.putpixel((10, 10), (0, 0, 0))
            output = io.BytesIO()
            image.save(output, format="PNG")
            asked.append(url)
            return output.getvalue()

        def route(a, b):
            return geo.Route(km=50, path=[(a.lat, a.lon), ((a.lat + b.lat) / 2, (a.lon + b.lon) / 2), (b.lat, b.lon)])

        countries = {}

        def country_of(point):
            return countries.get((point.lat, point.lon))

        asked = []
        cases = (("Toulouse", geo.Point(43.6045, 1.444), geo.Point(43.80, 1.98), "FR", "IGN – Plan IGN", "data.geopf.fr"),
                 ("Zürich", geo.Point(47.3769, 8.5417), geo.Point(47.5, 8.7237), "CH", "swisstopo", "wmts.geo.admin.ch"),
                 ("Madrid", geo.Point(40.4168, -3.7038), geo.Point(39.8628, -4.0273), "ES",
                  "Instituto Geográfico Nacional", "www.ign.es"),
                 ("Berlin", geo.Point(52.52, 13.405), geo.Point(52.3906, 13.0645), "DE", "GeoBasis-DE / BKG",
                  "geodatenzentrum.de"))
        for name, a, b, code, credit, host in cases:
            countries[(a.lat, a.lon)] = countries[(b.lat, b.lon)] = code
            asked.clear()
            picture, found = geo.choose_background(route(a, b), a, b, geo.AUTO_MAPS, tile, country_of)
            self.assertEqual(found, credit, name)
            self.assertTrue(picture is not None and all(host in url for url in asked), name)
        # Between two countries, or in one without a national map: the plain background.
        lyon, geneva = geo.Point(45.764, 4.8357), geo.Point(46.2044, 6.1432)
        countries[(lyon.lat, lyon.lon)], countries[(geneva.lat, geneva.lon)] = "FR", "CH"
        self.assertEqual(geo.choose_background(route(lyon, geneva), lyon, geneva, geo.AUTO_MAPS, tile, country_of),
                         (None, None))
        milan, turin = geo.Point(45.4642, 9.19), geo.Point(45.0703, 7.6869)
        countries[(milan.lat, milan.lon)] = countries[(turin.lat, turin.lon)] = "IT"
        self.assertEqual(geo.choose_background(route(milan, turin), milan, turin, geo.AUTO_MAPS, tile, country_of),
                         (None, None))
        # The geocoder does not answer: no national map.
        a, b = cases[0][1], cases[0][2]
        self.assertEqual(geo.choose_background(route(a, b), a, b, geo.AUTO_MAPS, tile, lambda point: None), (None, None))
        # A tile server of the company is used as it is, without a credit; none draws nothing.
        asked.clear()
        picture, found = geo.choose_background(route(a, b), a, b, "https://tiles.test/{z}/{x}/{y}.png", tile)
        self.assertTrue(picture is not None and found is None and all("tiles.test" in url for url in asked))
        self.assertEqual(geo.choose_background(route(a, b), a, b, False, tile), (None, None))

    def test_the_country_from_the_geocoder(self):
        point = geo.Point(43.6045, 1.444)
        answer = json.dumps({"name": "Toulouse", "address": {"country_code": "fr"}}).encode()
        self.assertEqual(geo.country_code(lambda url: answer, "https://nominatim.test", point), "FR")
        self.assertIsNone(geo.country_code(lambda url: b"{}", "https://nominatim.test", point))
        self.assertIsNone(geo.country_code(lambda url: answer, "", point), "no geocoder")

        def down(url):
            raise OSError("unreachable")
        self.assertIsNone(geo.country_code(down, "https://nominatim.test", point))

    def test_picture(self):
        start, end = geo.Point(43.6115, 1.4541, "Gare Matabiau"), geo.Point(43.9289, 2.1480, "Albi")
        png = geo.render_route(geo.Route(km=75.4, minutes=53, path=[(43.6115, 1.4541), (43.9289, 2.148)]),
                               start, end, ["Toulouse – Albi", "A  Gare", "B  Albi", "75.4 km"],
                               "© OpenStreetMap")
        self.assertTrue(png.startswith(b"\x89PNG"))


@tagged('post_install', '-at_install')
class TestMileageRoute(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.write({'expense_scan_route_url': "https://osrm.test",
                               'expense_scan_geocoder_url': "https://nominatim.test",
                               'expense_scan_map_tiles_url': False})
        cls.mileage = cls.env.ref('hr_expense.expense_product_mileage')
        cls.user = cls.env['res.users'].create({
            'name': "Romain Route", 'login': 'romain_route_zz',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})
        cls.employee = cls.env['hr.employee'].create({
            'name': "Romain Route", 'user_id': cls.user.id, 'expense_mileage_rate': 0.5,
            'private_street': "4 rue des Lilas", 'private_zip': "31000", 'private_city': "Toulouse"})
        Place = cls.env['expense.scan.place']
        cls.station = Place.create({'name': "Gare Matabiau", 'kind': 'station',
                                    'latitude': 43.6115, 'longitude': 1.4541})
        cls.albi = Place.create({'name': "Albi", 'latitude': 43.9289, 'longitude': 2.1480})

    def form(self, user=None):
        expense = self.env['hr.expense'].with_user(user or self.env.user)
        form = Form(expense.with_context(default_employee_id=self.employee.id))
        form.product_id = self.mileage
        return form

    def fake(self, **kwargs):
        fake = FakeMap(**kwargs)
        return fake, patch.object(type(self.env['hr.expense']), '_expense_scan_fetch',
                                  lambda self, url: fake(url))

    def test_home_comes_from_the_employee(self):
        home = self.env['expense.scan.place'].search([('employee_id', '=', self.employee.id)])
        self.assertEqual(home.kind, 'home')
        self.assertIn("Toulouse", home.address)
        self.employee.private_city = "Albi"
        self.assertIn("Albi", home.address)
        self.assertFalse(home.latitude, "moved: looked up again")
        # Somebody else's home is not offered.
        other = self.env['res.users'].create({
            'name': "Autre Zz", 'login': 'autre_route_zz',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.assertFalse(self.env['expense.scan.place'].with_user(other).search(
            [('id', '=', home.id)]))
        self.assertTrue(self.env['expense.scan.place'].with_user(self.user).search(
            [('id', '=', home.id)]))

    def test_a_return_trip_counts_twice(self):
        fake, patched = self.fake()
        with patched:
            form = self.form()
            form.expense_scan_route_from_id = self.station
            form.expense_scan_route_to_id = self.albi
            self.assertEqual(form.quantity, 75.4)
            self.assertEqual(form.name, "Gare Matabiau – Albi")
            form.expense_scan_round_trip = True
            self.assertEqual(form.quantity, 150.8)
            # "return trip" in the language of the database.
            self.assertTrue(form.name.startswith("Gare Matabiau – Albi, "), form.name)
            expense = form.save()
        self.assertAlmostEqual(expense.total_amount, 75.4)
        picture = expense.expense_scan_route_attachment_id
        self.assertEqual(picture.mimetype, 'image/png')
        self.assertEqual(expense.message_main_attachment_id, picture)
        self.assertTrue(any("/route/v1/driving/1.454100,43.611500;2.148000,43.928900" in url
                            for url in fake.urls))

    def test_changing_a_place_measures_again(self):
        fake, patched = self.fake()
        with patched:
            expense = self.env['hr.expense'].create({
                'name': "Trajet", 'employee_id': self.employee.id, 'product_id': self.mileage.id,
                'expense_scan_route_from_id': self.station.id,
                'expense_scan_route_to_id': self.albi.id})
            self.assertEqual(expense.quantity, 75.4)
            first = expense.expense_scan_route_attachment_id
            fake.route = dict(OSRM, routes=[dict(OSRM["routes"][0], distance=12000.0)])
            other = self.env['expense.scan.place'].create(
                {'name': "Blagnac", 'latitude': 43.635, 'longitude': 1.3678})
            expense.expense_scan_route_to_id = other
        self.assertEqual(expense.quantity, 12.0)
        self.assertFalse(first.exists(), "the old picture is replaced")

    def test_without_a_route_service_the_distance_is_estimated(self):
        fake, patched = self.fake(down=True)
        with patched:
            form = self.form()
            form.expense_scan_route_from_id = self.station
            form.expense_scan_route_to_id = self.albi
        self.assertTrue(form.expense_scan_route_estimated)
        crow = geo.haversine_km((43.6115, 1.4541), (43.9289, 2.1480))
        self.assertAlmostEqual(form.quantity, round(crow * geo.ROAD_FACTOR, 1), places=1)

    def test_a_typed_place_is_found_on_the_map_and_kept(self):
        fake, patched = self.fake(places={"Aeroport de Blagnac": (43.6293, 1.3638,
                                                                  "Aéroport Toulouse-Blagnac, Blagnac")})
        with patched:
            place_id, _name = self.env['expense.scan.place'].with_user(self.user).name_create(
                "Aeroport de Blagnac")
            with self.assertRaises(UserError):
                self.env['expense.scan.place'].name_create("Nulle part Zz")
        place = self.env['expense.scan.place'].browse(place_id)
        self.assertEqual(place.kind, 'airport')
        self.assertAlmostEqual(place.latitude, 43.6293)

    def test_a_customer_typed_is_taken_from_the_contacts(self):
        customer = self.env['res.partner'].create({
            'name': "Zorgal Industries", 'is_company': True, 'street': "1 avenue de l'Usine",
            'zip': "81000", 'city': "Albi", 'partner_latitude': 43.92, 'partner_longitude': 2.15})
        fake, patched = self.fake()
        with patched:
            place_id, _name = self.env['expense.scan.place'].name_create("Zorgal Industries")
        place = self.env['expense.scan.place'].browse(place_id)
        self.assertEqual((place.kind, place.partner_id), ('customer', customer))
        self.assertEqual(fake.urls, [], "the contact's coordinates are used")

    def test_the_return_trip_switch_doubles_without_asking_the_route_again(self):
        """The switch saves at once, without the form's onchange."""
        fake, patched = self.fake()
        with patched:
            expense = self.env['hr.expense'].create({
                'name': "Trajet", 'employee_id': self.employee.id, 'product_id': self.mileage.id,
                'expense_scan_route_from_id': self.station.id,
                'expense_scan_route_to_id': self.albi.id})
            routes = len([url for url in fake.urls if "/route/v1/" in url])
            expense.expense_scan_round_trip = True
        self.assertEqual(expense.quantity, 150.8)
        self.assertEqual(len([url for url in fake.urls if "/route/v1/" in url]), routes)

    def test_an_expense_created_elsewhere_is_named_after_its_route(self):
        """An import names the expense after its category, as Odoo does: the route renames it."""
        fake, patched = self.fake()
        with patched:
            expense = self.env['hr.expense'].create({
                'name': self.mileage.name, 'employee_id': self.employee.id, 'product_id': self.mileage.id,
                'expense_scan_route_from_id': self.station.id,
                'expense_scan_route_to_id': self.albi.id})
            self.assertEqual(expense.name, "Gare Matabiau – Albi")
            expense.expense_scan_round_trip = True
            self.assertTrue(expense.name.startswith("Gare Matabiau – Albi, "), expense.name)
            named = self.env['hr.expense'].create({
                'name': "Visite chantier", 'employee_id': self.employee.id, 'product_id': self.mileage.id,
                'expense_scan_route_from_id': self.station.id,
                'expense_scan_route_to_id': self.albi.id})
        self.assertEqual(named.name, "Visite chantier", "a description written by hand stays")

    def test_the_same_route_again_is_a_new_expense_dated_today(self):
        """The trip repeated: places, return trip and distance carried over, nothing measured again."""
        from odoo import fields
        fake, patched = self.fake()
        with patched:
            first = self.env['hr.expense'].create({
                'name': "Visite chantier", 'employee_id': self.employee.id, 'product_id': self.mileage.id,
                'date': "2026-09-02", 'expense_scan_round_trip': True,
                'expense_scan_route_from_id': self.station.id,
                'expense_scan_route_to_id': self.albi.id})
            routes = len([url for url in fake.urls if "/route/v1/" in url])
            action = first.action_expense_scan_same_route()
            again = self.env['hr.expense'].browse(action['res_id'])
            self.assertEqual(len([url for url in fake.urls if "/route/v1/" in url]), routes,
                             "the distance is known: the route service is not asked again")
        self.assertNotEqual(again, first)
        self.assertEqual(again.date, fields.Date.context_today(again))
        self.assertEqual((again.expense_scan_route_from_id, again.expense_scan_route_to_id),
                         (first.expense_scan_route_from_id, first.expense_scan_route_to_id))
        self.assertTrue(again.expense_scan_round_trip)
        self.assertEqual((again.expense_scan_route_km, again.quantity), (first.expense_scan_route_km, first.quantity))
        self.assertEqual(again.product_id, first.product_id)
        self.assertEqual(again.name, "Visite chantier", "what is written about the trip is kept")
        self.assertTrue(again.expense_scan_route_attachment_id, "the picture of the route is drawn for the new expense")
        self.assertNotEqual(again.expense_scan_route_attachment_id, first.expense_scan_route_attachment_id)
        self.assertEqual(again.state, 'draft')
        # Without a route there is nothing to repeat.
        plain = self.env['hr.expense'].create({'name': "Autre", 'employee_id': self.employee.id})
        with self.assertRaises(UserError):
            plain.action_expense_scan_same_route()

    def test_the_map_of_a_route_is_not_retouched(self):
        fake, patched = self.fake()
        with patched:
            expense = self.env['hr.expense'].create({
                'name': "Trajet", 'employee_id': self.employee.id, 'product_id': self.mileage.id,
                'expense_scan_route_from_id': self.station.id,
                'expense_scan_route_to_id': self.albi.id})
        self.assertEqual(expense.message_main_attachment_id, expense.expense_scan_route_attachment_id)
        with self.assertRaises(UserError):
            expense.expense_scan_retouch_data()

    def test_a_place_typed_again_is_not_doubled(self):
        place_id, _name = self.env['expense.scan.place'].name_create("gare matabiau")
        self.assertEqual(place_id, self.station.id)

    def test_an_employee_cannot_delete_a_place(self):
        with self.assertRaises(AccessError):
            self.albi.with_user(self.user).unlink()
