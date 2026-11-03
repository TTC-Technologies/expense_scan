// Copyright 2026 T.T.C. SAS
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Submit, Approve and Post of the expense list, and the actions of its
 * "Actions" menu (back to draft, ...), keep the selection.
 *
 * Odoo reloads the whole list once the action is done, which clears the
 * selection: a manager who submits a dozen expenses has to select them again
 * to approve them, or to say that they are re-invoiced. The same expenses are
 * selected again once the list is reloaded (those that are still in it: a
 * filter on the state may drop them).
 *
 * After a server action of the "Actions" menu, Odoo reloads the page, or
 * builds the list again, and reads its records a second time: the selection
 * noted when the action was chosen (see selection_memory.js) is put back on
 * the list, and again if the records are read once more in the next seconds.
 */

import { onWillUnmount } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { ExpenseListController } from "@hr_expense/views/list";
import { recallSelection } from "@expense_scan/js/selection_memory";

//: How long, after the first reading of the list, the selection is put back again, in milliseconds.
const RESTORE_MS = 3000;
//: How often, in that time, the selection is looked at.
const RESTORE_STEP_MS = 100;

patch(ExpenseListController.prototype, {
    setup() {
        super.setup();
        this.expenseScanNoted = { ids: [], until: 0 };
        // A reload of the very same list gets its selection back. A search, a sort or a grouping that
        // changes the list does not.
        const model = this.model;
        const load = model.load.bind(model);
        let last = null;
        model.load = async (...args) => {
            const ids = model.root?.selection?.map((record) => record.resId) || [];
            const { domain, groupBy, orderBy } = args[0] || {};
            const key = JSON.stringify({ domain, groupBy, orderBy });
            const same = key === last;
            const first = last === null;
            last = key;
            const result = await load(...args);
            if (same && ids.length) {
                this.expenseScanSelect(ids);
            } else if (first) {
                const noted = recallSelection();
                if (noted.length) {
                    this.expenseScanNoted = { ids: noted, until: Date.now() + RESTORE_MS };
                    this.expenseScanSelect(noted);
                }
            }
            return result;
        };
        // The records are read again a moment after the list is built, by a path that raises no event, and
        // the new ones are not selected: for a few seconds, an empty selection gets the noted one back.
        const timer = setInterval(() => {
            const { ids, until } = this.expenseScanNoted;
            if (ids.length && Date.now() < until && model.root?.records?.length && !model.root.selection.length) {
                this.expenseScanSelect(ids);
            }
        }, RESTORE_STEP_MS);
        onWillUnmount(() => clearInterval(timer));
    },

    async onClick(action) {
        const ids = this.model.root.selection.map((record) => record.resId);
        const context = {};
        if (action === "action_approve") {
            context["validate_analytic"] = true;
        }
        const reload = async () => {
            await this.model.root.load();
            this.expenseScanSelect(ids);
        };
        const res = await this.orm.call(this.model.config.resModel, action, [ids], { context });
        if (res) {
            await this.actionService.doAction(res, {
                additionalContext: { dont_redirect_to_payments: 1 },
                onClose: async (closeParams) => {
                    if (!closeParams?.noReload) {
                        await reload();
                    }
                },
            });
        }
        await reload();
    },

    /** Select again the records of ``ids`` that the list shows. */
    expenseScanSelect(ids) {
        for (const record of this.model.root.records) {
            if (ids.includes(record.resId)) {
                record.selected = true;
            }
        }
        this.model.notify();
    },
});
