from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SukkarOversellWizard(models.TransientModel):
    """The manager's prompt when an order needs more pieces than are available.

    It exists so the override is never a silent click: a name, a reason and a
    timestamp are captured before the order is allowed through.
    """
    _name = "sukkar.oversell.wizard"
    _description = "Cake Stock Override Approval"

    sale_order_id = fields.Many2one("sale.order", string="Order")
    pos_order_id = fields.Many2one("pos.order", string="Register Order")
    qty_needed = fields.Float(
        string="Pieces Needed", digits="Product Unit of Measure",
        readonly=True)
    qty_available = fields.Float(
        string="Pieces Available", digits="Product Unit of Measure",
        readonly=True)
    manager_id = fields.Many2one(
        "res.users", string="Approving Manager",
        default=lambda self: self.env.user, required=True)
    reason = fields.Text(string="Reason", required=True)

    @api.model
    def default_get(self, fields_list):
        """Opened from the order itself (Actions ▸ Cake Stock Override), so the
        two numbers the manager is judging are already filled in."""
        res = super().default_get(fields_list)
        Batch = self.env["sukkar.cake.batch"]
        if self.env.context.get("active_model") == "sale.order":
            order = self.env["sale.order"].browse(self.env.context.get("active_id"))
            res.update({
                "sale_order_id": order.id,
                "qty_needed": Batch._sukkar_order_pieces(order),
                "qty_available": Batch._sukkar_available_pieces(order.company_id),
            })
        elif self.env.context.get("active_model") == "pos.order":
            pos_order = self.env["pos.order"].browse(self.env.context.get("active_id"))
            res.update({
                "pos_order_id": pos_order.id,
                "qty_available": Batch._sukkar_available_pieces(pos_order.company_id),
            })
        return res

    def action_confirm(self):
        """Write the approval down, then let the order through."""
        self.ensure_one()
        order = self.sale_order_id
        pos_order = self.pos_order_id
        if not order and not pos_order:
            raise UserError(_(
                "Open this from the order that was blocked."))
        company = (order.company_id or pos_order.company_id or self.env.company)
        self.env["sukkar.cake.override"].create({
            "sale_order_id": order.id,
            "pos_order_id": pos_order.id,
            "manager_id": self.manager_id.id,
            "reason": self.reason,
            "qty_needed": self.qty_needed,
            "qty_available": self.qty_available,
            "company_id": company.id,
        })
        if order:
            order.sukkar_oversell_approved = True
            if order.state in ("draft", "sent"):
                order.action_confirm()
        else:
            pos_order.sukkar_oversell_approved = True
        return {"type": "ir.actions.act_window_close"}
