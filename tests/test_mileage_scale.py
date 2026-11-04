# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mileage scale by yearly distance, cumulated: the allowance of a year is the scale of its distance."""
from odoo.tests import Form, common, tagged


@tagged('post_install', '-at_install')
class TestExpenseMileageScale(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mileage = cls.env.ref('hr_expense.expense_product_mileage')
        cls.mileage.standard_price = 1.0
        cls.company = cls.env.company
        # A scale of our own for a year no other test uses: 4 CV, the figures of the French scale.
        cls.env['expense.mileage.scale'].search([('company_id', '=', cls.company.id), ('year', '=', 2031)]).unlink()
        cls.env['expense.mileage.scale'].create({
            'company_id': cls.company.id, 'year': 2031, 'hp': '4', 'rate_1': 0.606, 'rate_2': 0.340,
            'fixed_2': 1330.0, 'rate_3': 0.407})
        cls.employee = cls.env['hr.employee'].create({
            'name': "Camille Barème", 'expense_vehicle_hp': '4', 'expense_vehicle_electric': True,
            'expense_mileage_rate': 0.5})

    def trip(self, km, date='2031-03-10', employee=None):
        return self.env['hr.expense'].create({
            'name': "Déplacement", 'employee_id': (employee or self.employee).id,
            'product_id': self.mileage.id, 'quantity': km, 'date': date})

    def test_the_scale_meets_itself_at_5000_km(self):
        scale = self.env['expense.mileage.scale']._scale_for(self.company, 2031, '4')
        self.assertAlmostEqual(scale.amount(5000), 5000 * 0.606, places=2)
        self.assertAlmostEqual(scale.amount(5000), 5000 * 0.340 + 1330, places=2)
        self.assertAlmostEqual(scale.amount(25000), 25000 * 0.407, places=2)

    def test_a_first_trip_is_paid_at_the_first_rate_with_the_electric_increase(self):
        expense = self.trip(1000)
        self.assertAlmostEqual(expense.total_amount_currency, 1000 * 0.606 * 1.2, places=2)

    def test_the_trip_that_crosses_5000_km_carries_both_rates(self):
        first = self.trip(4000, '2031-03-10')
        second = self.trip(2000, '2031-03-20')
        # 1 000 km at the first rate, 1 000 at the second (fixed part included): the scale of 6 000 km less that of 4 000.
        expected = ((6000 * 0.340 + 1330) - 4000 * 0.606) * 1.2
        self.assertAlmostEqual(second.total_amount_currency, expected, places=2)
        # The allowance of the year is the scale of the cumulated distance, to the cent.
        total = first.total_amount_currency + second.total_amount_currency
        self.assertAlmostEqual(total, (6000 * 0.340 + 1330) * 1.2, delta=0.02)

    def test_beyond_5000_km_the_rate_is_the_second_one_without_the_fixed_part_again(self):
        self.trip(5000, '2031-02-01')
        third = self.trip(100, '2031-03-01')
        self.assertAlmostEqual(third.total_amount_currency, 100 * 0.340 * 1.2, places=2)

    def test_the_starting_kilometres_count(self):
        self.employee.write({'expense_mileage_start_km': 6000.0, 'expense_mileage_start_year': 2031})
        expense = self.trip(100)
        self.assertAlmostEqual(expense.total_amount_currency, 100 * 0.340 * 1.2, places=2)
        # Another year starts again from zero.
        other = self.trip(100, '2032-03-10')
        self.assertAlmostEqual(other.total_amount_currency, 100 * 0.606 * 1.2, places=2)

    def test_a_combustion_vehicle_has_no_increase(self):
        self.employee.expense_vehicle_electric = False
        self.assertAlmostEqual(self.trip(1000).total_amount_currency, 1000 * 0.606, places=2)

    def test_an_earlier_trip_changes_the_later_draft_ones(self):
        later = self.trip(1000, '2031-06-01')
        self.assertAlmostEqual(later.total_amount_currency, 1000 * 0.606 * 1.2, places=2)
        # A trip entered afterwards but dated before: the later one starts after its kilometres.
        self.trip(4500, '2031-05-01')
        expected = ((5500 * 0.340 + 1330) - 4500 * 0.606) * 1.2
        self.assertAlmostEqual(later.total_amount_currency, expected, places=2)
        # Changing the distance of the first moves the second again.
        first = self.env['hr.expense'].search([('employee_id', '=', self.employee.id), ('date', '=', '2031-05-01')])
        first.quantity = 100
        self.assertAlmostEqual(later.total_amount_currency, 1000 * 0.606 * 1.2, places=2)

    def test_a_refused_expense_leaves_the_distance(self):
        first = self.trip(5000, '2031-02-01')
        second = self.trip(100, '2031-03-01')
        self.assertAlmostEqual(second.total_amount_currency, 100 * 0.340 * 1.2, places=2)
        first.unlink()
        self.assertAlmostEqual(second.total_amount_currency, 100 * 0.606 * 1.2, places=2)

    def test_the_form_follows_the_distance(self):
        self.trip(4900, '2031-03-01')
        with Form(self.env['hr.expense']) as form:
            form.employee_id = self.employee
            form.product_id = self.mileage
            form.date = '2031-03-15'
            form.quantity = 200
            expected = ((5100 * 0.340 + 1330) - 4900 * 0.606) * 1.2
            self.assertAlmostEqual(form.total_amount_currency, expected, places=2)

    def test_without_a_vehicle_the_single_rate_stays(self):
        plain = self.env['hr.employee'].create({'name': "Sans véhicule", 'expense_mileage_rate': 0.5})
        self.assertAlmostEqual(self.trip(1000, employee=plain).price_unit, 0.5)

    def test_the_scale_of_the_latest_year_not_after_applies(self):
        scale = self.env['expense.mileage.scale']
        self.assertEqual(scale._scale_for(self.company, 2033, '4').year, 2031)
        self.assertFalse(scale._scale_for(self.company, 2030, '4').filtered(lambda s: s.year == 2031))
