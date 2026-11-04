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

An employee with a vehicle is paid by its scale: each mileage expense is worth the scale of the year
after the trip, less the scale before it. The thresholds and the fixed part need no thought: the trip
that crosses a threshold carries the rates of both sides, and the cumulated allowance of the year is
always the scale of the cumulated distance. Employees without a vehicle keep the single rate of
their record, or the cost of the category.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

#: Scales to start with, by country of the company: {vehicle: [(distance from, rate, fixed part)]}.
#: Rates change every year: they are to check against the scale in force.
FRANCE_THERMAL = {
    '3 CV or less': [(0, 0.529, 0.0), (5000, 0.316, 1065.0), (20000, 0.370, 0.0)],
    '4 CV': [(0, 0.606, 0.0), (5000, 0.340, 1330.0), (20000, 0.407, 0.0)],
    '5 CV': [(0, 0.636, 0.0), (5000, 0.357, 1395.0), (20000, 0.427, 0.0)],
    '6 CV': [(0, 0.665, 0.0), (5000, 0.374, 1457.0), (20000, 0.447, 0.0)],
    '7 CV and more': [(0, 0.697, 0.0), (5000, 0.394, 1515.0), (20000, 0.470, 0.0)],
}
FRANCE_ELECTRIC = {
    '3 CV or less': [(0, 0.635, 0.0), (5000, 0.379, 1278.0), (20000, 0.444, 0.0)],
    '4 CV': [(0, 0.727, 0.0), (5000, 0.408, 1596.0), (20000, 0.488, 0.0)],
    '5 CV': [(0, 0.763, 0.0), (5000, 0.428, 1674.0), (20000, 0.512, 0.0)],
    '6 CV': [(0, 0.798, 0.0), (5000, 0.449, 1748.0), (20000, 0.536, 0.0)],
    '7 CV and more': [(0, 0.836, 0.0), (5000, 0.473, 1818.0), (20000, 0.564, 0.0)],
}
#: HMRC approved mileage rates, cars and vans (miles).
UNITED_KINGDOM = {'Car or van': [(0, 0.45, 0.0), (10000, 0.25, 0.0)]}


class ExpenseMileageVehicle(models.Model):
    _name = 'expense.mileage.vehicle'
    _description = "Vehicle of a mileage scale"
    _order = 'company_id, sequence, name'

    name = fields.Char(required=True, translate=True, help="e.g. Car 4 CV, Car 4 CV (electric), Motorbike.")
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    scale_ids = fields.One2many('expense.mileage.scale', 'vehicle_id', string="Scales")

    _unique_name = models.Constraint('unique(company_id, name)', "This vehicle already exists in the company.")


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
                for name, brackets in vehicles.items():
                    label = _("Car %s", name) if code == 'FR' else name
                    vehicle = Vehicle.create({
                        'name': label + (" " + _("(electric)") if electric else ""),
                        'company_id': company.id, 'sequence': sequence})
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


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    expense_vehicle_id = fields.Many2one(
        'expense.mileage.vehicle', string="Vehicle (mileage scale)", groups="hr.group_hr_user",
        domain="[('company_id', '=', company_id)]",
        help="Pays the mileage expenses by the scale of the year of this vehicle, from the cumulated distance "
             "(the thresholds and the fixed part are handled). Empty: the mileage rate below, or the category cost.")
    expense_mileage_start_km = fields.Float(
        string="Distance already driven", groups="hr.group_hr_user",
        help="Business distance of the year driven before the first expense of Odoo (the year below).")
    expense_mileage_start_year = fields.Integer(
        string="Year of that distance", groups="hr.group_hr_user",
        help="The distance already driven counts for this year only.")

    @api.constrains('expense_mileage_start_km')
    def _check_expense_mileage_start_km(self):
        for employee in self.sudo():
            if employee.expense_mileage_start_km < 0:
                raise ValidationError(_("The distance already driven cannot be negative."))


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    # ------------------------------------------------------------------
    # Which expenses follow a scale
    # ------------------------------------------------------------------

    def _expense_scan_scale_of(self):
        """The scale of the vehicle of this expense for its year, or an empty scale."""
        self.ensure_one()
        employee = self.employee_id.sudo()
        if not (employee.expense_vehicle_id and self.company_id and self._expense_scan_is_distance()):
            return self.env['expense.mileage.scale']
        year = (self.date or fields.Date.context_today(self)).year
        return self.env['expense.mileage.scale']._scale_for(employee.expense_vehicle_id, year)

    def _expense_scan_scale_distance_before(self):
        """Distance of the year driven before this expense: the starting distance and the earlier trips."""
        self.ensure_one()
        employee = self.employee_id.sudo()
        date = self.date or fields.Date.context_today(self)
        year = date.year
        before = employee.expense_mileage_start_km if employee.expense_mileage_start_year == year else 0.0
        others = self.sudo().search([
            ('employee_id', '=', employee.id), ('state', '!=', 'refused'),
            ('date', '>=', fields.Date.from_string('%s-01-01' % year)),
            ('date', '<=', fields.Date.from_string('%s-12-31' % year))])
        for other in others:
            if self.id and other.id == self.id:
                continue
            if not other._expense_scan_is_distance():
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
        """Sets the price of the draft expenses that follow a scale, then those of the later trips."""
        for expense in self.filtered(lambda e: e.state == 'draft'):
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
        """The draft mileage expenses of the same employee and year, after this one."""
        self.ensure_one()
        if not self.employee_id.sudo().expense_vehicle_id or not self.date:
            return self.browse()
        later = self.sudo().search([
            ('employee_id', '=', self.employee_id.id), ('state', '=', 'draft'), ('id', '!=', self.id),
            ('date', '>=', self.date), ('date', '<=', fields.Date.from_string('%s-12-31' % self.date.year))])
        return later.filtered(lambda e: (e.date, e.id) > (self.date, self.id) and e._expense_scan_is_distance())

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

    @api.onchange('quantity', 'date')
    def _onchange_expense_scan_scale(self):
        for expense in self:
            if expense.state == 'draft' and expense.product_id:
                price = expense._expense_scan_scale_price_unit()
                if price is not None:
                    expense.price_unit = price

    @api.model_create_multi
    def create(self, vals_list):
        expenses = super().create(vals_list)
        expenses._expense_scan_apply_scale()
        return expenses

    def write(self, vals):
        result = super().write(vals)
        if {'quantity', 'date', 'employee_id', 'product_id', 'state'} & set(vals):
            self._expense_scan_apply_scale()
        return result

    def unlink(self):
        later = self.browse()
        for expense in self:
            later |= expense._expense_scan_later_scale_expenses()
        result = super().unlink()
        (later.exists() - self)._expense_scan_apply_scale()
        return result
