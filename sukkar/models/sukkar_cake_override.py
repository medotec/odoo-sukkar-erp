from odoo import api, fields, models


class SukkarCakeOverride(models.Model):
    """The permanent record of a manager letting an order through anyway.

    Confirming an order that needs more pieces than are available is blocked.
    A manager may override it — and every override is written down here with
    who, when and why, so the owner can read them back later.
    """
    _name = "sukkar.cake.override"
    _description = "Cake Stock Override"
    _order = "override_datetime desc, id desc"

    name = fields.Char(default="New", copy=False, readonly=True, index=True)
    sale_order_id = fields.Many2one(
        "sale.order", string="Order", ondelete="set null", index=True)
    pos_order_id = fields.Many2one(
        "pos.order", string="Register Order", ondelete="set null", index=True)
    manager_id = fields.Many2one(
        "res.users", string="Approved By", required=True)
    override_datetime = fields.Datetime(
        string="Approved At", required=True,
        default=fields.Datetime.now, index=True)
    reason = fields.Text(string="Reason", required=True)
    qty_needed = fields.Float(
        string="Pieces Needed", digits="Product Unit of Measure")
    qty_available = fields.Float(
        string="Pieces Available", digits="Product Unit of Measure")
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, required=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "sukkar.cake.override") or "New"
        return super().create(vals_list)
