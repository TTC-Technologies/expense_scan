// Copyright 2026 T.T.C. SAS
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * The expense date of the search lists the last twelve months, and no quarter nor year.
 *
 * Expenses are followed month by month; any other period is a custom filter. The year of the month
 * chosen is still part of the filter (Odoo adds it), it is only not offered on its own.
 */
import { patch } from "@web/core/utils/patch";
import { SearchModel } from "@web/search/search_model";

patch(SearchModel.prototype, {
    _enrichItem(searchItem) {
        const enriched = super._enrichItem(searchItem);
        if (enriched?.type === "dateFilter" && enriched.fieldName === "date" && this.resModel === "hr.expense") {
            enriched.options = enriched.options.filter((option) => option.id.startsWith("month"));
        }
        return enriched;
    },
});
