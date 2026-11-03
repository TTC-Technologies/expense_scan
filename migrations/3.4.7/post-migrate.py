# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""The national map of each country, where the Plan IGN was the map background."""
from odoo.addons.expense_scan.route import geo


def migrate(cr, version):
    cr.execute("UPDATE res_company SET expense_scan_map_tiles_url = %s WHERE expense_scan_map_tiles_url = %s",
               (geo.AUTO_MAPS, geo.IGN_TILES))
