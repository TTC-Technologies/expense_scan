# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""What people correct after the scan.

The scan keeps the values it set (``expense_scan_read_values``). Once the
expense is submitted, comparing them with the values it has now gives, for
each field, how often the reading was right: the best measure of the
module's accuracy on a company's own receipts, with no reference to build.
"""
from odoo import api, fields, models, tools

#: Field of the report, key of the value kept by the scan, and the current
#: value in SQL, as text.
CHECKED = (
    ('date', 'date', "to_char(e.date, 'YYYY-MM-DD')"),
    ('total_amount_currency', 'total_amount_currency',
     "round(e.total_amount_currency::numeric, 2)::text"),
    ('product_id', 'product_id', "e.product_id::text"),
    ('currency_id', 'currency_id', "e.currency_id::text"),
    # Kept since 19.0.3.3.0: older scans have no merchant to compare.
    ('merchant', 'expense_scan_merchant', "e.expense_scan_merchant"),
)


class ExpenseScanCorrection(models.Model):
    _name = 'expense.scan.correction'
    _description = "Scan corrections"
    _auto = False
    _order = 'date desc, expense_id desc'
    _rec_name = 'field'
    # Pending writes of the expenses reach the database before the view is read.
    _depends = {
        'hr.expense': [
            'expense_scan_read_values', 'approval_state', 'date', 'total_amount_currency',
            'product_id', 'currency_id', 'employee_id', 'company_id', 'expense_scan_merchant',
        ],
    }

    expense_id = fields.Many2one('hr.expense', string="Expense", readonly=True)
    employee_id = fields.Many2one('hr.employee', string="Employee", readonly=True)
    company_id = fields.Many2one('res.company', string="Company", readonly=True)
    product_id = fields.Many2one('product.product', string="Category", readonly=True)
    date = fields.Date(string="Date", readonly=True)
    field = fields.Selection([
        ('date', "Date"),
        ('total_amount_currency', "Total"),
        ('product_id', "Category"),
        ('currency_id', "Currency"),
        ('merchant', "Merchant"),
    ], string="Field", readonly=True)
    outcome = fields.Selection([
        ('kept', "Kept"),
        ('corrected', "Corrected"),
        ('filled', "Filled in"),
    ], string="After the scan", readonly=True,
        help="Kept: the value read is the final one. Corrected: a person "
             "changed it. Filled in: nothing was read, a person entered it.")
    corrected_rate = fields.Float(
        string="Corrected (%)", readonly=True, aggregator='avg',
        help="Share of the readings changed by a person, filled-in fields included.")

    @api.model
    def _select_field(self, name, key, current):
        values = "e.expense_scan_read_values::jsonb"
        read = "(%s ->> '%s')" % (values, key)
        empty = "(%s IS NULL OR %s = 'false')" % (read, read)
        # The stored total is a float written by Python ("12.5"): compared
        # as a number.
        if name == 'total_amount_currency':
            same = "(%s)::numeric = round(e.total_amount_currency::numeric, 2)" % read
        else:
            same = "%s = %s" % (read, current)
        # Never compared when empty: "false" is no number.
        same = "(CASE WHEN %s THEN FALSE ELSE %s END)" % (empty, same)
        return """
            SELECT e.id * 8 + %(index)d AS id, e.id AS expense_id, e.employee_id,
                   e.company_id, e.product_id, e.date, '%(name)s' AS field,
                   CASE WHEN %(empty)s THEN 'filled' WHEN %(same)s THEN 'kept'
                        ELSE 'corrected' END AS outcome,
                   CASE WHEN %(empty)s OR NOT %(same)s THEN 100.0 ELSE 0.0 END AS corrected_rate
              FROM hr_expense e
             WHERE %(scanned)s AND %(values)s ? '%(key)s'
               AND NOT (%(empty)s AND %(current)s IS NULL)
        """ % {'index': [n for n, _k, _c in CHECKED].index(name), 'name': name, 'empty': empty,
               'same': same, 'current': current, 'scanned': self._scanned(), 'values': values,
               'key': key}

    @api.model
    def _scanned(self):
        # Submitted or later: a draft may still be corrected. Refused ones
        # say nothing about the reading.
        return ("e.expense_scan_read_values IS NOT NULL AND e.expense_scan_read_values LIKE '{%' "
                "AND e.approval_state IN ('submitted', 'approved')")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        parts = [self._select_field(name, key, current) for name, key, current in CHECKED]
        self.env.cr.execute("CREATE OR REPLACE VIEW %s AS (%s)" % (
            self._table, " UNION ALL ".join(parts)))
