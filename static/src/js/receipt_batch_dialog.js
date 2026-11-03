// Copyright 2026 T.T.C. SAS
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Receipt batch: photograph several receipts in a row, read them together afterwards.
 *
 * A week of receipts is photographed in one go: each photo is kept on the phone (the dialog counts them,
 * and stays open behind the camera), and "Finish" sends them all as one upload, which creates one
 * expense per receipt and shows their list. They are then reviewed one by one, with the arrows of the
 * form.
 */
import { _t } from "@web/core/l10n/translation";
import { Component, t, useProps } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

export class ReceiptBatchDialog extends Component {
    static template = "expense_scan.ReceiptBatchDialog";
    static components = { Dialog };
    props = useProps({
        // The photos taken, in a reactive object: { files: [File], thumbs: [url] }.
        batch: t.object(),
        // Open the camera for the next one (within the click, or the browser refuses).
        next: t.function(),
        // Send the photos.
        finish: t.function(),
        // Throw them away.
        cancel: t.function(),
        close: t.function(),
    });

    get title() {
        return _t("Receipt batch");
    }

    get count() {
        return this.props.batch.files.length;
    }

    get summary() {
        return this.count
            ? _t("%(count)s receipt(s) ready to be read.", { count: this.count })
            : _t("Take the first receipt in photo.");
    }

    get finishLabel() {
        return _t("Finish and read the %(count)s receipt(s)", { count: this.count });
    }

    onFinish() {
        this.props.finish();
        this.props.close();
    }

    onCancel() {
        this.props.cancel();
        this.props.close();
    }
}
