# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
{
    'name': "Expense Receipt Scanner",
    'version': '19.0.3.4.2',
    # The store search reads only the name and the summary, as exact phrases: they carry the
    # words people type (expense OCR, receipt OCR, expense scanner, scan receipts, local OCR).
    # The store asks for a name of 25 characters at most.
    'summary': "Expense scanner with receipt OCR: scan receipts with a phone and the expense fills itself in "
               "(merchant, date, total, VAT, category). Local OCR and AI on your own server: expense OCR with "
               "no API key, no fee per scan.",
    'description': """
Expense Receipt Scanner: scan receipts with local OCR and AI
============================================================

Receipt scanner for Odoo expenses. Photograph a receipt with your phone, or upload a
photo or a PDF, and the expense is filled in: merchant, date, total, currency, VAT and
category. The employee reviews the expense side by side with the image of the receipt.

The scan runs on your own Odoo server. A neural network for optical character
recognition (PP-OCR, run by ONNX Runtime) reads the text, and a parser written for this
module turns it into an expense. No API key, no account, no fee per scan, and the
receipts never leave the server.

* One button ("Scan" on a phone, "Upload" on a computer) opens the camera or the photo
  library; several receipts can be scanned at once.
* The receipt is cropped and straightened before it is read; a manual retouch is there
  for the difficult ones.
* Doubtful fields carry a hint; the module never approves an expense by itself.
* Expense category recognition, receipts in several pieces, hotel nights, business meals,
  mileage by route with its map, projects and re-invoicing to the customer, expense
  sheets (PDF and Excel), expense rules and policies, exchange rates of the European
  Central Bank, 11 interface languages.

Requirements: the Python packages opencv-python-headless, rapidocr, onnxruntime,
pdf2image and openpyxl, and the poppler-utils package; about 285 MB on disk with the
OCR models (47.5 MB, downloaded once). Not for Odoo Online.

Network access: the OCR models are downloaded once; the public reference rates of the
European Central Bank are downloaded daily (one switch in the settings). For mileage by
route only, the places typed are looked up on OpenStreetMap (Nominatim, OSRM; your own
servers can be set instead). Receipts, their texts and amounts never leave the server.

Free module, LGPL-3.
""",
    'author': "T.T.C. SAS",
    'support': "ttc@green-engine.eu",
    'category': 'Human Resources/Expenses',
    'license': 'LGPL-3',
    'images': ['static/description/banner.gif'],
    'depends': ['hr_expense', 'project'],
    # Tesseract, the fallback engine, stays optional.
    'external_dependencies': {
        'python': ['numpy', 'cv2', 'rapidocr', 'onnxruntime', 'pdf2image', 'openpyxl'],
        'bin': ['pdftoppm'],
    },
    'data': [
        'security/ir.model.access.csv',
        'security/expense_scan_rules.xml',
        'report/expense_sheet_report.xml',
        'data/export_templates.xml',
        'data/expense_policies.xml',
        'data/expense_scan_cron.xml',
        'data/mail_activity_types.xml',
        'data/menu_icon.xml',
        'views/hr_expense_views.xml',
        'views/hr_employee_views.xml',
        'views/expense_team_views.xml',
        'views/expense_batch_actions.xml',
        'views/expense_sheet_views.xml',
        'views/expense_policy_views.xml',
        'views/expense_invoicing_views.xml',
        'views/expense_vat_views.xml',
        'views/expense_scan_correction_views.xml',
        'views/expense_scan_place_views.xml',
        'views/product_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'expense_scan/static/src/scss/expense_scan.scss',
            'expense_scan/static/src/css/control_panel_mobile.css',
            'expense_scan/static/src/js/**/*.js',
            'expense_scan/static/src/js/**/*.xml',
            'expense_scan/static/src/xml/**/*.xml',
        ],
    },
    'demo': [
        'demo/expense_policy_demo.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'installable': True,
    'application': False,
    'auto_install': False,
}
