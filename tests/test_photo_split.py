# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""A photo of several receipts: each cut out, the purchases told apart, one expense each.

The receipts are drawn on a table (a dark picture), then read with a fake engine that answers by the
number of the piece: the tests cover the cutting, the grouping and the writing, not the reading.
"""
import re
import unittest
from unittest.mock import patch

from odoo.tests import common, tagged

from ..ocr import flatten, parser, preprocess
from ..ocr.types import PreprocessInfo
from .test_flatten import receipt
from .test_parser import words_from_text


def table(count, size=(2100, 1200)):
    """``count`` receipts laid on a dark table, a little askew."""
    import cv2
    import numpy as np
    canvas = np.full((size[1], size[0]), 0, np.uint8)
    canvas = np.dstack([canvas + 45, canvas + 60, canvas + 70]).astype(np.uint8)
    centres = {1: [(0.5, 0.5)], 2: [(0.27, 0.5), (0.73, 0.5)], 3: [(0.2, 0.5), (0.5, 0.5), (0.8, 0.5)]}[count]
    for index, (cx, cy) in enumerate(centres):
        piece = preprocess.rotate(cv2.resize(receipt(seed=40 + index), None, fx=0.6, fy=0.6), (0, 3, -4)[index])
        x = max(0, int(cx * size[0] - piece.shape[1] / 2))
        y = max(0, int(cy * size[1] - piece.shape[0] / 2))
        region = canvas[y:y + piece.shape[0], x:x + piece.shape[1]]
        region[:] = piece[:region.shape[0], :region.shape[1]]
    return canvas


@tagged('post_install', '-at_install')
class TestPhotoSplit(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ok, message = preprocess.dependencies_status()
        if not ok or not flatten.available():
            raise unittest.SkipTest(message or "OpenCV is not installed")
        cls.employee = cls.env['hr.employee'].create({'name': "Tania Table"})

    def test_the_receipts_of_a_photo_are_cut_out_in_reading_order(self):
        self.assertEqual(preprocess.split_receipts(table(1)), [])
        for count in (2, 3):
            cuts = preprocess.split_receipts(table(count))
            self.assertEqual(len(cuts), count)
            self.assertTrue(all(cut.shape[0] > 300 and cut.shape[1] > 200 for cut in cuts))

    def expense(self, count):
        expense = self.env['hr.expense'].create({'name': "Photo", 'employee_id': self.employee.id})
        attachment = self.env['ir.attachment'].create({
            'name': 'table.jpg', 'raw': preprocess.encode_jpeg(table(count)), 'mimetype': 'image/jpeg',
            'res_model': 'hr.expense', 'res_id': expense.id})
        expense.sudo()._message_set_main_attachment_id(attachment, force=True)
        return expense, attachment

    @staticmethod
    def reader(texts):
        """A fake reading: the text of the piece named "(n/N)"; the whole photo has the first."""
        def read(self, attachment, progress=None):
            match = re.search(r"\((\d+)/\d+\)", attachment.name or "")
            text = texts[(int(match.group(1)) - 1) if match else 0]
            result = parser.parse(words_from_text(text))
            result.engine, result.duration, result.preprocess = "fake", 0.0, PreprocessInfo()
            return result
        return read

    def scan(self, expense, texts):
        with patch.object(type(expense), '_expense_scan_process', autospec=True, side_effect=self.reader(texts)), \
                patch.object(type(expense), '_expense_scan_store_image', autospec=True, return_value={}):
            expense.with_context(lang='en_US')._expense_scan_run(force=True)

    def test_two_purchases_make_two_expenses(self):
        expense, photo = self.expense(2)
        before = self.env['hr.expense'].search([('employee_id', '=', self.employee.id)])
        self.scan(expense, ["TRAITEUR SARL\n12/09/2026 12:30\nTOTAL 12,50", "GARAGE DUPONT\n12/09/2026 15:10\nTOTAL 80,00"])
        expenses = self.env['hr.expense'].search([('employee_id', '=', self.employee.id)]) - before
        mine = expense | expenses
        self.assertEqual(len(mine), 2, "one expense each")
        self.assertEqual(sorted(mine.mapped('total_amount_currency')), [12.5, 80.0])
        self.assertEqual(expense.total_amount_currency, 12.5, "the first stays on the expense that was scanned")
        # The photo is kept, apart, and the receipts attached are the pieces.
        self.assertEqual(expense.scan_original_attachment_id, photo)
        self.assertNotIn(photo, expense._expense_scan_image_attachments())
        for each in mine:
            self.assertEqual(len(each._expense_scan_image_attachments()), 1)
        self.assertIn("2 different purchases", " ".join(expense.message_ids.mapped('body')))

    def test_the_two_parts_of_one_purchase_make_one_expense(self):
        """The till receipt and the card slip, on one photo: one expense, read from the whole photo."""
        expense, photo = self.expense(2)
        self.scan(expense, ["TRAITEUR SARL\n12/09/2026 12:30\nTOTAL 12,50", "CARTE BANCAIRE\nMONTANT 12,50 EUR"])
        self.assertEqual(self.env['hr.expense'].search_count([('employee_id', '=', self.employee.id)]), 1)
        self.assertEqual(expense.total_amount_currency, 12.5)
        # No piece is left behind: the photo itself is the receipt.
        self.assertEqual(expense._expense_scan_image_attachments(), photo)

    def test_a_photo_of_one_receipt_is_read_as_before(self):
        expense, photo = self.expense(1)
        self.scan(expense, ["TRAITEUR SARL\n12/09/2026 12:30\nTOTAL 12,50"])
        self.assertEqual(expense.total_amount_currency, 12.5)
        self.assertEqual(expense._expense_scan_image_attachments(), photo)
        self.assertFalse(expense.scan_original_attachment_id)
