# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Generated sheets: the kind of the earlier ones, from the expenses they hold.

A sheet whose expenses were all re-invoiced to a project is a client sheet; the others are internal.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for archive in env['expense.scan.sheet.archive'].with_context(active_test=False).search([('scope', '=', False)]):
        expenses = archive.expense_ids
        archive.scope = 'reinvoice' if expenses and all(e.reinvoice_mode == 'project' for e in expenses) else 'all'
