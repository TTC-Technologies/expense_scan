# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mileage scale by yearly distance, applied cumulatively.

The official scale (French "barème kilométrique") gives the allowance of a whole year for a vehicle
from the distance of the year: ``d × rate_1`` up to 5 000 km, ``d × rate_2 + fixed`` from 5 001 to
20 000 km, ``d × rate_3`` beyond. The fixed part is not an extra to claim: it makes the second
formula meet the first one at 5 000 km.

An employee with a vehicle (fiscal horsepower) is paid by that scale: each mileage expense is worth
the scale of the year after the trip, less the scale before it. The thresholds and the fixed part
need no thought: the expense of the trip that crosses 5 000 km carries the rate of both sides, and
the cumulated allowance of the year is always the scale of the cumulated distance. An electric
vehicle has its own scale (the official one: the thermal scale increased by 20 % and rounded).

Employees without a vehicle keep the single rate of their record, or the cost of the category.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

#: Thresholds of the yearly distance of the scale.
THRESHOLD_1 = 5000
THRESHOLD_2 = 20000

#: Scales of a car, 2026 (fiscal horsepower: rate up to 5 000 km, rate and fixed part from 5 001 to
#: 20 000 km, rate beyond 20 000 km): thermal, hydrogen and hybrid vehicles, then 100 % electric ones.
#: The values to start with: check them against the scale in force.
DEFAULT_CAR_SCALE = {
    False: {
        '3': (0.529, 0.316, 1065.0, 0.370),
        '4': (0.606, 0.340, 1330.0, 0.407),
        '5': (0.636, 0.357, 1395.0, 0.427),
        '6': (0.665, 0.374, 1457.0, 0.447),
        '7': (0.697, 0.394, 1515.0, 0.470),
    },
    True: {
        '3': (0.635, 0.379, 1278.0, 0.444),
        '4': (0.727, 0.408, 1596.0, 0.488),
        '5': (0.763, 0.428, 1674.0, 0.512),
        '6': (0.798, 0.449, 1748.0, 0.536),
        '7': (0.836, 0.473, 1818.0, 0.564),
    },
}


class ExpenseMileageScale(models.Model):
    _name = 'expense.mileage.scale'
    _description = "Mileage scale"
    _order = 'company_id, year desc, hp'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    year = fields.Integer(
        required=True, default=lambda self: fields.Date.context_today(self).year,
        help="The scale applies from this year, until a more recent one.")
    hp = fields.Selection(
        [('3', "3 CV or less"), ('4', "4 CV"), ('5', "5 CV"), ('6', "6 CV"), ('7', "7 CV and more")],
        string="Fiscal horsepower", required=True)
    electric = fields.Boolean(string="Electric vehicle", help="The scale of 100 % electric vehicles.")
    rate_1 = fields.Float(string="Up to 5 000 km (per km)", digits=(16, 4), required=True)
    rate_2 = fields.Float(string="5 001 to 20 000 km (per km)", digits=(16, 4), required=True)
    fixed_2 = fields.Float(string="5 001 to 20 000 km (fixed part)", digits=(16, 2), required=True)
    rate_3 = fields.Float(string="Beyond 20 000 km (per km)", digits=(16, 4), required=True)

    _unique_year_hp = models.Constraint(
        'unique(company_id, year, hp, electric)',
        "A scale already exists for this company, this year, this power and this kind of vehicle.")

    def amount(self, distance):
        """Allowance of a year for this distance, before the increase of an electric vehicle."""
        self.ensure_one()
        distance = max(distance or 0.0, 0.0)
        if distance <= THRESHOLD_1:
            return distance * self.rate_1
        if distance <= THRESHOLD_2:
            return distance * self.rate_2 + self.fixed_2
        return distance * self.rate_3

    @api.model
    def _scale_for(self, company, year, hp, electric=False):
        """The scale in force for a company, a year, a power and a kind of vehicle: the most recent one
        not after the year."""
        return self.sudo().search([
            ('company_id', '=', company.id), ('hp', '=', hp), ('electric', '=', bool(electric)),
            ('year', '<=', year)], order='year desc', limit=1)

    @api.model
    def _expense_scan_scale_init(self):
        """The scales of cars of the companies that have none, thermal and electric (install and update)."""
        year = fields.Date.context_today(self).year
        for company in self.env['res.company'].search([]):
            for electric, scale in DEFAULT_CAR_SCALE.items():
                if self.sudo().search_count([('company_id', '=', company.id), ('electric', '=', electric)]):
                    continue
                self.sudo().create([
                    {'company_id': company.id, 'year': year, 'hp': hp, 'electric': electric, 'rate_1': r1,
                     'rate_2': r2, 'fixed_2': fixed, 'rate_3': r3}
                    for hp, (r1, r2, fixed, r3) in scale.items()])


class ResCompany(models.Model):
    _inherit = 'res.company'

    expense_mileage_scale_ids = fields.One2many('expense.mileage.scale', 'company_id', string="Mileage scale")


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    expense_vehicle_hp = fields.Selection(
        [('3', "3 CV or less"), ('4', "4 CV"), ('5', "5 CV"), ('6', "6 CV"), ('7', "7 CV and more")],
        string="Vehicle (fiscal horsepower)", groups="hr.group_hr_user",
        help="Pays the mileage expenses by the scale of the year, from the cumulated distance (the fixed "
             "part and the thresholds are handled). Empty: the mileage rate below, or the category cost.")
    expense_vehicle_electric = fields.Boolean(
        string="Electric vehicle", groups="hr.group_hr_user",
        help="Pays by the scale of 100 % electric vehicles.")
    expense_mileage_start_km = fields.Float(
        string="Kilometres already driven", groups="hr.group_hr_user",
        help="Business distance of the year driven before the first expense of Odoo (the year below).")
    expense_mileage_start_year = fields.Integer(
        string="Year of those kilometres", groups="hr.group_hr_user",
        help="The kilometres already driven count for this year only.")

    @api.constrains('expense_mileage_start_km')
    def _check_expense_mileage_start_km(self):
        for employee in self.sudo():
            if employee.expense_mileage_start_km < 0:
                raise ValidationError(_("The kilometres already driven cannot be negative."))


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    # ------------------------------------------------------------------
    # Which expenses follow a scale
    # ------------------------------------------------------------------

    def _expense_scan_scale_of(self):
        """The scale and the vehicle of this expense, or an empty scale."""
        self.ensure_one()
        employee = self.employee_id.sudo()
        if not (employee.expense_vehicle_hp and self.company_id and self._expense_scan_is_distance()):
            return self.env['expense.mileage.scale']
        year = (self.date or fields.Date.context_today(self)).year
        return self.env['expense.mileage.scale']._scale_for(
            self.company_id, year, employee.expense_vehicle_hp, employee.expense_vehicle_electric)

    def _expense_scan_scale_distance_before(self):
        """Distance of the year driven before this expense: the starting kilometres and the earlier trips."""
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
            if not other.id or (self.id and other.id == self.id):
                continue
            if not other._expense_scan_is_distance():
                continue
            # The expenses are ordered by date, then by creation: a new one is the latest of its day.
            if (other.date, other.id) < (date, self.id or float('inf')):
                before += other.quantity
        return before

    def _expense_scan_scale_price_unit(self):
        """Price per km that makes the amount of this trip the scale after it less the scale before it."""
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
        if not self.employee_id.sudo().expense_vehicle_hp or not self.date:
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
