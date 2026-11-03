# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Flattening of a curled or folded receipt, for the picture shown to the user.

The receipts are drawn here, then bent by a known amount: the lines come out
straight again, a flat receipt is not touched, and a flattened picture is not
flattened a second time.
"""
import base64
import json
import random
import unittest
from unittest.mock import patch

from odoo.tests import common, tagged
from odoo.tools import file_path

from ..ocr import flatten, preprocess

WORDS = "CAFE MENU PAIN LAIT BEURRE TOTAL TVA ESPECES MONNAIE MERCI SUPERETTE TICKET".split()


def receipt(seed=1, width=700, height=1100):
    """A white receipt of about twenty-five lines of print, as a BGR picture."""
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
    font = ImageFont.truetype(file_path('web/static/fonts/google/Roboto/Roboto-Medium.ttf'), 27)
    rng = random.Random(seed)
    picture = Image.new('RGB', (width, height), (252, 252, 250))
    draw = ImageDraw.Draw(picture)
    y = 60
    while y < height - 80:
        words = [rng.choice(WORDS) for _ in range(rng.randint(2, 4))]
        draw.text((50, y), " ".join(words) + " %d.%02d" % (rng.randint(1, 30), rng.randint(0, 99)),
                  font=font, fill=(30, 30, 30))
        y += 42
    return np.array(picture)[:, :, ::-1].copy()


def bend(picture, bow=0.0, slope_top=0.0, slope_bottom=0.0):
    """The picture curled by ``bow`` pixels and with slopes that change halfway down (a fold)."""
    import cv2
    import numpy as np
    height, width = picture.shape[:2]
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    u = (xs - width / 2) / (width / 2)
    slope = np.where(ys < height / 2, slope_top, slope_bottom).astype(np.float32)
    slope = cv2.GaussianBlur(slope, (0, 0), 0.04 * height)
    return cv2.remap(picture, xs, (ys - (bow * (1 - u ** 2) + u * slope)).astype(np.float32),
                     cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def sharpness(picture):
    import cv2
    return flatten._row_sharpness(cv2.cvtColor(picture, cv2.COLOR_BGR2GRAY))


@tagged('post_install', '-at_install')
class TestFlatten(common.TransactionCase):

    def setUp(self):
        super().setUp()
        ok, message = preprocess.dependencies_status()
        if not ok or not flatten.available():
            self.skipTest(message or "OpenCV is not installed")

    def test_a_flat_receipt_is_left_alone(self):
        picture = receipt()
        flat, model = flatten.flatten(picture)
        self.assertIsNone(model)
        self.assertIs(flat, picture, "the very picture, not a copy resampled")

    def test_curled_and_folded_receipts_get_their_lines_straight(self):
        for name, kwargs in (("curled", dict(bow=30)), ("curled the other way", dict(bow=-24)),
                             ("folded in two", dict(slope_bottom=30)),
                             ("folded, slopes of opposite sense", dict(slope_top=-22, slope_bottom=24)),
                             ("curled and folded", dict(bow=20, slope_top=-10, slope_bottom=24))):
            reference = receipt(seed=3)
            bent = bend(reference, **kwargs)
            flat, model = flatten.flatten(bent)
            self.assertTrue(model, name)
            # The ink falls again into rows: from a fraction of what a flat receipt does to most of it.
            self.assertLess(sharpness(bent), 0.8 * sharpness(reference), name)
            self.assertGreater(sharpness(flat), 0.78 * sharpness(reference), name)
            self.assertEqual(flat.shape, bent.shape)
            # Applied once: the flattened picture is stable.
            self.assertIsNone(flatten.flatten(flat)[1], name)

    def test_a_picture_that_is_not_a_receipt_is_left_alone(self):
        import numpy as np
        rng = np.random.RandomState(0)
        for picture in (np.full((600, 400, 3), 255, np.uint8),
                        rng.randint(0, 255, (600, 400, 3)).astype(np.uint8),
                        np.zeros((5, 5, 3), np.uint8)):
            flat, model = flatten.flatten(picture)
            self.assertIsNone(model)
            self.assertIs(flat, picture)
        self.assertEqual(flatten.flatten(None), (None, None))
        self.assertEqual(flatten.flatten("image"), ("image", None))

    def test_the_picture_is_not_replaced_by_one_with_blurrier_rows(self):
        """Whatever the model says, the rows of ink must come out crisper."""
        bent = bend(receipt(seed=5), bow=26)
        with patch.object(flatten, '_row_sharpness', side_effect=[1.0, 0.9]):
            flat, model = flatten.flatten(bent)
        self.assertIsNone(model)
        self.assertIs(flat, bent)

    def test_a_failure_leaves_the_picture_alone(self):
        bent = bend(receipt(seed=5), bow=26)
        with patch.object(flatten, 'fit', side_effect=ValueError("singular")):
            flat, model = flatten.flatten(bent)
        self.assertIsNone(model)
        self.assertIs(flat, bent)


@tagged('post_install', '-at_install')
class TestFlattenInTheExpense(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ok, message = preprocess.dependencies_status()
        if not ok or not flatten.available():
            raise unittest.SkipTest(message or "OpenCV is not installed")
        cls.employee = cls.env['hr.employee'].create({'name': "Rosa Roulé"})

    def expense(self, picture):
        data = preprocess.encode_jpeg(picture)
        expense = self.env['hr.expense'].create({'name': "Ticket roulé", 'employee_id': self.employee.id})
        attachment = self.env['ir.attachment'].create({
            'name': 'ticket.jpg', 'raw': data, 'mimetype': 'image/jpeg',
            'res_model': 'hr.expense', 'res_id': expense.id})
        expense.sudo()._message_set_main_attachment_id(attachment, force=True)
        return expense

    def test_the_editor_flattens_the_receipt_as_it_is_turned(self):
        bent = bend(receipt(seed=2), bow=30)
        expense = self.expense(bent)
        answer = expense.expense_scan_retouch_flatten(0, 0.0)
        self.assertTrue(answer['changed'])
        flat = preprocess.load_image(base64.b64decode(answer['url'].split(',', 1)[1]))
        self.assertGreater(sharpness(flat), 1.5 * sharpness(bent))
        # Turned a quarter, the receipt lies: its lines run down, nothing to flatten.
        self.assertFalse(expense.expense_scan_retouch_flatten(1, 0.0)['changed'])
        # A flat receipt: the picture comes back as it was, "changed" says so.
        answer = self.expense(receipt(seed=2)).expense_scan_retouch_flatten(0, 0.0)
        self.assertFalse(answer['changed'])
        self.assertTrue(answer['url'].startswith('data:image/jpeg;base64,'))

    def test_the_settings_of_a_flattened_retouch_are_kept(self):
        clean = self.env['hr.expense']._expense_scan_clean_retouch_params
        params = {'quarter': 0, 'fine': 0, 'crop': [0.1, 0.1, 0.9, 0.9], 'flat': {'quarter': 1, 'fine': -3.5}}
        self.assertEqual(json.loads(clean(params))['flat'], {'quarter': 1, 'fine': -3.5})
        params['flat'] = None
        self.assertNotIn('flat', json.loads(clean(params)))
        params['flat'] = {'quarter': 'x'}
        self.assertFalse(clean(params))

    def test_the_scan_flattens_the_picture_last_and_says_so(self):
        from ..ocr.types import OcrWord

        class Engine:
            label = description = "fake engine"
            working_side = 1800

            def recognize(self, image):
                return [OcrWord(text="TOTAL 12,50", score=0.99, left=50, top=50, right=300, bottom=80)]

        bent = bend(receipt(seed=4), bow=30)
        expense = self.expense(bent)
        with patch('odoo.addons.expense_scan.ocr.engines.resolve_engine', return_value=Engine()):
            result = expense._expense_scan_process(expense.message_main_attachment_id)
        self.assertTrue(result.preprocess.flattened)
        self.assertTrue(result.image_bytes)
        self.assertIn("flattened", expense.with_context(lang='en_US')._expense_scan_summary(result))
        shown = preprocess.load_image(result.image_bytes)
        self.assertGreater(sharpness(shown), 1.3 * sharpness(bent))
        # A company that does not straighten leaves the picture as it is.
        expense.company_id.expense_scan_deskew = False
        with patch('odoo.addons.expense_scan.ocr.engines.resolve_engine', return_value=Engine()):
            result = expense._expense_scan_process(expense.message_main_attachment_id)
        self.assertFalse(result.preprocess.flattened)
