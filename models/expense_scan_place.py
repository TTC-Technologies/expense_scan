# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Places of the mileage expenses: the start and the end of a route.

Picked in a list rather than typed: the employee's home, the office, the
customers, the airports and stations the company uses, and the places
already used. A place typed for the first time is looked up on the map
(geocoding) and kept for the next times.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain

from ..route import geo

_logger = logging.getLogger(__name__)



class ExpenseScanPlace(models.Model):
    _name = 'expense.scan.place'
    _description = "Place of a route"
    _order = 'last_used desc, sequence, name, id'

    name = fields.Char(string="Name", required=True)
    kind = fields.Selection([
        ('home', "Home"),
        ('work', "Office"),
        ('customer', "Customer"),
        ('airport', "Airport"),
        ('station', "Station"),
        ('other', "Other"),
    ], string="Kind", default='other', required=True)
    address = fields.Char(string="Address", help="Full address, as found on the map.")
    latitude = fields.Float(string="Latitude", digits=(10, 7))
    longitude = fields.Float(string="Longitude", digits=(10, 7))
    partner_id = fields.Many2one('res.partner', string="Contact", ondelete='cascade',
                                 help="The customer or the office this place stands for.")
    employee_id = fields.Many2one('hr.employee', string="Employee", ondelete='cascade',
                                  help="Home of this employee: offered to them only, and "
                                       "read by HR.")
    company_id = fields.Many2one('res.company', string="Company",
                                 default=lambda self: self.env.company)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    last_used = fields.Datetime(string="Last used", readonly=True, copy=False)

    _check_coordinates = models.Constraint(
        'CHECK(latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)',
        "A latitude lies between -90 and 90, a longitude between -180 and 180.",
    )

    @api.depends('name', 'kind')
    def _compute_display_name(self):
        icons = {'home': "🏠", 'work': "🏢", 'customer': "🤝", 'airport': "✈", 'station': "🚆"}
        for place in self:
            icon = icons.get(place.kind)
            place.display_name = "%s %s" % (icon, place.name) if icon else place.name

    def _expense_scan_point(self):
        """The place on the map, looked up first if needed: a ``geo.Point``."""
        self.ensure_one()
        if not (self.latitude or self.longitude):
            found = self._expense_scan_geocode(self.address or self.name)
            if not found:
                raise UserError(_("\"%s\" was not found on the map: write its address "
                                  "more fully (street, town).", self.name))
            self.sudo().write({'latitude': found.lat, 'longitude': found.lon,
                               'address': self.address or found.label})
        return geo.Point(self.latitude, self.longitude, self.name)

    def _expense_scan_public_address(self):
        """The address shown on a route picture: a home only by its town."""
        self.ensure_one()
        if self.kind == 'home':
            town = self.sudo().employee_id.private_city
            return "%s (%s)" % (self.name, town) if town else self.name
        return self.address or self.name

    @api.model
    def _expense_scan_geocode(self, text):
        """The first place the map finds for a text, or ``None``."""
        company = self.env.company
        base = company.expense_scan_geocoder_url
        if not base:
            return None
        lang = (self.env.lang or 'en_US').split('_')[0]
        countries = [company.country_id.code.lower()] if company.country_id.code else None
        fetch = self.env['hr.expense']._expense_scan_fetch
        try:
            found = geo.geocode(fetch, base, text, language=lang, country_codes=countries, limit=1) \
                or (countries and geo.geocode(fetch, base, text, language=lang, limit=1))
        except Exception as error:  # noqa: BLE001 - the map unreachable is said, not raised
            _logger.warning("Geocoding of a place failed: %s", error)
            return None
        return found[0] if found else None

    @api.model
    def _expense_scan_kind_of(self, label):
        """Airport or station, from the words of the address found."""
        words = set(geo_words(label))
        for word, kind in (('aeroport', 'airport'), ('airport', 'airport'), ('flughafen', 'airport'),
                           ('aeroporto', 'airport'), ('aeropuerto', 'airport'),
                           ('gare', 'station'), ('station', 'station'), ('bahnhof', 'station'),
                           ('stazione', 'station'), ('estacion', 'station')):
            if word in words:
                return kind
        return 'other'

    @api.model
    def name_create(self, name):
        """A place typed in the expense: a contact of that name, or the map.

        The contact first: typing "Airbus" makes a place of the customer's
        address. Otherwise the text is looked up on the map, and kept.
        """
        name = (name or "").strip()
        # A place already known by that name is taken again, not doubled.
        known = self.search([('name', '=ilike', name)], limit=1)
        if known:
            return known.id, known.display_name
        partners = self.env['res.partner'].search(
            [('name', '=ilike', name), ('city', '!=', False)], limit=2)
        if len(partners) == 1:
            place = self._expense_scan_partner_place(partners, 'customer', self.env.company)
            return place.id, place.display_name
        found = self._expense_scan_geocode(name)
        if not found:
            raise UserError(_("\"%s\" was not found on the map: write its address more fully "
                              "(street, town), or pick a place in the list.", name))
        place = self.create({
            'name': name,
            'address': found.label,
            'latitude': found.lat,
            'longitude': found.lon,
            'kind': self._expense_scan_kind_of(found.label + " " + name),
        })
        return place.id, place.display_name

    # ------------------------------------------------------------------
    # The usual places: home and office of each employee
    # ------------------------------------------------------------------

    @api.model
    def _expense_scan_sync_employees(self, employees):
        """Keep the home and the office of employees among the places.

        Run when an employee's address changes. Elevated rights: an employee
        does not read the private address held by HR, yet drives home from a
        customer; the place shows them its name, not the address.
        """
        Place = self.sudo().with_context(active_test=False)
        for employee in employees.sudo():
            home = ", ".join(filter(None, (
                employee.private_street, employee.private_street2,
                " ".join(filter(None, (employee.private_zip, employee.private_city))),
                employee.private_country_id.name)))
            current = Place.search([('employee_id', '=', employee.id), ('kind', '=', 'home')],
                                   limit=1)
            if home and not current:
                # Named in the employee's language: the sync runs from HR or at install.
                lang = employee.user_id.lang or employee.work_contact_id.lang or self.env.lang
                Place.create({'name': Place.with_context(lang=lang).env._("Home"),
                              'kind': 'home', 'address': home,
                              'employee_id': employee.id, 'company_id': employee.company_id.id})
            elif current and current.address != (home or current.address):
                # Moved house: the place follows, and is looked up again.
                current.write({'address': home, 'latitude': 0.0, 'longitude': 0.0, 'active': True})
            office = employee.address_id or employee.company_id.partner_id
            if office and office.city:
                self._expense_scan_partner_place(office, 'work', employee.company_id)

    @api.model
    def _expense_scan_partner_place(self, partner, kind, company):
        """The place of a contact, created or brought up to date."""
        Place = self.sudo().with_context(active_test=False)
        place = Place.search([('partner_id', '=', partner.id)], limit=1)
        address = ", ".join(filter(None, (
            partner.street, partner.street2, " ".join(filter(None, (partner.zip, partner.city))),
            partner.country_id.name)))
        values = {'address': address}
        if partner.partner_latitude or partner.partner_longitude:
            values.update(latitude=partner.partner_latitude, longitude=partner.partner_longitude)
        if not place:
            place = Place.create(dict(values, name=partner.name, kind=kind, partner_id=partner.id,
                                      company_id=company.id if company else False))
        elif place.address != address:
            place.write(dict({'latitude': 0.0, 'longitude': 0.0}, **values))
        return place.sudo(False)

    @api.model
    def _search_display_name(self, operator, value):
        # The address counts too: "Blagnac" finds the airport.
        domain = super()._search_display_name(operator, value)
        if operator in ('ilike', 'like', '=ilike') and value:
            domain = Domain.OR([domain, [('address', operator, value)]])
        return domain


def geo_words(text):
    from ..ocr import lexicon
    return lexicon.fold(text or "").split()
