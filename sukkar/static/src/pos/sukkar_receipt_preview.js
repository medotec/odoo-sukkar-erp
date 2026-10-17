import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { patch } from "@web/core/utils/patch";
import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";

/**
 * An earlier order's receipt, shown before anything is printed.
 *
 * The orders list used to have a bare "Print Receipt" button that sent the
 * slip straight to the printer. The owner wanted to see it first and to be
 * able to keep it as a file (30 Sep 2026): so the button now opens the slip
 * exactly as it prints, with Print and Download PDF underneath.
 *
 * The PDF is made on the server from the same order, receipt-sized, so it is
 * a real file that can be sent to a customer — not a screenshot.
 */
export class SukkarReceiptPreview extends Component {
    static template = "sukkar.ReceiptPreview";
    static components = { Dialog, OrderReceipt };
    static props = { order: Object, close: Function };

    setup() {
        this.pos = usePos();
    }

    get receiptData() {
        return this.pos.orderExportForPrinting(this.props.order);
    }

    async print() {
        await this.pos.printReceipt({ order: this.props.order });
    }

    downloadPdf() {
        const id = this.props.order.id;
        if (typeof id !== "number") {
            // Not on the server yet, so there is nothing to make a file from.
            return;
        }
        window.open(`/report/pdf/sukkar.report_pos_receipt_ar/${id}`, "_blank");
    }

    get canDownload() {
        return typeof this.props.order.id === "number";
    }
}

patch(TicketScreen.prototype, {
    sukkarShowReceipt(order) {
        this.dialog.add(SukkarReceiptPreview, { order });
    },
});
