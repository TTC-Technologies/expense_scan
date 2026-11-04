# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mileage scales by yearly distance, applied cumulatively.

Countries pay mileage by a scale of the yearly distance, in two ways:

* ``formula`` (France): the allowance of the whole year is ``d × rate + fixed part`` of the bracket
  the yearly distance falls in. The fixed part is not an extra to claim: it makes each bracket meet
  the previous one;
* ``marginal`` (United Kingdom, many others): each kilometre or mile is paid at the rate of the
  bracket it falls in.

A scale belongs to a vehicle of the company ("Car 4 CV (electric)", "Car or van", "Motorbike"...), a
year and a method; its brackets are lines (distance from which a rate applies, rate, fixed part).
Everything is a table the administrator edits: nothing is rewritten by an update of the module, and a
scale is copied to the next year to be adjusted when the rates change.

An employee who drives their own vehicle (one or several, on their record, with the fiscal power and
the registration certificate) is paid by the scale of its category: each mileage expense is worth the
scale of the year after the trip, less the scale before it, for that vehicle. The thresholds and the fixed part need no thought: the trip
that crosses a threshold carries the rates of both sides, and the cumulated allowance of the year is
always the scale of the cumulated distance. A trip in a company vehicle, or by an employee
without a vehicle of their own, keeps the single rate of the record, or the cost of the category (the
company may count it by the scale and re-invoice it to a customer).
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

#: Scales to start with, by country of the company: {vehicle: [(distance from, rate, fixed part)]}.
#: Rates change every year: they are to check against the scale in force.
#: {category: (fiscal power from, to (0: no limit), brackets)}
FRANCE_THERMAL = {
    '3 CV or less': (0, 3, [(0, 0.529, 0.0), (5000, 0.316, 1065.0), (20000, 0.370, 0.0)]),
    '4 CV': (4, 4, [(0, 0.606, 0.0), (5000, 0.340, 1330.0), (20000, 0.407, 0.0)]),
    '5 CV': (5, 5, [(0, 0.636, 0.0), (5000, 0.357, 1395.0), (20000, 0.427, 0.0)]),
    '6 CV': (6, 6, [(0, 0.665, 0.0), (5000, 0.374, 1457.0), (20000, 0.447, 0.0)]),
    '7 CV and more': (7, 0, [(0, 0.697, 0.0), (5000, 0.394, 1515.0), (20000, 0.470, 0.0)]),
}
FRANCE_ELECTRIC = {
    '3 CV or less': (0, 3, [(0, 0.635, 0.0), (5000, 0.379, 1278.0), (20000, 0.444, 0.0)]),
    '4 CV': (4, 4, [(0, 0.727, 0.0), (5000, 0.408, 1596.0), (20000, 0.488, 0.0)]),
    '5 CV': (5, 5, [(0, 0.763, 0.0), (5000, 0.428, 1674.0), (20000, 0.512, 0.0)]),
    '6 CV': (6, 6, [(0, 0.798, 0.0), (5000, 0.449, 1748.0), (20000, 0.536, 0.0)]),
    '7 CV and more': (7, 0, [(0, 0.836, 0.0), (5000, 0.473, 1818.0), (20000, 0.564, 0.0)]),
}
#: HMRC approved mileage rates, cars and vans (miles).
UNITED_KINGDOM = {'Car or van': (0, 0, [(0, 0.45, 0.0), (10000, 0.25, 0.0)])}


class ExpenseMileageVehicle(models.Model):
    """A category of vehicles with its own scale: "Car 4 CV (electric)", "Car or van", "Motorbike"..."""
    _name = 'expense.mileage.vehicle'
    _description = "Vehicle category of a mileage scale"
    _order = 'company_id, sequence, name'

    name = fields.Char(required=True, translate=True, help="e.g. Car 4 CV, Car 4 CV (electric), Motorbike.")
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    electric = fields.Boolean(string="Electric", help="For 100 % electric vehicles.")
    power_from = fields.Integer(
        string="Fiscal power from", help="The vehicles of the employees whose fiscal power is in this range "
        "take the category by themselves (the employee can still choose another one).")
    power_to = fields.Integer(string="to", help="0: no upper limit.")
    scale_ids = fields.One2many('expense.mileage.scale', 'vehicle_id', string="Scales")

    _unique_name = models.Constraint('unique(company_id, name)', "This category already exists in the company.")

    @api.model
    def _category_for(self, company, power, electric):
        """The category of a vehicle of this fiscal power and fuel: the closest range that holds the power."""
        candidates = self.sudo().search([('company_id', '=', company.id), ('electric', '=', bool(electric))],
                                        order='power_from desc, id')
        if not power:
            # No power entered: only the categories that do not depend on it (one scale for every vehicle).
            return candidates.filtered(lambda c: not c.power_from and not c.power_to)[:1]
        for category in candidates:
            if category.power_from <= (power or 0) and (not category.power_to or (power or 0) <= category.power_to):
                return category
        return self.browse()


class ExpenseMileageScale(models.Model):
    _name = 'expense.mileage.scale'
    _description = "Mileage scale"
    _order = 'company_id, vehicle_id, year desc'

    vehicle_id = fields.Many2one('expense.mileage.vehicle', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='vehicle_id.company_id', store=True, index=True)
    year = fields.Integer(
        required=True, default=lambda self: fields.Date.context_today(self).year,
        help="The scale applies from this year, until a more recent one.")
    method = fields.Selection(
        [('formula', "Yearly formula (distance x rate + fixed part of the bracket)"),
         ('marginal', "Each kilometre at the rate of its bracket")],
        string="Method", required=True, default='marginal',
        help="Yearly formula: the allowance of the year is the distance times the rate of the bracket of "
             "the yearly distance, plus its fixed part (French scale). Each kilometre at the rate of its "
             "bracket: the first kilometres at the first rate, the following ones at the next (United "
             "Kingdom).")
    line_ids = fields.One2many('expense.mileage.scale.line', 'scale_id', string="Brackets", copy=True)
    name = fields.Char(compute='_compute_name')

    _unique_year = models.Constraint('unique(vehicle_id, year)', "This vehicle already has a scale for this year.")

    @api.depends('vehicle_id.name', 'year')
    def _compute_name(self):
        for scale in self:
            scale.name = "%s %s" % (scale.vehicle_id.name or "", scale.year or "")

    def amount(self, distance):
        """Allowance of the year for this yearly distance (in the unit of the scale)."""
        self.ensure_one()
        distance = max(distance or 0.0, 0.0)
        lines = self.line_ids.sorted('distance_from')
        if not lines or not distance:
            return 0.0
        if self.method == 'formula':
            line = lines[0]
            for candidate in lines:
                if candidate.distance_from < distance:
                    line = candidate
            return distance * line.rate + line.fixed
        total = 0.0
        for index, line in enumerate(lines):
            upper = lines[index + 1].distance_from if index + 1 < len(lines) else float('inf')
            if distance > line.distance_from:
                total += (min(distance, upper) - line.distance_from) * line.rate
        return total

    @api.model
    def _scale_for(self, vehicle, year):
        """The scale in force for a vehicle and a year: the most recent one not after the year."""
        return self.sudo().search([('vehicle_id', '=', vehicle.id), ('year', '<=', year)], order='year desc', limit=1)

    def action_copy_next_year(self):
        """The same scale, a year later: the rates are then adjusted by hand."""
        copies = self.browse()
        for scale in self:
            target = scale.year + 1
            if self.sudo().search_count([('vehicle_id', '=', scale.vehicle_id.id), ('year', '=', target)]):
                raise ValidationError(_("%(vehicle)s already has a scale for %(year)s.",
                                        vehicle=scale.vehicle_id.name, year=target))
            copies |= scale.copy({'year': target})
        if len(copies) == 1:
            return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': copies.id,
                    'view_mode': 'form', 'target': 'current'}
        return True

    @api.model
    def _expense_scan_scale_init(self):
        """The scales to start with, for the companies that have no vehicle yet: those of their country."""
        year = fields.Date.context_today(self).year
        Vehicle = self.env['expense.mileage.vehicle'].sudo()
        for company in self.env['res.company'].search([]):
            if Vehicle.search_count([('company_id', '=', company.id)]):
                continue
            code = company.country_id.code
            sets = []
            if code == 'FR':
                sets = [(False, 'formula', FRANCE_THERMAL), (True, 'formula', FRANCE_ELECTRIC)]
            elif code == 'GB':
                sets = [(False, 'marginal', UNITED_KINGDOM)]
            sequence = 10
            for electric, method, vehicles in sets:
                for name, (power_from, power_to, brackets) in vehicles.items():
                    label = _("Car %s", name) if code == 'FR' else name
                    vehicle = Vehicle.create({
                        'name': label + (" " + _("(electric)") if electric else ""),
                        'company_id': company.id, 'sequence': sequence, 'electric': electric,
                        'power_from': power_from, 'power_to': power_to})
                    sequence += 10
                    self.sudo().create({
                        'vehicle_id': vehicle.id, 'year': year, 'method': method,
                        'line_ids': [(0, 0, {'distance_from': start, 'rate': rate, 'fixed': fixed})
                                     for start, rate, fixed in brackets]})


class ExpenseMileageScaleLine(models.Model):
    _name = 'expense.mileage.scale.line'
    _description = "Bracket of a mileage scale"
    _order = 'scale_id, distance_from'

    scale_id = fields.Many2one('expense.mileage.scale', required=True, ondelete='cascade', index=True)
    distance_from = fields.Float(
        string="From (distance)", digits=(16, 0),
        help="The bracket applies above this yearly distance, until the next bracket. 0 for the first.")
    rate = fields.Float(string="Rate per distance unit", digits=(16, 4), required=True)
    fixed = fields.Float(
        string="Fixed part", digits=(16, 2),
        help="Added to the allowance of the year in the yearly formula method; ignored by the other.")


class ResCompany(models.Model):
    _inherit = 'res.company'

    expense_mileage_vehicle_ids = fields.One2many('expense.mileage.vehicle', 'company_id', string="Vehicles")


class ExpenseEmployeeVehicle(models.Model):
    """A vehicle of an employee, used for the business trips they are paid for by the scale."""
    _name = 'expense.employee.vehicle'
    _description = "Personal vehicle of an employee"
    _order = 'employee_id, sequence, id'

    name = fields.Char(string="Vehicle", required=True, help="e.g. Peugeot 208.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    employee_id = fields.Many2one('hr.employee', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='employee_id.company_id', store=True, index=True)
    plate = fields.Char(string="Registration number")
    fiscal_power = fields.Integer(string="Fiscal power", help="Fiscal horsepower on the registration certificate.")
    electric = fields.Boolean(string="Electric", help="100 % electric vehicle: its own scale.")
    scale_vehicle_id = fields.Many2one(
        'expense.mileage.vehicle', string="Scale category", compute='_compute_scale_vehicle', store=True,
        readonly=False, domain="[('company_id', '=', company_id)]",
        help="Chosen from the fiscal power and the fuel; change it if the scale of your country has other "
             "categories.")
    registration_file = fields.Binary(string="Registration certificate", attachment=True)
    registration_filename = fields.Char()
    start_km = fields.Float(
        string="Distance already driven",
        help="Business distance of the year driven with this vehicle before the first expense of Odoo "
             "(the year below).")
    start_year = fields.Integer(string="Year of that distance")

    @api.depends('fiscal_power', 'electric', 'company_id')
    def _compute_scale_vehicle(self):
        for vehicle in self:
            category = self.env['expense.mileage.vehicle']._category_for(
                vehicle.company_id or self.env.company, vehicle.fiscal_power, vehicle.electric)
            if category or not vehicle.scale_vehicle_id:
                vehicle.scale_vehicle_id = category

    @api.depends('name', 'plate')
    def _compute_display_name(self):
        for vehicle in self:
            vehicle.display_name = "%s (%s)" % (vehicle.name, vehicle.plate) if vehicle.plate else vehicle.name

    @api.constrains('start_km')
    def _check_start_km(self):
        for vehicle in self:
            if vehicle.start_km < 0:
                raise ValidationError(_("The distance already driven cannot be negative."))


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    expense_vehicle_ids = fields.One2many(
        'expense.employee.vehicle', 'employee_id', string="Personal vehicles", groups="hr.group_hr_user",
        help="The vehicles of the employee, with their fiscal power and registration certificate. A mileage "
             "expense in one of them is paid by the scale of its category, from the distance cumulated "
             "over the year. A trip in a company vehicle keeps the mileage rate below, or the category cost.")
    expense_default_vehicle_id = fields.Many2one(
        'expense.employee.vehicle', string="Usual vehicle", compute='_compute_expense_default_vehicle',
        groups="hr.group_hr_user")

    def _compute_expense_default_vehicle(self):
        for employee in self:
            employee.expense_default_vehicle_id = employee.sudo().expense_vehicle_ids[:1]


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    # ------------------------------------------------------------------
    # Which expenses follow a scale
    # ------------------------------------------------------------------

    expense_company_vehicle = fields.Boolean(
        string="Company vehicle",
        help="The trip is made in a company vehicle: no allowance by the scale of the employee's own vehicle.")
    expense_vehicle_id = fields.Many2one(
        'expense.employee.vehicle', string="Vehicle", domain="[('employee_id', '=', employee_id)]",
        copy=True, help="The vehicle of the employee used for the trip: its scale pays the distance.")
    expense_has_vehicles = fields.Boolean(
        compute='_compute_expense_has_vehicles',
        help="The employee has a personal vehicle: the form then asks which one drove the trip.")

    @api.depends('employee_id')
    def _compute_expense_has_vehicles(self):
        for expense in self:
            expense.expense_has_vehicles = bool(expense.employee_id.sudo().expense_vehicle_ids)

    def _expense_scan_vehicle(self):
        """The personal vehicle of the trip: the one chosen, else the usual one, unless it is a company vehicle."""
        self.ensure_one()
        if self.expense_company_vehicle or not self._expense_scan_is_distance():
            return self.env['expense.employee.vehicle']
        return self.expense_vehicle_id.sudo() or self.employee_id.sudo().expense_default_vehicle_id

    def _expense_scan_scale_of(self):
        """The scale of the vehicle of this expense for its year, or an empty scale."""
        self.ensure_one()
        vehicle = self._expense_scan_vehicle()
        if not (vehicle.scale_vehicle_id and self.company_id):
            return self.env['expense.mileage.scale']
        year = (self.date or fields.Date.context_today(self)).year
        return self.env['expense.mileage.scale']._scale_for(vehicle.scale_vehicle_id, year)

    def _expense_scan_scale_distance_before(self):
        """Distance of the year driven with the vehicle before this expense: the starting distance and the
        earlier trips."""
        self.ensure_one()
        vehicle = self._expense_scan_vehicle()
        date = self.date or fields.Date.context_today(self)
        year = date.year
        before = vehicle.start_km if vehicle.start_year == year else 0.0
        others = self.sudo().search([
            ('employee_id', '=', self.employee_id.id), ('state', '!=', 'refused'),
            ('date', '>=', fields.Date.from_string('%s-01-01' % year)),
            ('date', '<=', fields.Date.from_string('%s-12-31' % year))])
        for other in others:
            if self.id and other.id == self.id:
                continue
            if other._expense_scan_vehicle() != vehicle:
                continue
            # The expenses are ordered by date, then by creation: a new one is the latest of its day.
            if (other.date, other.id) < (date, self.id or float('inf')):
                before += other.quantity
        return before

    def _expense_scan_scale_price_unit(self):
        """Price per unit that makes the amount of this trip the scale after it less the scale before it."""
        self.ensure_one()
        scale = self._expense_scan_scale_of()
        if not scale or not self.quantity or self.quantity <= 0:
            return None
        before = self._expense_scan_scale_distance_before()
        amount = scale.amount(before + self.quantity) - scale.amount(before)
        return amount / self.quantity

    def _expense_scan_apply_scale(self):
        """Sets the vehicle and the price of the draft expenses that follow a scale, then those of the later trips."""
        for expense in self.filtered(lambda e: e.state == 'draft'):
            usual = expense._expense_scan_vehicle()
            if usual and not expense.expense_vehicle_id and not expense.expense_company_vehicle:
                expense.expense_vehicle_id = usual
            price = expense._expense_scan_scale_price_unit()
            if price is not None and abs(price - expense.price_unit) > 1e-9:
                expense.price_unit = price
        # A trip changes the cumulated distance of the later ones of the year, whatever its own state.
        later = self.browse()
        for expense in self:
            later |= expense._expense_scan_later_scale_expenses()
        for other in later.sorted(lambda e: (e.date, e.id)):
            price = other._expense_scan_scale_price_unit()
            if price is not None and abs(price - other.price_unit) > 1e-9:
                other.price_unit = price

    def _expense_scan_later_scale_expenses(self):
        """The draft mileage expenses of the same vehicle in the same year, after this one."""
        self.ensure_one()
        vehicle = self._expense_scan_vehicle()
        if not vehicle or not self.date:
            return self.browse()
        later = self.sudo().search([
            ('employee_id', '=', self.employee_id.id), ('state', '=', 'draft'), ('id', '!=', self.id),
            ('date', '>=', self.date), ('date', '<=', fields.Date.from_string('%s-12-31' % self.date.year))])
        return later.filtered(lambda e: (e.date, e.id) > (self.date, self.id) and e._expense_scan_vehicle() == vehicle)

    # ------------------------------------------------------------------
    # The hooks
    # ------------------------------------------------------------------

    def _expense_scan_default_unit_price(self):
        self.ensure_one()
        if self._expense_scan_is_distance():
            price = self._expense_scan_scale_price_unit()
            if price is not None:
                return price
        return super()._expense_scan_default_unit_price()

    @api.onchange('employee_id', 'product_id')
    def _onchange_expense_scan_vehicle(self):
        for expense in self:
            if expense.state == 'draft' and expense.expense_scan_mileage and not expense.expense_company_vehicle:
                expense.expense_vehicle_id = expense.employee_id.sudo().expense_default_vehicle_id

    @api.onchange('quantity', 'date', 'expense_vehicle_id', 'expense_company_vehicle')
    def _onchange_expense_scan_scale(self):
        for expense in self:
            if expense.state == 'draft' and expense.product_id:
                price = expense._expense_scan_scale_price_unit()
                if price is None and expense.expense_company_vehicle and expense._expense_scan_is_distance():
                    # A company vehicle is paid at the single rate, not at a scale.
                    price = expense._expense_scan_default_unit_price()
                if price is not None:
                    expense.price_unit = price

    @api.model_create_multi
    def create(self, vals_list):
        expenses = super().create(vals_list)
        expenses._expense_scan_apply_scale()
        return expenses

    def write(self, vals):
        scale_keys = {'quantity', 'date', 'employee_id', 'product_id', 'state', 'expense_vehicle_id',
                      'expense_company_vehicle'} & set(vals)
        # The later trips of the vehicle the expense leaves (another vehicle, a company vehicle, another date).
        left = self.browse()
        if scale_keys:
            for expense in self:
                left |= expense._expense_scan_later_scale_expenses()
        result = super().write(vals)
        if scale_keys:
            if vals.get('expense_company_vehicle'):
                for expense in self.filtered(lambda e: e.state == 'draft' and e._expense_scan_is_distance()):
                    if expense.expense_vehicle_id:
                        expense.expense_vehicle_id = False
                    price = expense._expense_scan_default_unit_price()
                    if price is not None and abs(price - expense.price_unit) > 1e-9:
                        expense.price_unit = price
            self._expense_scan_apply_scale()
            (left.exists() - self)._expense_scan_apply_scale()
        return result

    def unlink(self):
        later = self.browse()
        for expense in self:
            later |= expense._expense_scan_later_scale_expenses()
        result = super().unlink()
        (later.exists() - self)._expense_scan_apply_scale()
        return result
