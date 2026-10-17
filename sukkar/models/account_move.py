from odoo import api, fields, models


class AccountMove(models.Model):
    """Carry the gifting details onto the invoice so they print on it."""
    _inherit = "account.move"

    sukkar_has_real_tax = fields.Boolean(
        string="Carries Tax", compute="_compute_sukkar_has_real_tax",
        help="True only when the invoice actually charges tax. Kuwait has no "
             "VAT and the products carry a 0% tax, which still counts as a tax "
             "group — so the printed invoice has to ask about the money, not "
             "about whether a group exists.")

    @api.depends("tax_totals")
    def _compute_sukkar_has_real_tax(self):
        for move in self:
            totals = move.tax_totals or {}
            amount = totals.get("tax_amount_currency") or 0.0
            currency = move.currency_id or move.company_currency_id
            move.sukkar_has_real_tax = bool(
                totals.get("has_tax_groups")
                and not (currency or self.env.company.currency_id).is_zero(amount))

    sukkar_delivery_datetime = fields.Datetime(string="Delivery Date/Time")
    sukkar_delivery_time_range = fields.Char(string="Delivery Time Range")
    sukkar_receiver_name = fields.Char(string="Receiver Name")
    sukkar_receiver_phone = fields.Char(string="Receiver Phone")
    sukkar_gift_from = fields.Char(string="From")
    sukkar_gift_to = fields.Char(string="To")
    sukkar_gift_message = fields.Char(string="Gift Message")
    sukkar_special_requests = fields.Text(string="Special Requests")
    # "bank_link" retired 29 Aug 2026 — see sale_order.py; no invoice ever used it.
    sukkar_payment_method = fields.Selection(
        [("cod", "Cash on Delivery"),
         ("link", "Link Payment"),
         ("knet", "KNET"),
         ("cash", "Cash")],
        string="Payment Method")
    sukkar_knet_approval = fields.Char(string="KNET Approval No.")
    sukkar_payment_reference = fields.Char(string="Payment Link Reference")

    # === September 2026 batch ===============================================
    sukkar_order_type = fields.Selection(
        [("delivery", "Delivery"),
         ("pickup", "Pickup")],
        string="Delivery or Pickup")
    sukkar_driver_id = fields.Many2one(
        "res.users", string="Driver",
        domain="[('sukkar_is_driver', '=', True)]",
        help="Printed on the customer's invoice: who delivered the order.")
    sukkar_flavour_summary = fields.Text(string="Cake Flavours")
    sukkar_address_summary = fields.Char(
        string="Delivery Address (one line)",
        help="The whole delivery address on one line, as the order formatted it.")

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        moves._sukkar_copy_from_sale_order()
        return moves

    def _sukkar_copy_from_sale_order(self):
        """Take from the order whatever the invoice was not already given.

        sale.order._prepare_invoice fills these in on the normal path; this only
        fills what arrived blank, so the two can never disagree.
        """
        for move in self:
            order = move.line_ids.sale_line_ids.order_id[:1]
            if not order:
                continue
            vals = {}
            for field_name, value in (
                    ("sukkar_order_type", order.sukkar_order_type),
                    ("sukkar_driver_id", order.sukkar_driver_id.id),
                    ("sukkar_address_summary", order.sukkar_address_summary),
                    ("sukkar_flavour_summary", order.sukkar_flavour_summary)):
                if value and not move[field_name]:
                    vals[field_name] = value
            if vals:
                move.write(vals)
