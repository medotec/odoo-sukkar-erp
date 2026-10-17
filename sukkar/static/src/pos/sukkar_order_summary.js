import { patch } from "@web/core/utils/patch";
import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";
import { SukkarFlavourPopup } from "@sukkar/pos/sukkar_flavour_popup";

/**
 * The cart line's own controls (owner's UX rule, 30 Aug 2026: controls live
 * where the user expects them — on the thing itself): the ✕ that removes the
 * line, and the flavours of the cake it is made of. Patched on OrderSummary,
 * the cart's own renderer, so receipts and past-order views never grow either.
 */
patch(OrderSummary.prototype, {
    sukkarRemoveLine(line) {
        this.currentOrder.removeOrderline(line);
    },

    sukkarEditFlavours(line) {
        this.dialog.add(SukkarFlavourPopup, { line });
    },

    /** The pieces stopped adding up — the quantity moved after the cashier
        split the flavours by hand. The cart says so here, rather than letting
        the kitchen find out on the day. */
    sukkarFlavoursOff(line) {
        const pieces = line.sukkarPieces();
        if (!pieces) {
            return false;
        }
        const picked = line.sukkarFlavours().reduce((total, pick) => total + pick.qty, 0);
        return picked !== pieces;
    },
});
