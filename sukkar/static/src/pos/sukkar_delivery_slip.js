import { Component, useRef } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { patch } from "@web/core/utils/patch";
import { ReceiptScreen } from "@point_of_sale/app/screens/receipt_screen/receipt_screen";

/**
 * The delivery slip — the piece of paper that rides with the order.
 *
 * It answers, in English and in one glance, the five things a driver stops to
 * ask: who it goes to, what their phone is, where exactly, when it must be
 * there, and whether there is money to collect. Everything else on a receipt —
 * the shop's register number, loyalty points, the gift message — is left off;
 * this is a working document, not a keepsake.
 *
 * Printed from the till's receipt screen, beside the customer's own receipt.
 * A pickup never needs one, so the button is not offered for a pickup.
 * (Owner's ask, 29 Sep 2026.)
 */
export class SukkarDeliverySlip extends Component {
    static template = "sukkar.DeliverySlip";
    static components = { Dialog };
    static props = { order: Object, close: Function };

    setup() {
        this.pos = usePos();
        this.sheet = useRef("sheet");
    }

    get order() {
        return this.props.order;
    }

    get delivery() {
        return this.order.sukkarDelivery || {};
    }

    get slip() {
        const d = this.delivery;
        const partner = this.order.get_partner();
        const slot = (this.pos.sukkarDeliverySlots || []).find(
            (window) => window.id === d.sukkar_delivery_slot_id);
        const driver = (this.pos.models["res.users"] || []).find(
            (user) => user.id === d.sukkar_driver_id);
        return {
            orderNumber: this.order.tracking_number || this.order.name || "",
            receiverName: d.sukkar_receiver_name || partner?.name || "",
            receiverPhone: d.sukkar_receiver_phone
                || partner?.mobile || partner?.phone || "",
            isGift: Boolean(d.sukkar_recipient_id),
            senderName: partner?.name || "",
            senderPhone: partner?.mobile || partner?.phone || "",
            area: d.sukkar_addr_area || "",
            addressLines: this.addressLines,
            directions: d.sukkar_addr_directions || "",
            deliveryNote: d.sukkar_delivery_note || "",
            date: d.sukkar_delivery_date || "",
            window: slot?.name || "",
            driver: driver?.name || "",
            fee: d.sukkar_delivery_fee || 0,
        };
    }

    /** The address in the order a driver reads it: the area, then the way in. */
    get addressLines() {
        const d = this.delivery;
        const line = (label, value) => (value ? `${label} ${value}` : "");
        return [
            [line("Block", d.sukkar_addr_block),
             line("Street", d.sukkar_addr_street),
             line("Avenue", d.sukkar_addr_avenue)].filter(Boolean).join(" · "),
            [line("House", d.sukkar_addr_building),
             line("Floor", d.sukkar_addr_floor),
             line("Apartment", d.sukkar_addr_apartment)].filter(Boolean).join(" · "),
        ].filter(Boolean);
    }

    /** What the bouquets are, without the prices: the driver is checking the
        box holds what the ticket says, not selling it again. */
    get items() {
        return this.order.lines
            .filter((line) => line.qty > 0 && line.product_id?.id !== this.feeProductId)
            .map((line) => ({
                qty: this.env.utils.formatProductQty(line.get_quantity(), false),
                name: line.product_id?.display_name || "",
                note: line.sukkar_flavour_summary || line.get_customer_note() || "",
            }));
    }

    get feeProductId() {
        const company = this.pos.company;
        return company.sukkar_pos_fee_product_id?.id
            || company.raw?.sukkar_pos_fee_product_id
            || 0;
    }

    /** What the driver has to come back with. An order already paid at the
        till carries nothing, and the slip says so in as many words — a driver
        who is unsure asks the customer, and that is the awkward conversation
        this line exists to prevent. */
    get toCollect() {
        const due = this.order.get_due();
        return due > 0 ? due : 0;
    }

    /** A cash order shows what it is worth even once settled (owner, 2 Oct
        2026), so the driver can answer the customer at the door. */
    get cashValue() {
        const paidInCash = this.order.payment_ids.some(
            (pay) => pay.payment_method_id?.is_cash_count && pay.amount > 0);
        return paidInCash && !this.toCollect ? this.order.get_total_with_tax() : 0;
    }

    format(amount) {
        return this.env.utils.formatCurrency(amount);
    }

    print() {
        // The browser prints the slip on its own, the way the kitchen wall
        // screen prints: the till's own printer driver is set up for a receipt
        // roll, and a driver's slip is read on A5 in a van.
        const sheet = this.sheet.el;
        if (!sheet) {
            return;
        }
        const frame = document.createElement("iframe");
        frame.style.position = "fixed";
        frame.style.right = "0";
        frame.style.bottom = "0";
        frame.style.width = "0";
        frame.style.height = "0";
        frame.style.border = "0";
        document.body.appendChild(frame);
        const doc = frame.contentWindow.document;
        doc.open();
        doc.write(`<!doctype html><html><head><meta charset="utf-8"/>
            <title>Delivery Slip</title><style>${SukkarDeliverySlip.printStyle}</style>
            </head><body>${sheet.innerHTML}</body></html>`);
        doc.close();
        frame.contentWindow.focus();
        frame.contentWindow.print();
        setTimeout(() => frame.remove(), 1000);
    }
}

/** The slip's own stylesheet, carried into the print window with it: nothing
    of the register's styling reaches that window, so it travels here. */
SukkarDeliverySlip.printStyle = `
    * { box-sizing: border-box; }
    body { margin: 0; padding: 14px 16px; color: #111;
           font: 400 13px/1.45 'Roboto', system-ui, -apple-system, sans-serif; }
    .ks-slip-head { display: flex; justify-content: space-between;
                    align-items: flex-start; border-bottom: 2px solid #8E1650;
                    padding-bottom: 8px; margin-bottom: 10px; }
    .ks-slip-title { font-size: 19px; font-weight: 600; color: #8E1650; }
    .ks-slip-no { font-size: 26px; font-weight: 600; }
    .ks-slip-row { display: flex; gap: 10px; padding: 6px 0;
                   border-bottom: 1px solid #E6E0DA; }
    .ks-slip-label { width: 108px; flex: 0 0 108px; color: #6B5E55;
                     text-transform: uppercase; font-size: 10.5px;
                     letter-spacing: .06em; padding-top: 2px; }
    .ks-slip-value { flex: 1; font-size: 14px; }
    .ks-slip-big { font-size: 17px; font-weight: 600; }
    .ks-slip-items { width: 100%; border-collapse: collapse; margin-top: 4px; }
    .ks-slip-items td { padding: 4px 0; border-bottom: 1px solid #F0EAE4;
                        vertical-align: top; }
    .ks-slip-qty { width: 34px; font-weight: 600; }
    .ks-slip-note { color: #6B5E55; font-size: 12px; }
    .ks-slip-collect { margin-top: 10px; padding: 9px 12px; border-radius: 4px;
                       font-size: 17px; font-weight: 600; }
    .ks-slip-cash { background: #FDECEC; color: #B02A1F;
                    border: 1px solid #F3C9C4; }
    .ks-slip-paid { background: #EAF6EE; color: #1E7A43;
                    border: 1px solid #C5E6D2; }
    .ks-slip-sign { margin-top: 16px; display: flex; gap: 26px; }
    .ks-slip-sign div { flex: 1; border-top: 1px solid #B9AFA6; padding-top: 4px;
                        color: #6B5E55; font-size: 11px; }
    @page { size: A5; margin: 8mm; }
`;

patch(ReceiptScreen.prototype, {
    /** A delivery gets a slip; a collection does not. */
    get sukkarNeedsSlip() {
        const delivery = this.currentOrder?.sukkarDelivery || {};
        return delivery.sukkar_order_type === "delivery";
    },

    sukkarOpenSlip() {
        this.dialog.add(SukkarDeliverySlip, { order: this.currentOrder });
    },
});
