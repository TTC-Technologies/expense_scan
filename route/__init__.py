# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Routes of mileage expenses, without depending on Odoo.

Finding an address (geocoding), the road distance between two points and
the picture of the route that serves as the receipt. The network goes
through a ``fetch(url) -> bytes`` function given by the caller: the module
passes its own, the tests a fake one.
"""
