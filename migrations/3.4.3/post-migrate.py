# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""The route pictures on the Plan IGN, where no map background was set."""
from odoo.addons.expense_scan.route import geo


def migrate(cr, version):
    cr.execute("UPDATE res_company SET expense_scan_map_tiles_url = %s "
               "WHERE COALESCE(expense_scan_map_tiles_url, '') = ''", (geo.IGN_TILES,))
