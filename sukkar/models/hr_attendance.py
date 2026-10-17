from odoo import api, fields, models


class HrAttendance(models.Model):
    """Attendance, with a note of whether a person or the machine last set it.

    The pairing rebuilds attendance from raw punches whenever new ones arrive.
    Without this flag that rebuild would quietly overwrite a manager's correction
    — which is the one edit that must survive, since the whole reason a correction
    exists is that the machine's record was wrong.
    """
    _inherit = "hr.attendance"

    sukkar_punch_managed = fields.Boolean(
        string="Built from Punches", default=False, copy=False,
        help="Set when this record was built automatically from attendance-machine "
             "punches. It clears the moment a person edits the times, and from then "
             "on the rebuild leaves the record alone.")

    def write(self, vals):
        # A human touching the times takes ownership of the record. The pairing
        # code passes sukkar_pairing in the context so its own writes are exempt.
        if not self.env.context.get("sukkar_pairing") and (
                "check_in" in vals or "check_out" in vals):
            vals = dict(vals, sukkar_punch_managed=False)
        return super().write(vals)
