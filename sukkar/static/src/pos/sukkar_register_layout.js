import { patch } from "@web/core/utils/patch";
import { floatIsZero } from "@web/core/utils/numbers";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";
import { ActionpadWidget } from "@point_of_sale/app/screens/product_screen/action_pad/action_pad";
import { ProductCard } from "@point_of_sale/app/generic_components/product_card/product_card";

const { DateTime } = luxon;

/**
 * The register, laid out as the Counter — the owner's pick of three mocks,
 * 29 Sep 2026 (docs/mockups/layout-2-rtl-counter.html):
 *
 *   left   — the categories, as a rail;
 *   middle — the bouquets, each photo carrying its price;
 *   right  — the order, with − / + and ✕ on every line, the bill, three big
 *            buttons and Pay.
 *
 * English only, left to right (29 Sep 2026). No button changes what it does
 * here: each one calls the same Odoo or Sukkar code it called before. The
 * number pad (price and discount) waits behind More.
 */

patch(ProductScreen.prototype, {
    setup() {
        super.setup(...arguments);
        // The delivery windows, so the ticket can name the chosen one even for
        // an order that was set up before the register was reloaded.
        this.pos.sukkarSlots();
    },

    /** The price printed on the photo. A bouquet sold in sizes shows the price
        its sizes start at, marked "from", so a quote read off the screen is
        never lower than the size the customer then picks. */
    sukkarCardPrice(product) {
        const templateId = product.raw.product_tmpl_id;
        const prices = this.pos.models["product.product"]
            .filter((variant) => variant.raw.product_tmpl_id === templateId)
            .map((variant) => this.pos.getProductPrice(variant));
        const price = this.env.utils.formatCurrency(Math.min(...prices), false);
        return new Set(prices).size > 1 ? `from ${price}` : price;
    },

    get sukkarOrderNumber() {
        return this.currentOrder?.tracking_number || "";
    },

    // How and when this order leaves the shop, once the cashier has said so:
    // "Delivery · Today · 17:00-19:00".
    get sukkarTicketTag() {
        const delivery = this.currentOrder?.sukkarDelivery || {};
        if (!delivery.sukkar_order_type) {
            return "";
        }
        const slot = (this.pos.sukkarDeliverySlots || []).find(
            (window) => window.id === delivery.sukkar_delivery_slot_id);
        return [
            delivery.sukkar_order_type === "delivery" ? "Delivery" : "Pickup",
            this.sukkarDayLabel(delivery.sukkar_delivery_date),
            slot?.name,
        ].filter(Boolean).join(" · ");
    },

    // Today and tomorrow by name — the two days a counter sale is almost always
    // for — and any other day by its date. The till runs on Kuwait time.
    sukkarDayLabel(isoDate) {
        if (!isoDate) {
            return "";
        }
        const day = DateTime.fromISO(isoDate).startOf("day");
        const daysAhead = Math.round(day.diff(DateTime.now().startOf("day"), "days").days);
        if (daysAhead === 0) {
            return "Today";
        }
        if (daysAhead === 1) {
            return "Tomorrow";
        }
        return day.toFormat("dd/MM");
    },

    // Pay carries the amount, so the cashier reads it before pressing.
    get sukkarPayTotal() {
        return this.env.utils.formatCurrency(this.currentOrder?.get_total_with_tax() ?? 0, false);
    },

    sukkarCloseNumpad() {
        this.pos.sukkarNumpadOpen = false;
    },
});

patch(OrderSummary.prototype, {
    // The line as Odoo draws it, plus the bouquet's photo. Cart only: receipts
    // build their own lines and never see this.
    sukkarLineData(line) {
        const data = line.getDisplayData();
        if (line.product_id && !this.sukkarIsFee(line)) {
            data.imageSrc = line.product_id.getTemplateImageUrl();
        }
        return data;
    },

    // The delivery fee rides on its own product, the one Settings → Sukkar names.
    sukkarIsFee(line) {
        const company = this.pos.company;
        const feeId = company.sukkar_pos_fee_product_id?.id
            || company.raw?.sukkar_pos_fee_product_id;
        return Boolean(feeId) && line.product_id?.id === feeId;
    },

    // − and + are for what is being sold. A refund, the delivery fee and the
    // parts of a combo keep Odoo's own quantity handling.
    sukkarCanStep(line) {
        return line.qty > 0
            && !this.sukkarIsFee(line)
            && !line.refunded_orderline_id
            && !line.combo_parent_id
            && !line.isTipLine();
    },

    async sukkarStep(line, delta) {
        const quantity = line.get_quantity() + delta;
        if (quantity < 1) {
            return;  // ✕ removes a line; − never does it by surprise
        }
        this.pos.selectOrderLine(this.currentOrder, line);
        if (delta < 0 && this.pos.disallowLineQuantityChange()) {
            // Where lowering a sent quantity needs a reason, Odoo asks for it.
            return this._showDecreaseQuantityPopup();
        }
        const result = line.set_quantity(quantity, Boolean(line.combo_line_ids?.length));
        for (const part of line.combo_line_ids || []) {
            part.set_quantity(quantity, true);
        }
        if (result !== true) {
            this.dialog.add(AlertDialog, result);
        }
    },

    /** The bill under the lines: the items, the delivery, the total. Tax prints
        only when there is some — Kuwait charges none, so a "Taxes 0.000" line
        was noise on every order. */
    get sukkarTotals() {
        const order = this.currentOrder;
        const digits = this.pos.currency.decimal_places;
        const fee = order.lines
            .filter((line) => this.sukkarIsFee(line))
            .reduce((total, line) => total + line.get_price_with_tax(), 0);
        const total = order.get_total_with_tax();
        const tax = order.get_total_tax();
        const amount = (value) => this.env.utils.formatCurrency(value, false);
        return {
            items: amount(total - fee),
            fee: floatIsZero(fee, digits) ? "" : amount(fee),
            tax: floatIsZero(tax, digits) ? "" : amount(tax),
            total: this.env.utils.formatCurrency(total),
        };
    },
});

// The extra things the screens now hand these components. Declared, so the
// register's own checks (debug mode) accept them.
ProductCard.props = {
    ...ProductCard.props,
    sukkarPrice: { type: String, optional: true },
};
ActionpadWidget.props = {
    ...ActionpadWidget.props,
    sukkarTotal: { type: String, optional: true },
};
