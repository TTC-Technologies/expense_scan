# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mileage scale by yearly distance, cumulated: the allowance of a year is the scale of its distance."""
from odoo.tests import Form, common, tagged

#: 4 CV, 2026 scale: thermal, then 100 % electric (rate to 5 000 km, rate and fixed part to 20 000 km, rate beyond).
THERMAL = (0.606, 0.340, 1330.0, 0.407)
ELECTRIC = (0.727, 0.408, 1596.0, 0.488)


def scale_of(figures, distance):
    r1, r2, fixed, r3 = figures
    if distance <= 5000:
        return distance * r1
    if distance <= 20000:
        return distance * r2 + fixed
    return distance * r3


@tagged('post_install', '-at_install')
class TestExpenseMileageScale(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mileage = cls.env.ref('hr_expense.expense_product_mileage')
        cls.mileage.standard_price = 1.0
        cls.company = cls.env.company
        # Scales of our own for a year no other test uses.
        Scale = cls.env['expense.mileage.scale']
        Scale.search([('company_id', '=', cls.company.id), ('year', '=', 2031)]).unlink()
        for electric, figures in ((False, THERMAL), (True, ELECTRIC)):
            Scale.create({'company_id': cls.company.id, 'year': 2031, 'hp': '4', 'electric': electric,
                          'rate_1': figures[0], 'rate_2': figures[1], 'fixed_2': figures[2], 'rate_3': figures[3]})
        cls.employee = cls.env['hr.employee'].create({
            'name': "Camille Barème", 'expense_vehicle_hp': '4', 'expense_vehicle_electric': True,
            'expense_mileage_rate': 0.5})

    def trip(self, km, date='2031-03-10', employee=None):
        return self.env['hr.expense'].create({
            'name': "Déplacement", 'employee_id': (employee or self.employee).id,
            'product_id': self.mileage.id, 'quantity': km, 'date': date})

    def test_the_default_scales_are_the_official_ones(self):
        """The scales of the year: thermal and electric, five powers each (4 CV electric: the figures of 2026)."""
        scale = self.env['expense.mileage.scale']._scale_for(self.company, 2026, '4', True)
        self.assertEqual((scale.rate_1, scale.rate_2, scale.fixed_2, scale.rate_3), ELECTRIC)
        thermal = self.env['expense.mileage.scale']._scale_for(self.company, 2026, '7', False)
        self.assertEqual((thermal.rate_1, thermal.fixed_2), (0.697, 1515.0))
        self.assertEqual(self.env['expense.mileage.scale'].search_count([
            ('company_id', '=', self.company.id), ('year', '=', 2026)]), 10)

    def test_the_scale_formula(self):
        scale = self.env['expense.mileage.scale']._scale_for(self.company, 2031, '4', True)
        self.assertAlmostEqual(scale.amount(1000), 727.0, places=2)
        self.assertAlmostEqual(scale.amount(6000), 6000 * 0.408 + 1596, places=2)
        self.assertAlmostEqual(scale.amount(25000), 25000 * 0.488, places=2)

    def test_a_first_trip_is_paid_at_the_first_rate(self):
        expense = self.trip(1000)
        self.assertAlmostEqual(expense.total_amount_currency, 1000 * 0.727, places=2)

    def test_the_trip_that_crosses_5000_km_carries_both_rates(self):
        first = self.trip(4000, '2031-03-10')
        second = self.trip(2000, '2031-03-20')
        # The scale of 6 000 km less that of 4 000.
        expected = scale_of(ELECTRIC, 6000) - scale_of(ELECTRIC, 4000)
        self.assertAlmostEqual(second.total_amount_currency, expected, places=2)
        # The allowance of the year is the scale of the cumulated distance, to the cent.
        total = first.total_amount_currency + second.total_amount_currency
        self.assertAlmostEqual(total, scale_of(ELECTRIC, 6000), delta=0.02)

    def test_beyond_5000_km_the_rate_is_the_second_one_without_the_fixed_part_again(self):
        self.trip(6000, '2031-02-01')
        third = self.trip(100, '2031-03-01')
        self.assertAlmostEqual(third.total_amount_currency, 100 * 0.408, places=2)

    def test_the_starting_kilometres_count(self):
        self.employee.write({'expense_mileage_start_km': 6000.0, 'expense_mileage_start_year': 2031})
        expense = self.trip(100)
        self.assertAlmostEqual(expense.total_amount_currency, 100 * 0.408, places=2)
        # Another year starts again from zero.
        other = self.trip(100, '2032-03-10')
        self.assertAlmostEqual(other.total_amount_currency, 100 * 0.727, places=2)

    def test_a_thermal_vehicle_has_the_thermal_scale(self):
        self.employee.expense_vehicle_electric = False
        self.assertAlmostEqual(self.trip(1000).total_amount_currency, 1000 * 0.606, places=2)
        self.assertAlmostEqual(self.trip(5000, '2031-04-01').total_amount_currency,
                               scale_of(THERMAL, 6000) - scale_of(THERMAL, 1000), places=2)

    def test_an_earlier_trip_changes_the_later_draft_ones(self):
        later = self.trip(1000, '2031-06-01')
        self.assertAlmostEqual(later.total_amount_currency, 1000 * 0.727, places=2)
        # A trip entered afterwards but dated before: the later one starts after its kilometres.
        first = self.trip(4500, '2031-05-01')
        expected = scale_of(ELECTRIC, 5500) - scale_of(ELECTRIC, 4500)
        self.assertAlmostEqual(later.total_amount_currency, expected, places=2)
        # Changing the distance of the first moves the second again.
        first.quantity = 100
        self.assertAlmostEqual(later.total_amount_currency, 1000 * 0.727, places=2)

    def test_a_deleted_expense_leaves_the_distance(self):
        first = self.trip(6000, '2031-02-01')
        second = self.trip(100, '2031-03-01')
        self.assertAlmostEqual(second.total_amount_currency, 100 * 0.408, places=2)
        first.unlink()
        self.assertAlmostEqual(second.total_amount_currency, 100 * 0.727, places=2)

    def test_the_form_follows_the_distance(self):
        self.trip(4900, '2031-03-01')
        with Form(self.env['hr.expense']) as form:
            form.employee_id = self.employee
            form.product_id = self.mileage
            form.date = '2031-03-15'
            form.quantity = 200
            expected = scale_of(ELECTRIC, 5100) - scale_of(ELECTRIC, 4900)
            self.assertAlmostEqual(form.total_amount_currency, expected, places=2)

    def test_without_a_vehicle_the_single_rate_stays(self):
        plain = self.env['hr.employee'].create({'name': "Sans véhicule", 'expense_mileage_rate': 0.5})
        self.assertAlmostEqual(self.trip(1000, employee=plain).price_unit, 0.5)

    def test_the_scale_of_the_latest_year_not_after_applies(self):
        scale = self.env['expense.mileage.scale']
        self.assertEqual(scale._scale_for(self.company, 2033, '4', True).year, 2031)
        self.assertFalse(scale._scale_for(self.company, 2030, '4', True).filtered(lambda s: s.year == 2031))
