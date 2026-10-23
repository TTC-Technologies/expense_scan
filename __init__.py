# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from . import models
from .models import native_tweaks
from .models.renamed_translations import apply_renamed_translations


def post_init_hook(env):
    """Add receipt words and icons to the existing expense categories, and
    the homes and offices of the employees to the places of the routes."""
    env['product.template']._expense_scan_seed_keywords()
    env['product.template']._expense_scan_seed_icons()
    env['expense.scan.place']._expense_scan_sync_employees(
        env['hr.employee'].with_context(active_test=False).search([]))
    apply_renamed_translations(env)


def uninstall_hook(env):
    """Give back the menus, actions and icon of Odoo that the module changed."""
    native_tweaks.restore(env)
