# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""The expense date lists twelve months: the trimmed search view no longer empties its months.

The view is not updated with the module (an option of the settings): where it was made, it still
carried the attributes that removed the months of the expense date.
"""
import re

from odoo import SUPERUSER_ID, api


PATTERN = re.compile(
    r"\s*<!-- The months are the two filters above.*?-->\s*<xpath expr=\"//filter\[@name='date'\]\" "
    r"position=\"attributes\">.*?</xpath>", re.S)


def clean(arch):
    return PATTERN.sub("", arch)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    view = env.ref('expense_scan.hr_expense_view_search_tidy', raise_if_not_found=False)
    if view and clean(view.arch_db) != view.arch_db:
        view.arch_db = clean(view.arch_db)
