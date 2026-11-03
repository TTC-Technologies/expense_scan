# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""The item lines of a till receipt, read from the text once the expense is entered.

The scan reads the header and the total, which is what the employee waits for. The lines of items (what
was bought, line by line) are read afterwards, from the text kept, by a scheduled task: nothing is added
to the time of the scan. They are shown only when they add up to the total of the expense, which is what
tells a reading that can be trusted from a table of an invoice taken for a list of items.
"""
import json
import logging

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.modules import module as odoo_module

from ..ocr import parser

_logger = logging.getLogger(__name__)

#: A line is read at most this many at a time by the scheduled task.
BATCH = 300


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    #: ``[[label, amount], ...]`` as JSON once read; ``[]`` when the receipt has no lines that add up to
    #: its total; empty while it is still to read.
    expense_scan_items = fields.Text(string="Receipt lines (read)", readonly=True, copy=False)
    expense_scan_items_html = fields.Html(
        string="Lines of the receipt", compute='_compute_expense_scan_items_html', sanitize=False)

    @api.depends('expense_scan_items', 'total_amount_currency', 'currency_id')
    def _compute_expense_scan_items_html(self):
        for expense in self:
            items = expense._expense_scan_items_shown()
            rows = Markup('').join(
                Markup('<tr><td class="pe-3">%s</td><td class="text-end text-nowrap">%s</td></tr>') % (
                    escape(label), escape(expense.currency_id.format(amount) if expense.currency_id
                                          else "%.2f" % amount))
                for label, amount in items)
            expense.expense_scan_items_html = Markup(
                '<table class="o_expense_scan_items small text-muted">%s</table>') % rows if rows else False

    def _expense_scan_items_shown(self):
        """The lines to show: read, and still adding up to the total of the expense."""
        self.ensure_one()
        try:
            items = json.loads(self.expense_scan_items or '[]')
        except ValueError:
            return []
        if len(items) < 2 or not self._expense_scan_items_add_up(items):
            return []
        return [(label, amount) for label, amount in items]

    def _expense_scan_items_add_up(self, items):
        self.ensure_one()
        total = sum(amount for _label, amount in items)
        return abs(total - self.total_amount_currency) <= parser.ITEMS_TOLERANCE

    def _expense_scan_read_items(self):
        """Read the lines of the receipts of these expenses; kept when they add up to the total."""
        for expense in self:
            items = parser.extract_items(expense.scan_raw_text)
            kept = items if len(items) >= 2 and expense._expense_scan_items_add_up(items) else []
            expense.with_context(tracking_disable=True).expense_scan_items = json.dumps(kept)

    @api.model
    def _cron_expense_scan_items(self, batch=BATCH):
        """Read the lines of the receipts scanned since the last run."""
        todo = self.sudo().search([
            ('scan_raw_text', '!=', False), ('expense_scan_items', '=', False),
            ('scan_state', 'in', ('done', 'partial'))], limit=batch, order='id desc')
        todo._expense_scan_read_items()
        if not odoo_module.current_test:
            self.env.cr.commit()
        return len(todo)
