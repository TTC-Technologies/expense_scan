// Copyright 2026 T.T.C. SAS
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * The selection of the expense list, kept across the reload that follows an action.
 *
 * A server action of the "Actions" menu (back to draft, ...) may reload the whole page: the expenses
 * selected when the action was chosen are noted here, in the session storage of the tab, and the list
 * built next selects them again if they are still in it. Noted for a short time only.
 */

//: How long a noted selection stays good, in milliseconds: the time of a reload of the page, during which
//: Odoo may build the list more than once.
const KEEP_MS = 8000;
const KEY = "expense_scan.selection";
let noted = null;

/** Note the ids selected when an action is chosen. */
export function noteSelection(ids) {
    noted = ids.length ? { ids: [...ids], at: Date.now() } : null;
    try {
        if (noted) {
            browserStorage().setItem(KEY, JSON.stringify(noted));
        } else {
            browserStorage().removeItem(KEY);
        }
    } catch {
        // No storage (private window, blocked data): the selection is kept in memory only.
    }
}

/** The ids noted a moment ago, for each list built since. */
export function recallSelection() {
    let found = noted;
    try {
        found = found || JSON.parse(browserStorage().getItem(KEY) || "null");
    } catch {
        // Nothing stored.
    }
    return found && Date.now() - found.at < KEEP_MS ? found.ids : [];
}

function browserStorage() {
    return window.sessionStorage;
}
