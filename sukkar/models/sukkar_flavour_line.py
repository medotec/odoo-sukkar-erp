import json

from odoo import api, fields, models


class SukkarFlavourLine(models.Model):
    """How many cake pieces of each flavour one order line carries.

    One model for both worlds: a back-office sale order line and a register
    line point at the same structure, so the invoice, the receipt and the
    kitchen ticket all read one shape.
    """
    _name = "sukkar.flavour.line"
    _description = "Cake Flavour Line"
    _order = "sale_line_id, pos_line_id, id"

    flavour_id = fields.Many2one(
        "sukkar.cake.flavour", string="Flavour",
        required=True, ondelete="restrict")
    qty = fields.Integer(string="Pieces", default=1, required=True)
    sale_line_id = fields.Many2one(
        "sale.order.line", string="Order Line", ondelete="cascade", index=True)
    pos_line_id = fields.Many2one(
        "pos.order.line", string="Register Line", ondelete="cascade", index=True)
    order_id = fields.Many2one(
        "sale.order", related="sale_line_id.order_id", store=True, index=True)
    product_id = fields.Many2one(
        "product.product", related="sale_line_id.product_id", string="Item")
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company)

    def _sukkar_summary(self):
        """These picks as one readable line: '4 Red Velvet'.

        A bouquet made of one flavour throughout says the flavour and nothing
        else — the quantity is already the bouquet's size. Split flavours say
        how many pieces of each, which is what the kitchen has to count out.
        """
        picks = self.filtered(lambda line: line.flavour_id and line.qty > 0)
        if not picks:
            return False
        if len(picks) == 1:
            return picks.flavour_id._sukkar_label()
        return " + ".join(
            "%d %s" % (line.qty, line.flavour_id._sukkar_label()) for line in picks)

    @api.model
    def _sukkar_picks_from_json(self, raw):
        """The register's raw picks — [{"flavour_id": 3, "qty": 4}, …] — as
        create values, with anything unrecognisable dropped rather than fatal.

        A till that cannot post its order is a till that cannot sell, so a
        flavour that has since been deleted costs the shop the flavours on that
        line and nothing more.
        """
        try:
            picks = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return []
        if not isinstance(picks, list):
            return []
        vals = []
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            try:
                flavour_id = int(pick.get("flavour_id") or 0)
                qty = int(pick.get("qty") or 0)
            except (TypeError, ValueError):
                continue
            flavour = self.env["sukkar.cake.flavour"].browse(flavour_id).exists()
            if flavour and qty > 0:
                vals.append({"flavour_id": flavour.id, "qty": qty})
        return vals
