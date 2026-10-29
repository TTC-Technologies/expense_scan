# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mileage by route: places, road distance, return trip, route picture.

The map services are replaced by a fake answering what OSRM and Nominatim
would: the tests make no network request.
"""
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
