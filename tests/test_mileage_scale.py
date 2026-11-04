# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mileage scales by yearly distance, cumulated: the allowance of a year is the scale of its distance."""
from odoo.exceptions import ValidationError
from odoo.tests import Form, common, tagged

#: 4 CV, 2026 French scale: (distance from, rate, fixed part) thermal, then 100 % electric.
THERMAL = [(0, 0.606, 0.0), (5000, 0.340, 1330.0), (20000, 0.407, 0.0)]
ELECTRIC = [(0, 0.727, 0.0), (5000, 0.408, 1596.0), (20000, 0.488, 0.0)]
#: HMRC: 45 p up to 10 000 miles, 25 p beyond.
BRITISH = [(0, 0.45, 0.0), (10000, 0.25, 0.0)]


def formula(brackets, distance):
    line = brackets[0]
    for candidate in brackets:
        if candidate[0] < distance:
            line = candidate
    return distance * line[1] + line[2]


@tagged('post_install', '-at_install')
class TestExpenseMileageScale(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mileage = cls.env.ref('hr_expense.expense_product_mileage')
        cls.mileage.standard_price = 1.0
        cls.company = cls.env.company
        Vehicle = cls.env['expense.mileage.vehicle']
        cls.thermal = Vehicle.create({'name': "Test 4 CV", 'company_id': cls.company.id,
                                      'power_from': 4, 'power_to': 4})
        cls.electric = Vehicle.create({'name': "Test 4 CV (electric)", 'company_id': cls.company.id,
                                       'electric': True, 'power_from': 4, 'power_to': 4})
        cls.make_scale(cls.thermal, 2031, 'formula', THERMAL)
        cls.make_scale(cls.electric, 2031, 'formula', ELECTRIC)
        cls.employee = cls.env['hr.employee'].create({'name': "Camille Barème", 'expense_mileage_rate': 0.5})
        cls.zoe = cls.make_vehicle(cls.employee, "Zoe", cls.electric, 4, True)

    @classmethod
    def make_vehicle(cls, employee, name, category, power, electric=False, **values):
        return cls.env['expense.employee.vehicle'].create(dict({
            'name': name, 'employee_id': employee.id, 'fiscal_power': power, 'electric': electric,
            'scale_vehicle_id': category.id}, **values))

    @classmethod
    def make_scale(cls, vehicle, year, method, brackets):
        return cls.env['expense.mileage.scale'].create({
            'vehicle_id': vehicle.id, 'year': year, 'method': method,
            'line_ids': [(0, 0, {'distance_from': start, 'rate': rate, 'fixed': fixed})
                         for start, rate, fixed in brackets]})

    def trip(self, km, date='2031-03-10', employee=None, **values):
        return self.env['hr.expense'].create(dict({
            'name': "Déplacement", 'employee_id': (employee or self.employee).id,
            'product_id': self.mileage.id, 'quantity': km, 'date': date}, **values))

    # -- the scales ---------------------------------------------------------------------------

    def test_the_formula_scale(self):
        scale = self.env['expense.mileage.scale']._scale_for(self.electric, 2031)
        self.assertAlmostEqual(scale.amount(1000), 727.0, places=2)
        self.assertAlmostEqual(scale.amount(5000), 5000 * 0.727, places=2)
        self.assertAlmostEqual(scale.amount(6000), 6000 * 0.408 + 1596, places=2)
        self.assertAlmostEqual(scale.amount(25000), 25000 * 0.488, places=2)

    def test_the_marginal_scale_of_another_country(self):
        pound = self.env['expense.mileage.vehicle'].create({'name': "Test car or van", 'company_id': self.company.id})
        scale = self.make_scale(pound, 2031, 'marginal', BRITISH)
        self.assertAlmostEqual(scale.amount(8000), 8000 * 0.45, places=2)
        self.assertAlmostEqual(scale.amount(12000), 10000 * 0.45 + 2000 * 0.25, places=2)
        # A trip across the threshold is paid at both rates.
        worker = self.env['hr.employee'].create({'name': "Alex UK"})
        self.make_vehicle(worker, "Focus", pound, 0)
        self.trip(9000, '2031-02-01', worker)
        second = self.trip(2000, '2031-03-01', worker)
        self.assertAlmostEqual(second.total_amount_currency, 1000 * 0.45 + 1000 * 0.25, places=2)

    def test_the_scale_of_the_latest_year_not_after_applies(self):
        scale = self.env['expense.mileage.scale']
        self.assertEqual(scale._scale_for(self.electric, 2033).year, 2031)
        self.assertFalse(scale._scale_for(self.electric, 2030))

    def test_a_scale_is_copied_to_next_year_then_adjusted(self):
        scale = self.env['expense.mileage.scale']._scale_for(self.electric, 2031)
        scale.action_copy_next_year()
        copy = self.env['expense.mileage.scale']._scale_for(self.electric, 2032)
        self.assertEqual(copy.year, 2032)
        self.assertEqual(len(copy.line_ids), 3)
        copy.line_ids.filtered(lambda line: line.distance_from == 0).rate = 0.75
        self.assertAlmostEqual(self.trip(100, '2032-03-01').total_amount_currency, 75.0, places=2)
        # The year before keeps its rates.
        self.assertAlmostEqual(self.trip(100, '2031-03-01').total_amount_currency, 72.7, places=2)
        with self.assertRaises(ValidationError):
            scale.action_copy_next_year()

    def test_the_scales_to_start_with_follow_the_country(self):
        france = self.env['res.company'].create({
            'name': "Société FR test", 'country_id': self.env.ref('base.fr').id})
        self.env['expense.mileage.scale']._expense_scan_scale_init()
        vehicles = france.expense_mileage_vehicle_ids
        self.assertEqual(len(vehicles), 10)
        electric = self.env['expense.mileage.vehicle']._category_for(france, 4, True)
        self.assertIn('4 CV', electric.name)
        self.assertTrue(electric.electric)
        self.assertEqual(self.env['expense.mileage.vehicle']._category_for(france, 9, False).power_from, 7)
        self.assertEqual(self.env['expense.mileage.vehicle']._category_for(france, 2, False).power_to, 3)
        scale = self.env['expense.mileage.scale']._scale_for(electric, fields_year())
        self.assertEqual(scale.method, 'formula')
        self.assertEqual(sorted(scale.line_ids.mapped('rate')), [0.408, 0.488, 0.727])
        # An update leaves what the administrator changed.
        scale.line_ids.filtered(lambda line: line.distance_from == 0).rate = 0.8
        self.env['expense.mileage.scale']._expense_scan_scale_init()
        self.assertIn(0.8, scale.line_ids.mapped('rate'))
        self.assertEqual(len(france.expense_mileage_vehicle_ids), 10)

    # -- the expenses -------------------------------------------------------------------------

    def test_a_first_trip_is_paid_at_the_first_rate(self):
        self.assertAlmostEqual(self.trip(1000).total_amount_currency, 1000 * 0.727, places=2)

    def test_the_trip_that_crosses_5000_km_carries_both_rates(self):
        first = self.trip(4000, '2031-03-10')
        second = self.trip(2000, '2031-03-20')
        self.assertAlmostEqual(second.total_amount_currency, formula(ELECTRIC, 6000) - formula(ELECTRIC, 4000), places=2)
        # The allowance of the year is the scale of the cumulated distance, to the cent.
        total = first.total_amount_currency + second.total_amount_currency
        self.assertAlmostEqual(total, formula(ELECTRIC, 6000), delta=0.02)

    def test_beyond_5000_km_the_rate_is_the_second_one_without_the_fixed_part_again(self):
        self.trip(6000, '2031-02-01')
        self.assertAlmostEqual(self.trip(100, '2031-03-01').total_amount_currency, 100 * 0.408, places=2)

    def test_the_starting_distance_counts(self):
        self.zoe.write({'start_km': 6000.0, 'start_year': 2031})
        self.assertAlmostEqual(self.trip(100).total_amount_currency, 100 * 0.408, places=2)
        # Another year starts again from zero.
        self.assertAlmostEqual(self.trip(100, '2032-03-10').total_amount_currency, 100 * 0.727, places=2)

    def test_a_thermal_vehicle_has_the_thermal_scale(self):
        self.zoe.write({'scale_vehicle_id': self.thermal.id})
        self.assertAlmostEqual(self.trip(1000).total_amount_currency, 1000 * 0.606, places=2)
        self.assertAlmostEqual(self.trip(5000, '2031-04-01').total_amount_currency,
                               formula(THERMAL, 6000) - formula(THERMAL, 1000), places=2)

    def test_a_vehicle_without_power_takes_no_power_bound_category(self):
        categories = self.env['expense.mileage.vehicle']
        self.assertFalse(categories._category_for(self.company, 0, False).filtered(lambda c: c.power_from or c.power_to))
        anywhere = categories.create({'name': "Test any car", 'company_id': self.company.id, 'electric': False})
        self.assertIn(anywhere, categories._category_for(self.company, 0, False))

    def test_the_category_follows_the_power_and_the_fuel(self):
        vehicle = self.env['expense.employee.vehicle'].create({
            'name': "Golf", 'employee_id': self.employee.id, 'fiscal_power': 4, 'electric': False})
        self.assertTrue(vehicle.scale_vehicle_id.power_from <= 4)
        self.assertFalse(vehicle.scale_vehicle_id.electric)
        vehicle.electric = True
        self.assertTrue(vehicle.scale_vehicle_id.electric)

    def test_the_trip_takes_the_usual_vehicle_and_keeps_the_choice(self):
        expense = self.trip(100)
        self.assertEqual(expense.expense_vehicle_id, self.zoe)
        golf = self.make_vehicle(self.employee, "Golf", self.thermal, 4)
        other = self.trip(100, '2031-03-12', expense_vehicle_id=golf.id)
        self.assertEqual(other.expense_vehicle_id, golf)
        self.assertAlmostEqual(other.total_amount_currency, 100 * 0.606, places=2)

    def test_each_vehicle_has_its_own_cumulated_distance(self):
        golf = self.make_vehicle(self.employee, "Golf", self.thermal, 4)
        self.trip(6000, '2031-02-01')
        # The first kilometres of another vehicle are paid at its first rate.
        other = self.trip(100, '2031-03-12', expense_vehicle_id=golf.id)
        self.assertAlmostEqual(other.total_amount_currency, 100 * 0.606, places=2)

    def test_a_company_vehicle_keeps_the_single_rate(self):
        expense = self.trip(1000, expense_company_vehicle=True)
        self.assertFalse(expense.expense_vehicle_id)
        self.assertAlmostEqual(expense.price_unit, 0.5)
        # And it does not count in the distance of the personal vehicle.
        self.assertAlmostEqual(self.trip(100, '2031-04-01').total_amount_currency, 100 * 0.727, places=2)

    def test_the_registration_certificate_is_kept(self):
        from ..models.odoo_compat import binary_bytes
        self.zoe.write({'registration_file': {'content': b"carte grise", 'filename': "carte_grise.pdf"}})
        self.assertEqual(binary_bytes(self.zoe.registration_file), b"carte grise")

    def test_an_earlier_trip_changes_the_later_draft_ones(self):
        later = self.trip(1000, '2031-06-01')
        self.assertAlmostEqual(later.total_amount_currency, 1000 * 0.727, places=2)
        # A trip entered afterwards but dated before: the later one starts after its distance.
        first = self.trip(4500, '2031-05-01')
        self.assertAlmostEqual(later.total_amount_currency, formula(ELECTRIC, 5500) - formula(ELECTRIC, 4500), places=2)
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
            self.assertAlmostEqual(form.total_amount_currency, formula(ELECTRIC, 5100) - formula(ELECTRIC, 4900), places=2)

    def test_without_a_vehicle_the_single_rate_stays(self):
        plain = self.env['hr.employee'].create({'name': "Sans véhicule", 'expense_mileage_rate': 0.5})
        self.assertAlmostEqual(self.trip(1000, employee=plain).price_unit, 0.5)


def fields_year():
    from odoo import fields
    return fields.Date.today().year
