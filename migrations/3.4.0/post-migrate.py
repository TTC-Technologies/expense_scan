# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""The homes and offices of the employees, among the places of the routes."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['expense.scan.place']._expense_scan_sync_employees(
        env['hr.employee'].with_context(active_test=False).search([]))
