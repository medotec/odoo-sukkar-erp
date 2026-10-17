import { patch } from "@web/core/utils/patch";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";

/**
 * Cash on Delivery is offered for a delivery only (owner, 10 Oct 2026): a
 * pickup is paid at the counter, so the driver can never be the one to
 * collect it.
 */
patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        const order = this.pos.get_order();
        if (order?.sukkarDelivery?.sukkar_order_type !== "delivery") {
            this.payment_methods_from_config = this.payment_methods_from_config.filter(
                (method) => !method.sukkar_is_cod);
        }
    },

    /** Odoo warns "generate an invoice… for due balance follow-up" whenever a
        Customer Account method is tapped. For Cash on Delivery the follow-up is
        the driver's cash hand-in, so the warning is only noise at the counter
        (owner, 10 Oct 2026). Only that warning, only for that button. */
    async addNewPaymentLine(paymentMethod) {
        if (!paymentMethod?.sukkar_is_cod) {
            return super.addNewPaymentLine(...arguments);
        }
        const notification = this.notification;
        this.notification = {
            ...notification,
            add: (message, options = {}) =>
                String(message).includes("due balance follow-up")
                    ? () => {}
                    : notification.add(message, options),
        };
        try {
            return await super.addNewPaymentLine(...arguments);
        } finally {
            this.notification = notification;
        }
    },
});
