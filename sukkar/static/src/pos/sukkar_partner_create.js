import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { makeAwaitable } from "@point_of_sale/app/store/make_awaitable_dialog";
import { SukkarCustomerPopup } from "@sukkar/pos/sukkar_customer_popup";

/**
 * The till's customer screen is the counter form, never Odoo's contact form —
 * for a new customer, and since 2 Oct 2026 for editing one too (owner: "the
 * same as customer creation, with an option to edit the address").
 */
patch(PosStore.prototype, {
    async editPartner(partner) {
        const saved = await makeAwaitable(
            this.env.services.dialog, SukkarCustomerPopup,
            partner ? { partner } : {});
        return saved || undefined;
    },
});
