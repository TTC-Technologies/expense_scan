# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Texts of the sheets whose wording changed: the translations of the module are read again.

An update keeps the translation a database already holds for a field label or a choice, even when the
English text it translated has changed ("All expenses" became "Internal: All expenses"): the catalogues
of the module are loaded once over them.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = [code for code, _name in env['res.lang'].get_installed() if code != 'en_US']
    if langs:
        env['ir.module.module']._load_module_terms(['expense_scan'], langs, overwrite=True)
