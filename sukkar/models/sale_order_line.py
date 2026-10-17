from odoo import api, fields, models


class SaleOrderLine(models.Model):
    """Cake flavours and cake-piece count on one order line."""
    _inherit = "sale.order.line"

    sukkar_flavour_line_ids = fields.One2many(
        "sukkar.flavour.line", "sale_line_id", string="Cake Flavours",
        copy=True)
    # The picture the till's Internal Note box put on this item (owner,
    # 10 Oct 2026). The kitchen sees it beside the note.
    sukkar_note_image = fields.Image(
        string="Note Image", max_width=1920, max_height=1920)
    sukkar_flavour_summary = fields.Char(
        string="Flavours", compute="_compute_sukkar_flavour_summary",
        store=True,
        help="One readable line, e.g. '4 Red Velvet + 3 Chocolate'. Printed on "
             "the invoice and shown on the kitchen screen.")
    sukkar_piece_qty = fields.Float(
        string="Cake Pieces", digits="Product Unit of Measure",
        compute="_compute_sukkar_piece_qty",
        help="Pieces this line takes out of stock: the product's piece count "
             "times the quantity ordered.")

    @api.depends("sukkar_flavour_line_ids.flavour_id",
                 "sukkar_flavour_line_ids.qty")
    def _compute_sukkar_flavour_summary(self):
        for line in self:
            line.sukkar_flavour_summary = \
                line.sukkar_flavour_line_ids._sukkar_summary()

    @api.depends("product_id", "product_uom_qty")
    def _compute_sukkar_piece_qty(self):
        for line in self:
            product = line.product_id.with_company(
                line.company_id or self.env.company)
            line.sukkar_piece_qty = product.sukkar_piece_count * line.product_uom_qty

    # --- Cake pieces follow the line -----------------------------------------
    # A confirmed order is edited every day: a bouquet is dropped, another is
    # added, a quantity is corrected. The pieces it holds have to follow, or the
    # shop is either sitting on cake it can never sell or promising cake twice.
    def _sukkar_resync_pieces(self):
        orders = self.mapped("order_id").filtered(lambda o: o.state == "sale")
        if orders:
            self.env["sukkar.cake.batch"]._sukkar_sync_reservations(orders)

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._sukkar_resync_pieces()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if {"product_uom_qty", "product_id"} & set(vals):
            self._sukkar_resync_pieces()
        return res

    def unlink(self):
        orders = self.mapped("order_id")
        res = super().unlink()
        orders.filtered(lambda o: o.state == "sale")._sukkar_release_pieces()
        return res

    def _action_launch_stock_rule(self, previous_product_uom_qty=False):
        """A counter sale has already left the shop in the customer's hands.

        The register raises its own stock move on validation; letting the mirror
        order raise a second delivery order leaves one permanently open transfer
        per till sale and the same cakes waiting to leave inventory twice.
        """
        lines = self.filtered(lambda l: not l.order_id.sukkar_pos_order_id)
        if not lines:
            return True
        return super(SaleOrderLine, lines)._action_launch_stock_rule(
            previous_product_uom_qty=previous_product_uom_qty)

    def _prepare_invoice_line(self, **optional_values):
        # The flavours are what the customer ordered, so they belong on the
        # line of the invoice that charges for it.
        vals = super()._prepare_invoice_line(**optional_values)
        vals["sukkar_flavour_summary"] = self.sukkar_flavour_summary
        return vals
