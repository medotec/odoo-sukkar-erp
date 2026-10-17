from odoo import fields, models


class AccountMoveLine(models.Model):
    """Carry the cake flavours onto the invoice line so they print on it."""
    _inherit = "account.move.line"

    sukkar_flavour_summary = fields.Char(string="Flavours")
