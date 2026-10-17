from odoo import api, fields, models


class ProductProduct(models.Model):
    """How many cake pieces one unit of this product takes.

    The number is not typed in anywhere: it already lives in the recipe, as the
    quantity of the cake-piece component on the product's bill of materials
    (a 7-piece bouquet has 7). This surfaces it so the rest of the system can
    read it without exploding a BoM every time.
    """
    _inherit = "product.product"

    sukkar_piece_count = fields.Float(
        string="Cake Pieces per Unit", digits="Product Unit of Measure",
        compute="_compute_sukkar_piece_count",
        help="Read from this product's recipe: the quantity of the cake-piece "
             "component it consumes.")

    # The register leads with Arabic (owner's layout pick, 29 Sep 2026). Both
    # are read from the translations already written on the product and its
    # size, so they are edited where every other name is edited.
    sukkar_name_ar = fields.Char(
        string="Arabic Name", compute="_compute_sukkar_arabic",
        help="The product's Arabic name, read from its translation.")
    sukkar_variant_ar = fields.Char(
        string="Arabic Size", compute="_compute_sukkar_arabic",
        help="This variant's options (its size) in Arabic.")

    @api.depends("bom_ids")
    def _compute_sukkar_piece_count(self):
        piece = self.env.company.sukkar_cake_piece_product_id
        BoM = self.env["mrp.bom"].sudo()
        for product in self:
            if not piece:
                product.sukkar_piece_count = 0.0
                continue
            if product == piece:
                product.sukkar_piece_count = 1.0
                continue
            bom = BoM._bom_find(product).get(product)
            lines = bom and bom.bom_line_ids.filtered(
                lambda line: line.product_id == piece)
            product.sukkar_piece_count = (
                sum(lines.mapped("product_qty")) / bom.product_qty
                if lines and bom.product_qty else 0.0)

    @api.depends("name", "product_template_variant_value_ids")
    @api.depends_context("lang")
    def _compute_sukkar_arabic(self):
        arabic = self.env["res.lang"]._sukkar_arabic_code()
        if not arabic:
            self.sukkar_name_ar = self.sukkar_variant_ar = False
            return
        for product, product_ar in zip(self, self.with_context(lang=arabic)):
            # No Arabic written yet: the translation falls back to the English,
            # and the register would print the same name twice.
            product.sukkar_name_ar = (
                product_ar.name if product_ar.name != product.name else False)
            product.sukkar_variant_ar = ", ".join(
                product_ar.product_template_variant_value_ids.mapped("name")) or False
