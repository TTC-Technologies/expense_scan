# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""The report of what people correct after the scan."""
import json
from datetime import date

from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestCorrections(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Product = cls.env['product.product']
        cls.meal = Product.create({'name': "Repas corrigé Zz", 'can_be_expensed': True})
        cls.taxi = Product.create({'name': "Taxi corrigé Zz", 'can_be_expensed': True})
        cls.employee = cls.env['hr.employee'].create({
            'name': "Corinne Correction", 'user_id': cls.env.user.id})

    def scanned(self, read, **values):
        expense = self.env['hr.expense'].create(dict({
            'name': "Ticket", 'employee_id': self.employee.id, 'product_id': self.meal.id,
            'date': date(2026, 9, 3), 'total_amount_currency': 18.5,
            'expense_scan_read_values': json.dumps(read),
        }, **values))
        return expense

    def outcomes(self, expense):
        Report = self.env['expense.scan.correction']
        Report.invalidate_model()
        return {row.field: row.outcome for row in Report.search([('expense_id', '=', expense.id)])}

    def test_kept_corrected_and_filled_in(self):
        currency = self.env.company.currency_id.id
        expense = self.scanned({'date': '2026-09-03', 'total_amount_currency': 18.5,
                                'product_id': self.taxi.id, 'currency_id': currency,
                                'expense_scan_merchant': "Chez Zorg"},
                               expense_scan_merchant="Chez Zorg")
        self.assertEqual(self.outcomes(expense), {}, "a draft may still change: not counted")
        expense.action_submit()
        self.assertEqual(self.outcomes(expense), {
            'date': 'kept', 'total_amount_currency': 'kept', 'product_id': 'corrected',
            'currency_id': 'kept', 'merchant': 'kept'})
        unread = self.scanned({'date': False, 'total_amount_currency': 18.5,
                               'product_id': self.meal.id, 'currency_id': currency})
        unread.action_submit()
        self.assertEqual(self.outcomes(unread)['date'], 'filled')
        self.assertEqual(self.outcomes(unread)['product_id'], 'kept')
        self.assertNotIn('merchant', self.outcomes(unread), "scanned before the merchant was kept")
        renamed = self.scanned({'date': '2026-09-03', 'total_amount_currency': 18.5,
                                'product_id': self.meal.id, 'currency_id': currency,
                                'expense_scan_merchant': "CHEZ Z0RG SARL"},
                               expense_scan_merchant="Chez Zorg")
        renamed.action_submit()
        self.assertEqual(self.outcomes(renamed)['merchant'], 'corrected')

    def test_a_merchant_whose_receipts_are_corrected_is_watched(self):
        """The next receipt of a merchant corrected most of the time asks for a look at the field."""
        from datetime import timedelta
        from unittest.mock import patch
        from odoo import fields
        from ..ocr import parser
        from .test_parser import words_from_text

        currency = self.env.company.currency_id.id
        today = fields.Date.context_today(self.env['hr.expense'])

        def read(total, merchant="Chez Zorg"):
            return {'date': fields.Date.to_string(today), 'total_amount_currency': total, 'product_id': self.meal.id,
                    'currency_id': currency, 'expense_scan_merchant': merchant}

        def scan(merchant="Chez Zorg"):
            expense = self.env['hr.expense'].create({'name': "Nouveau", 'employee_id': self.employee.id})
            text = "\n".join([merchant.upper(), (today - timedelta(days=1)).strftime("%d/%m/%Y") + " 12:30",
                              "TOTAL 18,50"])
            with patch.object(type(expense), '_expense_scan_store_image', autospec=True, return_value={}):
                expense.with_context(lang='en_US')._expense_scan_apply(
                    parser.parse(words_from_text(text)), self.env['ir.attachment'])
            return expense

        # Two corrections out of two readings: too few to watch.
        for total in (20.0, 21.0):
            self.scanned(read(18.5), date=today, total_amount_currency=total,
                         expense_scan_merchant="Chez Zorg").action_submit()
        self.assertNotIn('total', (scan().expense_scan_todo_codes or ''))
        # A third: the amount is watched, the date (always right) is not.
        self.scanned(read(18.5), date=today, total_amount_currency=22.0, expense_scan_merchant="Chez Zorg").action_submit()
        self.env['expense.scan.correction'].invalidate_model()
        watched = scan()
        self.assertIn('total', (watched.expense_scan_todo_codes or '').split(','))
        self.assertNotIn('date', (watched.expense_scan_todo_codes or '').split(','))
        self.assertIn("often wrong", watched.expense_scan_hints)
        # Another merchant is not concerned.
        self.assertNotIn('total', (scan("Cafe Zinc").expense_scan_todo_codes or '').split(','))

    def test_the_rate_per_field(self):
        currency = self.env.company.currency_id.id
        for total in (18.5, 18.5, 18.5, 81.5):
            self.scanned({'date': '2026-09-03', 'total_amount_currency': total,
                          'product_id': self.meal.id, 'currency_id': currency}).action_submit()
        groups = self.env['expense.scan.correction']._read_group(
            [('employee_id', '=', self.employee.id)], ['field'], ['corrected_rate:avg'])
        rates = dict(groups)
        self.assertEqual(rates['total_amount_currency'], 25.0)
        self.assertEqual(rates['date'], 0.0)
