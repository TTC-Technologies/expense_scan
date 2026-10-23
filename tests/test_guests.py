# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""A meal for two covers or more is a business meal, whose guests are named."""
from odoo.exceptions import UserError
from odoo.tests import common, tagged

from ..ocr import lexicon, parser
from .tax_setup import ensure_fiscal_country
from .test_parser import words_from_text


def reading(text):
    return parser.parse(words_from_text(text))


@tagged('post_install', '-at_install')
class TestGuests(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Product = cls.env['product.product']
        cls.company = cls.env.company
        ensure_fiscal_country(cls.env)
        cls.default = Product.create({'name': "Divers test", 'can_be_expensed': True})
        cls.company.expense_scan_product_id = cls.default
        cls.meal = Product.create({
            'name': "Repas test", 'can_be_expensed': True, 'sequence': 0,
            'expense_scan_keywords': "brasserie zorgal",
        })
        cls.invitation = Product.create({
            'name': "Invitations test", 'can_be_expensed': True, 'sequence': 0,
        })
        cls.employee = cls.env['hr.employee'].create({
            'name': "Gaston Convive", 'user_id': cls.env.user.id})

    def expense(self, **values):
        return self.env['hr.expense'].create(dict({
            'name': "Ticket", 'employee_id': self.employee.id,
            'product_id': self.default.id, 'total_amount_currency': 64.0,
        }, **values))

    def scan(self, expense, text):
        expense._expense_scan_apply(reading(text), self.env['ir.attachment'])
        return expense

    def test_business_meal_names(self):
        self.assertTrue(lexicon.is_invitation("Repas d'affaires"))
        self.assertTrue(lexicon.is_invitation("Bewirtung"))
        self.assertTrue(lexicon.is_invitation("Business meals"))
        self.assertTrue(lexicon.is_invitation("Gastos de representación"))
        self.assertTrue(lexicon.is_invitation("Repas client"))
        self.assertTrue(lexicon.is_invitation("Kundenbewirtung"))
        self.assertFalse(lexicon.is_invitation("Repas"))
        self.assertFalse(lexicon.is_invitation("Frais refacturés client"))
        # A business meal is no ordinary meal.
        self.assertIsNone(lexicon.family_of("Business meals"))

    def test_two_covers_make_a_business_meal(self):
        expense = self.scan(self.expense(), "BRASSERIE ZORGAL\nCouverts : 2\nTOTAL 64,00 EUR")
        self.assertEqual(expense.expense_scan_covers, 2)
        self.assertTrue(lexicon.is_invitation(expense.product_id.name), expense.product_id.name)
        self.assertIn("2", expense.expense_scan_category_reason)
        self.assertTrue(expense.expense_scan_hint_notes)
        self.assertIn('guests', expense.expense_scan_todo_codes)
        with self.assertRaises(UserError):
            expense.action_submit()
        # Guests named: the point is closed and the expense goes through.
        expense.description = "M. Dupont, Société Zorgal"
        self.assertFalse(expense.expense_scan_hint_notes)
        expense.action_submit()
        self.assertNotEqual(expense.state, 'draft')

    def test_one_cover_stays_a_meal(self):
        expense = self.scan(self.expense(), "BRASSERIE ZORGAL\nCouverts : 1\nTOTAL 18,00 EUR")
        self.assertTrue(expense._expense_scan_is_meal(expense.product_id))
        self.assertFalse(expense.expense_scan_hint_notes)

    def test_without_a_business_meal_category_it_is_only_suggested(self):
        self.env['product.product'].search([('can_be_expensed', '=', True)]).filtered(
            lambda product: lexicon.is_invitation(product.name)).product_tmpl_id.active = False
        expense = self.scan(self.expense(), "BRASSERIE ZORGAL\n3 couverts\nTOTAL 90,00 EUR")
        self.assertTrue(expense._expense_scan_is_meal(expense.product_id))
        self.assertIn("3", expense.expense_scan_hint_notes)
        # A meal: nothing blocks the submission.
        expense.action_submit()
        self.assertNotEqual(expense.state, 'draft')

    def test_notes_written_before_the_scan_ask_nothing(self):
        expense = self.scan(self.expense(description="Déjeuner avec Mme Martin (Zorgal SA)"),
                            "BRASSERIE ZORGAL\nCouverts : 2\nTOTAL 64,00 EUR")
        self.assertFalse(expense.expense_scan_hint_notes)
        self.assertNotIn('guests', expense.expense_scan_todo_codes or '')

    def test_category_set_back_to_a_meal_closes_the_point(self):
        expense = self.scan(self.expense(), "BRASSERIE ZORGAL\nCouverts : 2\nTOTAL 64,00 EUR")
        expense.product_id = self.meal
        self.assertFalse(expense.expense_scan_hint_notes)
        expense.action_submit()
        self.assertNotEqual(expense.state, 'draft')
