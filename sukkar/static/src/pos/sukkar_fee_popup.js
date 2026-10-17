import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { sukkarApplyFee } from "@sukkar/pos/sukkar_fee";
import { _t } from "@web/core/l10n/translation";

/**
 * Overriding the delivery fee, one tap.
 *
 * The register normally fills the fee in by itself, from the price on the area
 * the address is in. This screen is for the times it has to be changed by
 * hand. The amounts offered are the shop's own delivery prices, listed in
 * Settings → Sukkar — never a list in the code — and the fee is charged on
 * its own product, so it stands on its own line on the receipt and the invoice.
 */
export class SukkarFeePopup extends Component {
    static template = "sukkar.FeePopup";
    static components = { Dialog };
    static props = { order: Object, close: Function };

    setup() {
        this.pos = usePos();
        this.state = useState({ error: "" });
    }

    get amounts() {
        const listed = this.pos.company.sukkar_pos_fee_amounts || "";
        return listed
            .split(",")
            .map((part) => Number(part.trim()))
            .filter((amount) => Number.isFinite(amount) && amount > 0)
            .sort((a, b) => a - b);
    }

    get current() {
        return (this.props.order.sukkarDelivery || {}).sukkar_delivery_fee || 0;
    }

    format(amount) {
        return this.env.utils.formatCurrency(amount);
    }

    async pick(amount) {
        if (!(await sukkarApplyFee(this.pos, this.props.order, amount))) {
            this.state.error = _t("No fee product yet — set one in Settings → Sukkar.");
            return;
        }
        this.props.close();
    }

    async remove() {
        await sukkarApplyFee(this.pos, this.props.order, 0);
        this.props.close();
    }
}
