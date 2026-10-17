import { Component, useState, onWillStart } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { _t } from "@web/core/l10n/translation";

/**
 * The cake flavours of one cart line: the whole bouquet in one flavour (tap the
 * flavour), or a few of each (4 Red Velvet + 3 Chocolate, using − and +). The
 * pieces have to add up to what the bouquet holds, so the running count is
 * always on screen and a split that does not add up is refused.
 *
 * The flavour list is configuration — it is loaded with the register from
 * sukkar.cake.flavour, never hard-coded — and so is the number of pieces,
 * which is read off the bouquet's recipe.
 */
export class SukkarFlavourPopup extends Component {
    static template = "sukkar.FlavourPopup";
    static components = { Dialog };
    static props = { line: Object, close: Function };

    setup() {
        this.pos = usePos();
        this.state = useState({ pieces: 0, qty: {}, error: "" });
        onWillStart(async () => {
            const line = this.props.line;
            line.sukkarPiecesPerUnit = await this.pos.sukkarPiecesPerUnit(line.product_id);
            this.state.pieces = line.sukkarPieces();
            const picks = line.sukkarFlavours();
            for (const pick of picks) {
                this.state.qty[pick.flavour_id] = pick.qty;
            }
            // One flavour for the whole bouquet stays whole, even if the
            // quantity moved after it was picked.
            if (picks.length === 1 && this.state.pieces) {
                this.state.qty[picks[0].flavour_id] = this.state.pieces;
            }
        });
    }

    get flavours() {
        return this.pos.models["sukkar.cake.flavour"]?.getAll() || [];
    }

    get picked() {
        return Object.values(this.state.qty).reduce((total, qty) => total + qty, 0);
    }

    qtyOf(flavour) {
        return this.state.qty[flavour.id] || 0;
    }

    /** The whole bouquet in this one flavour — the answer the shop gives most. */
    only(flavour) {
        this.state.qty = { [flavour.id]: this.state.pieces || this.picked || 1 };
        this.state.error = "";
    }

    step(flavour, by) {
        const wanted = this.qtyOf(flavour) + by;
        const room = this.state.pieces
            ? this.state.pieces - this.picked + this.qtyOf(flavour)
            : wanted;
        this.state.qty[flavour.id] = Math.max(0, Math.min(wanted, room));
        this.state.error = "";
    }

    confirm() {
        if (!this.picked) {
            this.state.error = _t("Pick a flavour first.");
            return;
        }
        if (this.state.pieces && this.picked !== this.state.pieces) {
            this.state.error = _t("The pieces have to add up to %s.", this.state.pieces);
            return;
        }
        this.props.line.sukkarSetFlavours(
            Object.entries(this.state.qty).map(([flavourId, qty]) => ({
                flavour_id: Number(flavourId),
                qty,
            })));
        this.props.close();
    }
}
