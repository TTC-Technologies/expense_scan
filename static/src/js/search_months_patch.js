// Copyright 2026 T.T.C. SAS
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * The expense date of the search lists the last twelve months, and no quarter nor year.
 *
 * Expenses are followed month by month; any other period is a custom filter. Odoo 20 lists the current
 * month and the two before it: the view adds the nine others as options of the filter (month_3 to
 * month_11), whose names are the months, written here from the date of the day.
 */
import { patch } from "@web/core/utils/patch";
import { SearchModel } from "@web/search/search_model";

patch(SearchModel.prototype, {
    _enrichItem(searchItem) {
        const enriched = super._enrichItem(searchItem);
        if (enriched?.type === "dateFilter" && enriched.fieldName === "date" && this.resModel === "hr.expense") {
            enriched.options = enriched.options
                .filter((option) => option.id.startsWith("month") || option.id.startsWith("custom_month_"))
                .map((option) => {
                    const match = /^custom_month_(\d+)$/.exec(option.id);
                    if (!match) {
                        return option;
                    }
                    const months = -Number(match[1]);
                    return {
                        ...option,
                        description: this.referenceMoment.plus({ months }).toFormat("MMMM"),
                        groupNumber: 1,
                    };
                });
        }
        return enriched;
    },
});
