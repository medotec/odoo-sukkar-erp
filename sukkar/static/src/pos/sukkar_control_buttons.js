import { patch } from "@web/core/utils/patch";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { SukkarOrderTypePopup } from "@sukkar/pos/sukkar_order_type_popup";
import { _t } from "@web/core/l10n/translation";
import { TextInputPopup } from "@point_of_sale/app/utils/input_popups/text_input_popup";
import { OrderlineNoteButton } from "@point_of_sale/app/screens/product_screen/control_buttons/customer_note_button/customer_note_button";
import { SukkarNotePopup } from "@sukkar/pos/sukkar_note_popup";
import { makeAwaitable } from "@point_of_sale/app/store/make_awaitable_dialog";

/**
 * The Delivery / Pickup button — opens the screen that settles what kind of
 * order this is, when it is due, and (for a delivery) where it goes. Payment
 * asks for the same screen if the cashier has not been there yet.
 * (Removing a line is per-line ✕ on the cart itself: sukkar_order_summary.)
 */
patch(ControlButtons.prototype, {
    sukkarOpenOrderType() {
        const order = this.pos.get_order();
        if (!order) {
            return;
        }
        this.dialog.add(SukkarOrderTypePopup, { order });
    },

    sukkarOrderType() {
        return (this.pos.get_order()?.sukkarDelivery || {}).sukkar_order_type || "";
    },

    // The tile says what was chosen, with a tick, or asks the question.
    sukkarOrderTypeLabel() {
        const orderType = this.sukkarOrderType();
        if (orderType === "delivery") {
            return _t("Delivery ✓");
        }
        if (orderType === "pickup") {
            return _t("Pickup ✓");
        }
        return _t("Delivery / Pickup");
    },

    /** The number pad (price and discount) lives behind More now: the − and +
        on each line do the everyday quantity work. It opens on the line being
        worked on, or the last one added. */
    sukkarOpenNumpad() {
        const order = this.pos.get_order();
        if (order && !order.get_selected_orderline() && order.lines.length) {
            this.pos.selectOrderLine(order, order.lines.at(-1));
        }
        this.pos.sukkarNumpadOpen = true;
    },

    /** The General Note tile (owner, 2 Oct 2026): a note on the whole order,
        which used to hide behind More. Unlike the Note tile it needs no line
        picked, so it works on an empty ticket too. It reaches the kitchen
        screen, the kitchen ticket and the driver receipt. */
    sukkarGeneralNote() {
        return this.pos.get_order()?.general_note || "";
    },

    async sukkarEditGeneralNote() {
        const order = this.pos.get_order();
        if (!order) {
            return;
        }
        const current = order.general_note || "";
        const payload = await makeAwaitable(this.dialog, TextInputPopup, {
            title: _t("Add General Note"),
            buttons: this.pos.models["pos.note"].getAll().map((note) => ({
                label: note.name,
                isSelected: current.split("\n").includes(note.name),
            })),
            rows: 4,
            startingValue: current,
        });
        if (typeof payload === "string") {
            order.general_note = payload;
        }
    },

    sukkarOrderTypeIcon() {
        const orderType = this.sukkarOrderType();
        if (orderType === "delivery") {
            return "fa fa-motorcycle";
        }
        if (orderType === "pickup") {
            return "fa fa-shopping-bag";
        }
        return "fa fa-calendar-check-o";
    },
});

/**
 * The Internal Note box takes a picture too (owner, 10 Oct 2026 — moved here
 * from General Note the same day). The picture belongs to the line the note
 * is on, so two bouquets can each carry their own design. Odoo's own flow
 * (Apply, Discard, splitting a line already sent) is left as it is: only the
 * box changes, and it still hands back the note text.
 */
patch(OrderlineNoteButton.prototype, {
    async openTextInput(selectedNote) {
        if (!this._isInternalNote()) {
            return super.openTextInput(selectedNote);
        }
        const line = this.pos.get_order()?.get_selected_orderline();
        const payload = await makeAwaitable(this.dialog, SukkarNotePopup, {
            title: _t("Add %s", this.props.label),
            buttons: this.pos.models["pos.note"].getAll().map((note) => ({
                label: note.name,
                isSelected: (selectedNote || "").split("\n").includes(note.name),
            })),
            rows: 4,
            startingValue: selectedNote || "",
            startingImage: line?.sukkar_note_image || "",
        });
        if (!payload) {
            return payload;
        }
        if (line) {
            line.sukkar_note_image = payload.image || false;
        }
        return payload.note;
    },
});
