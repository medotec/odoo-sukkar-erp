from odoo import api, models


class ResLang(models.Model):
    _inherit = "res.lang"

    @api.model
    def _sukkar_arabic_code(self):
        """The shop's installed Arabic language (ar_001 today), or False.

        Found, not written in: the code a language carries is Odoo's business,
        and a register that asked for a language that is not there would print
        nothing at all."""
        return next((code for code, _name in self.get_installed()
                     if code.startswith("ar")), False)
