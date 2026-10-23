# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mileage expenses entered by route: from one place to another, one way or return.

The distance comes from the road route between the two places, and the
picture of the route becomes the receipt of the expense. The kilometres
stay editable: a detour made for the job is the employee's to declare.
"""
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import file_path, format_date

from ..route import geo

_logger = logging.getLogger(__name__)

#: Timeout of a request to the map services, in seconds.
FETCH_TIMEOUT = 10
FONTS = ('web/static/fonts/google/Roboto/Roboto-Regular.ttf',
         'web/static/fonts/google/Roboto/Roboto-Bold.ttf')
#: Fields that change the route, hence its picture; the places change its distance.
PLACE_FIELDS = {'expense_scan_route_from_id', 'expense_scan_route_to_id'}
ROUTE_FIELDS = PLACE_FIELDS | {'expense_scan_round_trip'}


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    expense_scan_mileage = fields.Boolean(
        string="Mileage", compute='_compute_expense_scan_mileage',
        help="The category is counted in kilometres or miles.")
    expense_scan_route_from_id = fields.Many2one(
        'expense.scan.place', string="From", copy=False, ondelete='restrict',
        help="Start of the route: type a place, a customer or an address, or pick one in the list.")
    expense_scan_route_to_id = fields.Many2one(
        'expense.scan.place', string="To", copy=False, ondelete='restrict')
    expense_scan_round_trip = fields.Boolean(
        string="Return trip", help="The distance counts twice: there and back.")
    expense_scan_route_km = fields.Float(
        string="One way (km)", digits=(16, 1), readonly=True, copy=False)
    expense_scan_route_minutes = fields.Float(readonly=True, copy=False)
    expense_scan_route_estimated = fields.Boolean(
        string="Estimated distance", readonly=True, copy=False,
        help="No route service answered: the distance is the straight line lengthened "
             "by a quarter. Check it.")
    #: Path of the route, as JSON [[latitude, longitude], ...]: the picture is
    #: drawn again (return trip switched) without asking the route again.
    expense_scan_route_path = fields.Text(string="Route path", readonly=True, copy=False)
    expense_scan_route_attachment_id = fields.Many2one(
        'ir.attachment', string="Route picture", readonly=True, copy=False,
        ondelete='set null')
    #: Last description written from the route: the module may rewrite it,
    #: not one the employee typed.
    expense_scan_route_name = fields.Char(copy=False)
    expense_scan_route_summary = fields.Char(
        string="Route", compute='_compute_expense_scan_route_summary')

    @api.depends('product_id', 'product_uom_id')
    def _compute_expense_scan_mileage(self):
        for expense in self:
            expense.expense_scan_mileage = bool(expense.product_id) \
                and expense._expense_scan_is_distance()

    @api.depends('expense_scan_route_km', 'expense_scan_route_minutes', 'expense_scan_round_trip',
                 'expense_scan_route_estimated')
    def _compute_expense_scan_route_summary(self):
        for expense in self:
            km = expense.expense_scan_route_km
            if not km:
                expense.expense_scan_route_summary = False
                continue
            text = _("%(km)s km one way", km=round(km, 1))
            if expense.expense_scan_round_trip:
                text = _("%(km)s km one way, %(total)s km there and back",
                         km=round(km, 1), total=round(km * 2, 1))
            if expense.expense_scan_route_minutes:
                text += " · " + geo.format_minutes(expense.expense_scan_route_minutes)
            if expense.expense_scan_route_estimated:
                text += " · " + _("estimated, no route found")
            expense.expense_scan_route_summary = text

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------

    @api.model
    def _expense_scan_fetch(self, url):
        """GET of a map service; the bytes of the answer.

        The services ask every caller to say who it is.
        """
        import requests
        response = requests.get(url, timeout=FETCH_TIMEOUT, headers={
            'User-Agent': 'Odoo expense_scan (https://github.com/TTC-Technologies/expense_scan)',
            'Accept-Language': (self.env.lang or 'en_US').replace('_', '-'),
        })
        response.raise_for_status()
        return response.content

    # ------------------------------------------------------------------
    # Route
    # ------------------------------------------------------------------

    @api.onchange('expense_scan_route_from_id', 'expense_scan_route_to_id', 'expense_scan_round_trip')
    def _onchange_expense_scan_route(self):
        for expense in self:
            if expense.expense_scan_route_from_id and expense.expense_scan_route_to_id:
                expense._expense_scan_apply_route(expense._expense_scan_find_route())

    def _expense_scan_places(self):
        """Both places, with elevated rights: an approver does not read an employee's home."""
        self.ensure_one()
        return self.expense_scan_route_from_id.sudo(), self.expense_scan_route_to_id.sudo()

    def _expense_scan_find_route(self):
        self.ensure_one()
        origin, destination = self._expense_scan_places()
        start, end = origin._expense_scan_point(), destination._expense_scan_point()
        if (start.lat, start.lon) == (end.lat, end.lon):
            raise UserError(_("The start and the end of the route are the same place."))
        company = self.company_id or self.env.company
        return geo.find_route(self._expense_scan_fetch, company.expense_scan_route_url, start, end)

    def _expense_scan_apply_route(self, route):
        """Distance, quantity and description from the route found."""
        self.ensure_one()
        factor = 2 if self.expense_scan_round_trip else 1
        self.expense_scan_route_km = round(route.km, 1)
        self.expense_scan_route_minutes = route.minutes
        self.expense_scan_route_estimated = route.estimated
        self.expense_scan_route_path = self._expense_scan_path_json(route)
        if self.expense_scan_mileage:
            self.quantity = round(route.km, 1) * factor
        if self._expense_scan_route_may_rename():
            self.name = self.expense_scan_route_name = self._expense_scan_route_name()

    def _expense_scan_route_may_rename(self):
        """Whether the description is still one the module or Odoo wrote.

        Odoo names a new expense after its category, in the language of the
        one who entered it: "[KM] Kilométrage" counts as automatic too.
        """
        self.ensure_one()
        name = (self.name or '').strip()
        if not name or name == self.expense_scan_route_name or self._expense_scan_name_is_automatic():
            return True
        product = self.product_id
        if not product:
            return False
        labels = set()
        for code, _label in self.env['res.lang'].get_installed():
            translated = product.with_context(lang=code)
            labels |= {translated.display_name, translated.name}
        return name in labels

    def _expense_scan_route_name(self):
        self.ensure_one()
        name = "%s – %s" % (self.expense_scan_route_from_id.name, self.expense_scan_route_to_id.name)
        if self.expense_scan_round_trip:
            name = _("%s, return trip", name)
        return name

    @api.model_create_multi
    def create(self, vals_list):
        expenses = super().create(vals_list)
        for expense, vals in zip(expenses, vals_list):
            if expense.expense_scan_route_from_id and expense.expense_scan_route_to_id:
                # The form sends the distance it found; another caller does not.
                expense._expense_scan_draw_route(measure='expense_scan_route_km' not in vals)
        return expenses

    def write(self, vals):
        result = super().write(vals)
        if ROUTE_FIELDS & set(vals) and not self.env.context.get('expense_scan_drawing_route'):
            # Other places measure again, unless the form sends the distance it
            # found; "Return trip" alone only doubles the known distance.
            self.filtered(lambda e: e.expense_scan_route_from_id and e.expense_scan_route_to_id) \
                ._expense_scan_draw_route(measure=bool(PLACE_FIELDS & set(vals))
                                          and 'expense_scan_route_km' not in vals)
        return result

    def _expense_scan_draw_route(self, measure=False):
        """Picture of the route, attached as the receipt; replaces the previous one.

        ``measure``: find the distance again (places changed without it).
        """
        for expense in self.with_context(expense_scan_drawing_route=True):
            route = None
            values = {}
            if measure or not expense.expense_scan_route_km:
                route = expense._expense_scan_find_route()
                values = {
                    'expense_scan_route_km': round(route.km, 1),
                    'expense_scan_route_minutes': route.minutes,
                    'expense_scan_route_estimated': route.estimated,
                    'expense_scan_route_path': expense._expense_scan_path_json(route),
                }
            if expense.expense_scan_mileage:
                factor = 2 if expense.expense_scan_round_trip else 1
                quantity = values.get('expense_scan_route_km', expense.expense_scan_route_km) * factor
                if abs(quantity - expense.quantity) > 0.001:
                    values['quantity'] = quantity
            if values:
                expense.write(values)
            try:
                content = expense._expense_scan_route_picture(route)
            except Exception:  # noqa: BLE001 - the expense is saved without its picture
                _logger.warning("Route picture of expense %s failed", expense.id, exc_info=True)
                continue
            old = expense.expense_scan_route_attachment_id
            attachment = self.env['ir.attachment'].create({
                'name': "%s.png" % expense._expense_scan_route_name().replace("/", "-"),
                'raw': content,
                'mimetype': 'image/png',
                'res_model': 'hr.expense',
                'res_id': expense.id,
            })
            expense.write({'expense_scan_route_attachment_id': attachment.id,
                           'message_main_attachment_id': attachment.id})
            # The places used last come first in the list.
            (expense.expense_scan_route_from_id | expense.expense_scan_route_to_id).sudo().write(
                {'last_used': fields.Datetime.now()})
            if old:
                old.unlink()

    @staticmethod
    def _expense_scan_path_json(route):
        return json.dumps([[round(lat, 5), round(lon, 5)] for lat, lon in route.path])

    def _expense_scan_known_route(self):
        """The route already measured, from the fields of the expense."""
        self.ensure_one()
        try:
            path = [tuple(point) for point in json.loads(self.expense_scan_route_path or "[]")]
        except ValueError:
            path = []
        return geo.Route(km=self.expense_scan_route_km, minutes=self.expense_scan_route_minutes,
                         path=path, estimated=self.expense_scan_route_estimated)

    def _expense_scan_route_picture(self, route=None):
        """PNG of the route: the path, the places, the distance and the date."""
        self.ensure_one()
        origin, destination = self._expense_scan_places()
        start, end = origin._expense_scan_point(), destination._expense_scan_point()
        company = self.company_id or self.env.company
        if route is None:
            route = self._expense_scan_known_route()
        lang = self.employee_id.user_id.lang or self.env.user.lang or self.env.lang
        expense = self.with_context(lang=lang)
        env = expense.env
        km = round(route.km, 1)
        distance = env._("%(km)s km", km=km)
        if self.expense_scan_round_trip:
            distance = env._("%(km)s km × 2 = %(total)s km", km=km, total=round(km * 2, 1))
        details = [distance]
        if route.minutes:
            details.append(env._("%s per trip", geo.format_minutes(route.minutes)))
        if self.date:
            details.append(format_date(env, self.date))
        if route.estimated:
            details.append(env._("estimated distance"))
        lines = [
            expense._expense_scan_route_name(),
            "A  %s" % origin._expense_scan_public_address(),
            "B  %s" % destination._expense_scan_public_address(),
            " · ".join(details),
        ]
        attribution = env._("Route: OSRM · map data © OpenStreetMap contributors") \
            if not route.estimated else env._("Map data © OpenStreetMap contributors")
        fonts = tuple(self._expense_scan_font(name) for name in FONTS)
        return geo.render_route(route, start, end, lines, attribution,
                                tiles_url=company.expense_scan_map_tiles_url or None,
                                fetch=self._expense_scan_fetch, fonts=fonts)

    @staticmethod
    def _expense_scan_font(name):
        try:
            return file_path(name)
        except (FileNotFoundError, ValueError):
            return None

    def action_expense_scan_route_map(self):
        """The route on openstreetmap.org, to look at it on a real map."""
        self.ensure_one()
        origin, destination = self._expense_scan_places()
        start, end = origin._expense_scan_point(), destination._expense_scan_point()
        return {'type': 'ir.actions.act_url', 'url': geo.osm_link(start, end), 'target': 'new'}
