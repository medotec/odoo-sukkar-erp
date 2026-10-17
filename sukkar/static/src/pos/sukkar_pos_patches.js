import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { TextInputPopup } from "@point_of_sale/app/utils/input_popups/text_input_popup";
import { makeAwaitable } from "@point_of_sale/app/store/make_awaitable_dialog";
import { SukkarOrderTypePopup } from "@sukkar/pos/sukkar_order_type_popup";
import { _t } from "@web/core/l10n/translation";

/**
 * Small, separate behaviours — kept in one patch file so the register's
 * Sukkar behaviour has a single place to read:
 *
 * 1. Orders carry their delivery / pickup details to the server (serialize).
 * 2. Receipts carry customer / receiver / delivery / flavours / loyalty data.
 * 3. Adding a greeting card asks for the card text right away, and the text
 *    box stops at the length the card can actually hold.
 * 4. Adding a cake bouquet gets the shop's default flavour, so the usual
 *    order is one tap; changing the quantity keeps a one-flavour bouquet right.
 * 5. Nothing reaches payment without delivery or pickup, its day and window.
 */

patch(PosOrder.prototype, {
    // 0 · The delivery choice is kept on the order itself, in one field, because
    //     that is the only thing the till saves in the browser's own database
    //     between reloads: it re-saves an unpaid order when a real field
    //     changes, and ignores anything else. Reading and writing still go
    //     through sukkarDelivery / sukkarSetDelivery, so no screen had to
    //     change. (Owner's stop, 29 Sep 2026.)
    get sukkarDelivery() {
        try {
            return JSON.parse(this.sukkar_delivery_state || "{}");
        } catch {
            return {};
        }
    },

    // A driver, a recipient and an address belong to the order for the
    // customer they were picked with, never to the next one (owner, 2 Oct
    // 2026): changing the customer on a ticket wipes them, and keeps the day,
    // the window and delivery-or-pickup.
    set_partner(partner) {
        const before = this.get_partner()?.id || false;
        const result = super.set_partner(...arguments);
        if ((partner?.id || false) !== before && this.sukkar_delivery_state) {
            this.sukkarSetDelivery({
                sukkar_driver_id: false,
                sukkar_recipient_id: false,
                sukkar_address_id: false,
                sukkar_receiver_name: "",
                sukkar_receiver_phone: "",
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
            });
        }
        return result;
    },

    // The one place those details are written. A fresh object every time, so
    // the screens reading the order redraw themselves.
    sukkarSetDelivery(vals) {
        const delivery = { ...this.sukkarDelivery, ...vals };
        this.update({ sukkar_delivery_state: JSON.stringify(delivery) });
        return delivery;
    },

    // 1 · Delivery details ride along with the order payload; every key here is
    //     a real pos.order field, so _process_order stores them as they are.
    serialize(options = {}) {
        const data = super.serialize(...arguments);
        if (options.orm) {
            Object.assign(data, this.sukkarDelivery);
        }
        return data;
    },

    // 5 · What is still unanswered before this order may be paid.
    sukkarScheduleMissing() {
        const delivery = this.sukkarDelivery;
        if (!delivery.sukkar_order_type
                || !delivery.sukkar_delivery_date
                || !delivery.sukkar_delivery_slot_id) {
            return true;
        }
        // A delivery nobody can be called about is a parcel with no owner.
        return delivery.sukkar_order_type === "delivery"
            && !(delivery.sukkar_receiver_name && delivery.sukkar_receiver_phone);
    },

    // 2 · Everything the receipt prints beyond the standard content.
    export_for_printing(baseUrl, headerData) {
        const data = super.export_for_printing(...arguments);
        const partner = this.get_partner();
        const delivery = this.sukkarDelivery;
        // Reward points only. A gift card is money on a card — one gift-card
        // point is a whole dinar, one reward point is 50 fils — so adding the
        // two together printed a points balance the shop never owed.
        const cards = partner
            ? (this.models["loyalty.card"] || []).filter(
                  (card) => card.partner_id?.id === partner.id
                      && card.program_id?.program_type === "loyalty")
            : [];
        const address = [
            delivery.sukkar_addr_area,
            delivery.sukkar_addr_block && _t("Block %s", delivery.sukkar_addr_block),
            delivery.sukkar_addr_street && _t("Street %s", delivery.sukkar_addr_street),
            delivery.sukkar_addr_avenue && _t("Avenue %s", delivery.sukkar_addr_avenue),
            delivery.sukkar_addr_building,
            delivery.sukkar_addr_floor && _t("Floor %s", delivery.sukkar_addr_floor),
            delivery.sukkar_addr_apartment && _t("Apartment %s", delivery.sukkar_addr_apartment),
        ].filter(Boolean).join(", ");
        data.sukkar = {
            customerName: partner ? partner.name : "",
            customerPhone: partner ? (partner.mobile || partner.phone || "") : "",
            senderName: delivery.sukkar_sender_name || "",
            senderPhone: delivery.sukkar_sender_phone || "",
            receiverName: delivery.sukkar_receiver_name || "",
            receiverPhone: delivery.sukkar_receiver_phone || "",
            orderType: delivery.sukkar_order_type || "",
            deliveryDate: delivery.sukkar_delivery_date || "",
            address,
            flavours: this.lines
                .map((line) => line.sukkar_flavour_summary)
                .filter(Boolean)
                .join(" / "),
            loyaltyPoints: cards.length
                ? cards.reduce((total, card) => total + (card.points || 0), 0)
                : null,
            // The receipt is printed in Arabic (30 Sep 2026), so it carries its
            // own copy of the lines with the Arabic names. The register screen
            // is untouched — it stays English (LOCKED #9).
            lines: this.lines.map((line) => ({
                name: line.product_id?.sukkar_name_ar || line.product_id?.name || "",
                variant: line.product_id?.sukkar_variant_ar || "",
                qty: line.get_quantity_str(),
                unitPrice: line.get_unit_display_price(),
                total: line.get_display_price(),
                flavours: line.sukkar_flavour_summary || "",
                note: line.get_customer_note() || "",
            })),
            // What is still owed on this order: nothing on an ordinary counter
            // sale, the whole amount on one that leaves unpaid.
            owed: Math.max(
                0,
                this.get_total_with_tax() - this.get_total_paid()),
        };
        return data;
    },
});

patch(PosOrderline.prototype, {
    // 4 · One flavour for the whole bouquet follows the quantity by itself. A
    //     split the cashier made by hand does not — the cart flags it instead,
    //     because only he knows how the new pieces should be divided.
    set_quantity(quantity, keep_price) {
        const result = super.set_quantity(...arguments);
        const flavours = this.sukkarFlavours();
        // A quantity of zero is a keystroke on the way to another number
        // (Backspace empties the buffer before the next digit arrives). Wiping
        // the flavour here left the bouquet blank, and the in-cart flavour
        // control disappears with the summary, so the cashier could never get
        // it back.
        if (this.sukkarPieces() <= 0) {
            return result;
        }
        if (flavours.length === 1 && this.sukkarPiecesPerUnit) {
            this.sukkarSetFlavours([{
                flavour_id: flavours[0].flavour_id,
                qty: this.sukkarPieces(),
            }]);
        }
        return result;
    },

    // The pieces this line is made of: the recipe's count times the quantity.
    sukkarPieces() {
        return Math.round((this.sukkarPiecesPerUnit || 0) * (this.qty || 0));
    },

    sukkarFlavours() {
        try {
            return JSON.parse(this.sukkar_flavour_json || "[]");
        } catch {
            return [];
        }
    },

    // Both fields are written together, always: the picks the server turns into
    // flavour lines, and the readable version the receipt prints.
    sukkarSetFlavours(picks) {
        const flavours = this.models["sukkar.cake.flavour"];
        const chosen = picks.filter((pick) => pick.qty > 0);
        this.sukkar_flavour_json = chosen.length ? JSON.stringify(chosen) : false;
        this.sukkar_flavour_summary = chosen
            .map((pick) => {
                const flavour = flavours?.get(pick.flavour_id);
                if (!flavour) {
                    return "";
                }
                // The register is English only since 29 Sep, so the note the
                // kitchen reads is the flavour's English name and nothing else.
                const label = flavour.name;
                return chosen.length === 1 ? label : `${pick.qty} ${label}`;
            })
            .filter(Boolean)
            .join(" + ") || false;
    },
});

// 3 · The card text is printed on a real card: past the length the shop set it
//     no longer fits, so the box stops taking characters and counts down. The
//     limit is optional, so every other text box in the register is untouched.
patch(TextInputPopup.prototype, {
    get sukkarRemaining() {
        return this.props.sukkarMaxLength - (this.state.inputValue || "").length;
    },
});
TextInputPopup.props = {
    ...TextInputPopup.props,
    sukkarMaxLength: { type: Number, optional: true },
};

patch(PosStore.prototype, {
    // 3 · A greeting card without its text is a mistake waiting to be wrapped:
    //     the text box opens the moment one lands in the cart. The text is the
    //     line's customer note, so it prints on the receipt and follows the
    //     order into the kitchen pipeline.
    // 4 · A cake bouquet leaves with the shop's default flavour on it.
    async addLineToCurrentOrder(vals, opts = {}, configure = true) {
        const line = await super.addLineToCurrentOrder(vals, opts, configure);
        const categories = line?.product_id?.pos_categ_ids || [];
        // Cards and toppers are two tabs since 30 Sep 2026. Only a card is
        // written on; a topper is stuck on the cake and carries no message.
        const isGreetingCard = categories.some((category) =>
            (category.name || "").trim() === "Cards" ||
            (category.name || "").includes("كروت"));
        if (isGreetingCard && !line.get_customer_note()) {
            const limit = this.company.sukkar_card_message_max;
            const text = await makeAwaitable(this.env.services.dialog, TextInputPopup, {
                title: _t("Card text"),
                placeholder: _t("What should the card say?"),
                rows: 3,
                sukkarMaxLength: limit > 0 ? limit : undefined,
            });
            if (text) {
                line.set_customer_note(text);
            }
        }
        await this.sukkarApplyDefaultFlavour(line);
        return line;
    },

    // 5 · Delivery or pickup, the day and the window, before any money.
    async pay() {
        const order = this.get_order();
        if (order && !order.is_empty() && !order._isRefundOrder()
                && order.sukkarScheduleMissing()) {
            const answered = await makeAwaitable(this.dialog, SukkarOrderTypePopup, { order });
            if (!answered) {
                return;
            }
        }
        return await super.pay(...arguments);
    },

    // How many cake pieces one unit of a product is. The number lives on the
    // recipe, never in the code, and is read once per product per session. A
    // register that cannot reach the server keeps selling: it simply adds the
    // line without flavours rather than refusing it.
    async sukkarPiecesPerUnit(product) {
        if (!product) {
            return 0;
        }
        if (!this.sukkarPieceCounts) {
            this.sukkarPieceCounts = {};
        }
        if (!(product.id in this.sukkarPieceCounts)) {
            try {
                const [data] = await this.env.services.orm.read(
                    "product.product", [product.id], ["sukkar_piece_count"]);
                this.sukkarPieceCounts[product.id] = data?.sukkar_piece_count || 0;
            } catch {
                return 0;
            }
        }
        return this.sukkarPieceCounts[product.id];
    },

    // The delivery windows, fetched once and kept: every order now asks for
    // one, and the counter should not wait for the server each time.
    async sukkarSlots() {
        if (!this.sukkarDeliverySlots) {
            try {
                this.sukkarDeliverySlots = await this.env.services.orm.searchRead(
                    "sukkar.delivery.slot", [], ["name"]);
            } catch {
                return [];
            }
        }
        return this.sukkarDeliverySlots;
    },

    sukkarDefaultFlavour() {
        const flavours = this.models["sukkar.cake.flavour"];
        return this.company.sukkar_default_flavour_id
            || flavours?.get(this.company.raw?.sukkar_default_flavour_id)
            || false;
    },

    async sukkarApplyDefaultFlavour(line) {
        if (!line || line.sukkar_flavour_json) {
            return;
        }
        const pieces = await this.sukkarPiecesPerUnit(line.product_id);
        if (!pieces) {
            return;  // not made of cake pieces: nothing to flavour
        }
        line.sukkarPiecesPerUnit = pieces;
        const flavour = this.sukkarDefaultFlavour();
        if (flavour) {
            line.sukkarSetFlavours([{ flavour_id: flavour.id, qty: line.sukkarPieces() }]);
        }
    },
});
