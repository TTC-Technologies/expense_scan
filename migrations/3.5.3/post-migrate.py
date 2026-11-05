# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Generated sheets: those that cover one whole calendar month read as that month.

Sheets generated from now on say themselves whether a month was asked for. For the earlier ones the
dates are all there is: the first to the last day of a month is taken as that month.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE expense_scan_sheet_archive
           SET is_month = TRUE
         WHERE date_from IS NOT NULL AND date_to IS NOT NULL
           AND date_from = date_trunc('month', date_from)::date
           AND date_to = (date_trunc('month', date_from) + interval '1 month - 1 day')::date
    """)
    # The formats of the files already kept, from their names (the Excel files end in .xlsx; the receipts say so
    # in the language they were generated in).
    cr.execute("""
        UPDATE ir_attachment SET expense_scan_kind = CASE
            WHEN name ILIKE '%.xlsx' THEN 'excel'
            WHEN name ~* '(receipt|justificatif|reçu|recibo|beleg|ricevut|bonnetje|kwit|kvitt|bilag)' THEN 'receipts'
            ELSE 'summary' END
        WHERE res_model = 'expense.scan.sheet.archive' AND expense_scan_kind IS NULL
    """)
