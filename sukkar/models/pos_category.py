from odoo import api, fields, models


class PosCategory(models.Model):
    """The register's categories lead with their Arabic name (owner's layout
    pick, 29 Sep 2026). The name is the one already written in the category's
    Arabic translation, so it is edited where every other name is edited."""
    _inherit = "pos.category"

    sukkar_name_ar = fields.Char(
        string="Arabic Name", compute="_compute_sukkar_name_ar",
        help="The category's Arabic name, read from its translation.")

    @api.depends("name")
    @api.depends_context("lang")
    def _compute_sukkar_name_ar(self):
        arabic = self.env["res.lang"]._sukkar_arabic_code()
        if not arabic:
            self.sukkar_name_ar = False
            return
        for category, category_ar in zip(self, self.with_context(lang=arabic)):
            # No Arabic written yet: the translation falls back to the English,
            # and the register would print the same name twice.
            category.sukkar_name_ar = (
                category_ar.name if category_ar.name != category.name else False)
