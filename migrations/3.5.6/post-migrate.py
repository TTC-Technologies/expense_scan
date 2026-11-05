# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mileage scales: the categories made by the module read as whole sentences in every language.

They were named "Car" + an English fragment ("Voiture 3 CV or less" in a French database). Those still
carrying that name get their translated name in each language of the database.
"""
from odoo import SUPERUSER_ID, api

LANGS = {'da': 0, 'de': 1, 'es': 2, 'fr': 3, 'it': 4, 'nb': 5, 'nl': 6, 'pl': 7, 'pt': 8, 'sv': 9}
NAMES = {'Car 3 CV or less': ('Bil 3 CV eller derunder', 'Auto 3 CV oder weniger', 'Coche 3 CV o menos', 'Voiture 3 CV ou moins', 'Auto 3 CV o meno', 'Bil 3 CV eller mindre', 'Auto 3 CV of minder', 'Samochód 3 CV lub mniej', 'Carro 3 CV ou menos', 'Bil 3 CV eller mindre'), 'Car 4 CV': ('Bil 4 CV', 'Auto 4 CV', 'Coche 4 CV', 'Voiture 4 CV', 'Auto 4 CV', 'Bil 4 CV', 'Auto 4 CV', 'Samochód 4 CV', 'Carro 4 CV', 'Bil 4 CV'), 'Car 5 CV': ('Bil 5 CV', 'Auto 5 CV', 'Coche 5 CV', 'Voiture 5 CV', 'Auto 5 CV', 'Bil 5 CV', 'Auto 5 CV', 'Samochód 5 CV', 'Carro 5 CV', 'Bil 5 CV'), 'Car 6 CV': ('Bil 6 CV', 'Auto 6 CV', 'Coche 6 CV', 'Voiture 6 CV', 'Auto 6 CV', 'Bil 6 CV', 'Auto 6 CV', 'Samochód 6 CV', 'Carro 6 CV', 'Bil 6 CV'), 'Car 7 CV or more': ('Bil 7 CV eller mere', 'Auto 7 CV oder mehr', 'Coche 7 CV o más', 'Voiture 7 CV et plus', 'Auto 7 CV o più', 'Bil 7 CV eller mer', 'Auto 7 CV of meer', 'Samochód 7 CV lub więcej', 'Carro 7 CV ou mais', 'Bil 7 CV eller mer')}
LEGACY = {
    "Car 3 CV or less": "Car 3 CV or less",
    "Car 7 CV and more": "Car 7 CV or more",
}
ELECTRIC = {"en_US": "(electric)", "fr_FR": "(\u00e9lectrique)"}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    active = {code: code.split("_")[0] for code in env['res.lang'].search([]).mapped('code')}
    for vehicle in env['expense.mileage.vehicle'].with_context(active_test=False).search([]):
        en = vehicle.with_context(lang='en_US').name or ""
        for legacy, key in LEGACY.items():
            if en not in (legacy, legacy + " (electric)"):
                continue
            electric = en.endswith("(electric)")
            for code, short in active.items():
                index = LANGS.get(short)
                if index is None:
                    continue
                name = NAMES[key][index]
                if electric:
                    name += " " + ELECTRIC.get(code, "(electric)")
                vehicle.with_context(lang=code).name = name
