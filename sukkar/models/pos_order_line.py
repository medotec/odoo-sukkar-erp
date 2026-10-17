from odoo import api, fields, models


class PosOrderLine(models.Model):
    """Cake flavours on a register line.

    The register sends `sukkar_flavour_json` — the raw picks, e.g.
    ``[{"flavour_id": 3, "qty": 4}, {"flavour_id": 1, "qty": 3}]`` — and the
    server turns them into real flavour lines. The summary rides along so the
    receipt can print without a second round trip.
    """
    _inherit = "pos.order.line"

    sukkar_flavour_json = fields.Char(
        string="Flavours (raw)", copy=False,
        help="What the register picked, as sent. The readable version is "
             "Flavours; the structured version is Cake Flavours.")
    sukkar_flavour_summary = fields.Char(string="Flavours", copy=False)
    # The picture from the Internal Note box (owner, 10 Oct 2026) — picked
    # from the till PC's files and made small on the till before it is sent.
    sukkar_note_image = fields.Image(
        string="Note Image", max_width=1920, max_height=1920, copy=False)
    sukkar_flavour_line_ids = fields.One2many(
        "sukkar.flavour.line", "pos_line_id", string="Cake Flavours")
    sukkar_piece_qty = fields.Float(
        string="Cake Pieces", digits="Product Unit of Measure",
        compute="_compute_sukkar_piece_qty")

    @api.depends("product_id", "qty")
    def _compute_sukkar_piece_qty(self):
        for line in self:
            product = line.product_id.with_company(
                line.company_id or self.env.company)
            line.sukkar_piece_qty = product.sukkar_piece_count * line.qty

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._sukkar_sync_flavour_lines()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if "sukkar_flavour_json" in vals:
            self._sukkar_sync_flavour_lines()
        return res

    def _sukkar_sync_flavour_lines(self):
        """Turn the register's raw picks into real flavour lines.

        Until this runs the flavours exist only as text the till sent; after it
        they are records the kitchen ticket, the delivery order and the invoice
        all read the same way as a back-office order's.
        """
        Flavour = self.env["sukkar.flavour.line"]
        for line in self.filtered(
                lambda l: l.sukkar_flavour_json or l.sukkar_flavour_line_ids):
            picks = Flavour._sukkar_picks_from_json(line.sukkar_flavour_json)
            commands = [(5, 0, 0)] + [(0, 0, vals) for vals in picks]
            line.sukkar_flavour_line_ids = commands
            if not line.sukkar_flavour_summary:
                line.sukkar_flavour_summary = \
                    line.sukkar_flavour_line_ids._sukkar_summary()
