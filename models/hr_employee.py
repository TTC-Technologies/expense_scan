# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mileage rate per employee, and their home and office among the places.

Official mileage scales often depend on the vehicle and the yearly distance:
a single rate on the "Mileage" category does not fit everyone.
"""
from odoo import api, fields, models

#: Fields whose change moves the employee's home or office.
ADDRESS_FIELDS = {'private_street', 'private_street2', 'private_zip', 'private_city',
                  'private_country_id', 'private_state_id', 'address_id', 'company_id'}


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # `groups` is mandatory: Odoo requires it on every field missing from
    # the public employee profile. Without it, the field is prefetched for
    # every user and reading an employee as a non-HR user fails (opening an
    # expense is enough).
    #
    # No storage precision: "Product Price" has two decimals on a standard
    # database and would round 0.636 to 0.64 on save. `min_display_digits`
    # keeps the full number and shows at least three decimals.
    expense_mileage_rate = fields.Float(
        string="Mileage rate",
        min_display_digits=3,
        groups="hr.group_hr_user",
        help="Price per kilometre applied to this employee's distance "
             "expenses. When empty, the category cost applies. No decimal is "
             "lost: official scales often have three, an in-house rate may "
             "have more.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        employees = super().create(vals_list)
        employees.env['expense.scan.place']._expense_scan_sync_employees(employees)
        return employees

    def write(self, vals):
        result = super().write(vals)
        if ADDRESS_FIELDS & set(vals):
            self.env['expense.scan.place']._expense_scan_sync_employees(self)
        return result
