from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SukkarMarkPaidWizard(models.TransientModel):
    """Prompt shown by 'Mark Paid': pick KNET, Cash or Link Payment, and capture the
    KNET approval number / payment link reference — which is then stamped on the order (and flows to the
    invoice + documents)."""
    _name = "sukkar.mark.paid.wizard"
    _description = "Confirm Payment (method + KNET approval)"

    production_ids = fields.Many2many("sukkar.production")
    currency_id = fields.Many2one("res.currency", compute="_compute_summary")
    amount_total = fields.Monetary(
        compute="_compute_summary", currency_field="currency_id", string="Amount")
    order_summary = fields.Char(compute="_compute_summary", string="Order(s)")
    payment_method = fields.Selection(
        [("knet", "KNET"), ("cash", "Cash"), ("link", "Link Payment")],
        string="Payment Method", required=True, default="knet")
    knet_approval = fields.Char(string="KNET Approval No.")
    payment_reference = fields.Char(
        string="Payment Link Reference",
        help="Typed in by hand — there is no gateway integration to fetch it.")

    @api.depends("production_ids")
    def _compute_summary(self):
        for wiz in self:
            orders = wiz.production_ids.mapped("sale_order_id")
            wiz.order_summary = ", ".join(orders.mapped("name")) or "-"
            wiz.amount_total = sum(orders.mapped("amount_total"))
            wiz.currency_id = (orders[:1].currency_id or wiz.env.company.currency_id).id

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # Pre-select the order's existing method when it is already KNET/Cash.
        prod_cmd = self.env.context.get("default_production_ids")
        ids = prod_cmd[0][2] if (prod_cmd and isinstance(prod_cmd[0], (list, tuple))) else []
        if ids:
            method = self.env["sukkar.production"].browse(ids)[:1].sale_order_id.sukkar_payment_method
            if method in ("knet", "cash", "link"):
                res["payment_method"] = method
        return res

    def action_confirm(self):
        self.ensure_one()
        orders = self.production_ids.mapped("sale_order_id")
        if not orders:
            raise UserError(_("There is no order linked to this ticket to mark as paid."))
        if self.payment_method == "knet" and not (self.knet_approval or "").strip():
            raise UserError(_("Please enter the KNET approval number for a KNET payment."))
        if self.payment_method == "link" and not (self.payment_reference or "").strip():
            raise UserError(_("Please enter the payment link reference for a link payment."))
        vals = {
            "sukkar_paid": True,
            "sukkar_payment_method": self.payment_method,
            "sukkar_knet_approval": self.knet_approval.strip() if self.payment_method == "knet" else False,
            "sukkar_payment_reference": self.payment_reference.strip() if self.payment_method == "link" else False,
        }
        orders.sudo().write(vals)
        return {"type": "ir.actions.act_window_close"}
