import { Component, useState, onWillStart } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { serializeDate } from "@web/core/l10n/dates";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { SukkarRecipientPopup } from "@sukkar/pos/sukkar_recipient_popup";
import { SukkarFeePopup } from "@sukkar/pos/sukkar_fee_popup";
import { _t } from "@web/core/l10n/translation";

const { DateTime } = luxon;

/**
 * Every sale is either a delivery or a collection, and both are appointments:
 * the register asks for the choice, the day and the window, and will not take
 * payment until it has all three. A pickup asks for no address and no driver.
 *
 * The address, the receiver and the fee sit behind their own buttons, so this
 * stays one short screen on the counter's tablet.
 */
export class SukkarOrderTypePopup extends Component {
    static template = "sukkar.OrderTypePopup";
    static components = { Dialog };
    static props = {
        order: Object,
        getPayload: { type: Function, optional: true },
        close: Function,
    };

    setup() {
        this.pos = usePos();
        this.dialog = useService("dialog");
        const saved = this.props.order.sukkarDelivery || {};
        this.state = useState({
            slots: [],
            order_type: saved.sukkar_order_type || "",
            date: saved.sukkar_delivery_date || "",
            slot_id: saved.sukkar_delivery_slot_id || 0,
            receiver: "",
            fee: 0,
            error: "",
        });
        this.readBack();
        onWillStart(async () => {
            this.state.slots = await this.pos.sukkarSlots();
            if (!this.state.slots.length) {
                this.state.error = _t("The delivery windows could not be loaded.");
            }
        });
    }

    /** What the address and fee popups left on the order, shown on their buttons. */
    readBack() {
        const saved = this.props.order.sukkarDelivery || {};
        const who = [
            saved.sukkar_receiver_name,
            saved.sukkar_recipient_id ? _t("(gift)") : "",
        ].filter(Boolean).join(" ");
        const where = [
            saved.sukkar_addr_area,
            saved.sukkar_addr_block && _t("Block %s", saved.sukkar_addr_block),
            saved.sukkar_addr_street && _t("Street %s", saved.sukkar_addr_street),
        ].filter(Boolean).join(", ");
        this.state.receiver = [who, where].filter(Boolean).join(" — ");
        this.state.fee = saved.sukkar_delivery_fee || 0;
    }

    get feeLabel() {
        return this.state.fee
            ? this.env.utils.formatCurrency(this.state.fee)
            : _t("No fee yet");
    }

    choose(orderType) {
        this.state.order_type = orderType;
        this.state.error = "";
    }

    setDate(daysAhead) {
        this.state.date = serializeDate(DateTime.now().plus({ days: daysAhead }));
    }

    /** Who receives it and where — the screen that saves a recipient under the
        customer, so the same mother or office is two taps next time. */
    openRecipient() {
        this.dialog.add(
            SukkarRecipientPopup,
            { order: this.props.order },
            { onClose: () => this.readBack() });
    }

    openFee() {
        this.dialog.add(
            SukkarFeePopup,
            { order: this.props.order },
            { onClose: () => this.readBack() });
    }

    /** A collection is not driven anywhere, so it carries no delivery fee.
        The fee button disappears the moment Pickup is tapped, so the cashier
        cannot take it off from this screen — it has to come off by itself. */
    dropDeliveryFee() {
        const company = this.pos.company;
        const productId = company.sukkar_pos_fee_product_id?.id
            || company.raw?.sukkar_pos_fee_product_id;
        if (!productId) {
            return;
        }
        const line = this.props.order.lines.find(
            (l) => l.product_id?.id === productId);
        if (line) {
            this.props.order.removeOrderline(line);
        }
    }

    confirm() {
        const s = this.state;
        if (!s.order_type) {
            s.error = _t("Choose Delivery or Pickup first.");
            return;
        }
        if (!s.date || !s.slot_id) {
            s.error = _t("The day and the time window are needed for both.");
            return;
        }
        const saved = this.props.order.sukkarDelivery || {};
        if (s.order_type === "delivery" && !saved.sukkar_address_id) {
            s.error = _t("Open Recipient: a delivery needs someone to receive it and an address.");
            return;
        }
        const vals = {
            sukkar_order_type: s.order_type,
            sukkar_delivery_date: s.date,
            sukkar_delivery_slot_id: Number(s.slot_id),
        };
        if (s.order_type === "pickup") {
            // Collected at the branch: no address to drive to, nobody to drive
            // it, and nothing to charge for driving it.
            this.dropDeliveryFee();
            Object.assign(vals, {
                sukkar_delivery_fee: 0,
                sukkar_recipient_id: false,
                sukkar_address_id: false,
                sukkar_addr_governorate_id: false,
                sukkar_addr_area: "",
                sukkar_addr_block: "",
                sukkar_addr_street: "",
                sukkar_addr_avenue: "",
                sukkar_addr_building: "",
                sukkar_addr_floor: "",
                sukkar_addr_apartment: "",
                sukkar_addr_directions: "",
                sukkar_delivery_note: "",
                sukkar_driver_id: false,
            });
        }
        this.props.order.sukkarSetDelivery(vals);
        this.props.getPayload?.(true);
        this.props.close();
    }
}
