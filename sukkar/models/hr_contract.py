from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class HrContract(models.Model):
    """The one payroll figure that differs person by person.

    Everything else about overtime — the divisor, the three rates, the minimum
    block — is the same for the whole shop and lives on the company. The monthly
    cap is not: the Supervisor is allowed 40 hours a month and the Decorators 15.
    """
    _inherit = "hr.contract"

    sukkar_ot_cap_hours = fields.Float(
        string="Monthly Overtime Cap (hours)",
        default=0.0,
        help="Most overtime hours this person may be PAID for in a month. "
             "Hours worked above the cap appear on the payslip as 'Overtime Hours "
             "Not Paid' rather than vanishing. Agreed: Supervisor 40, Decorators 15. "
             "Leave at 0 for no cap.",
    )

    @api.constrains("sukkar_ot_cap_hours")
    def _check_sukkar_ot_cap_hours(self):
        for contract in self:
            if contract.sukkar_ot_cap_hours < 0:
                raise ValidationError(_("The monthly overtime cap cannot be negative."))
