from odoo import api, fields, models


class SukkarCakeFlavour(models.Model):
    """The cake flavours a bouquet can be made of — admin-editable, never code."""
    _name = "sukkar.cake.flavour"
    _description = "Cake Flavour"
    _inherit = ["pos.load.mixin"]
    _order = "sequence, id"

    name = fields.Char(string="Flavour", required=True, translate=True)
    name_ar = fields.Char(string="Arabic Name")
    code = fields.Char(string="Short Code",
                       help="Two or three letters for the kitchen ticket, e.g. RV.")
    sequence = fields.Integer(default=10)
    is_mixed = fields.Boolean(
        string="Mixed Flavours",
        help="Tick on the catch-all 'Mixed Cake Flavours' entry — the one used "
             "when nobody chose a flavour by hand.")
    color = fields.Integer(string="Colour")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company)

    def _sukkar_label(self):
        """The flavour as every document names it: English · Arabic.

        The register prints it on the receipt, the invoice and the kitchen
        ticket print it too, so it is formed in one place and reads the same
        on all three.
        """
        self.ensure_one()
        # English only since 29 Sep 2026: the register, the receipt and the
        # invoice all read in English now, and a name printed two ways
        # made a flavour look like two.
        return self.name

    @api.model
    def _load_pos_data_fields(self, config_id):
        return ["id", "name", "name_ar", "code", "sequence", "is_mixed"]

    @api.model
    def _load_pos_data_domain(self, data):
        return [("active", "=", True)]
