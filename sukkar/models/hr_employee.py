from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    sukkar_device_user_id = fields.Char(
        string="Attendance ID",
        copy=False, index=True,
        help="The number the attendance machine knows this person by — the ID "
             "enrolled on the device, not their employee number. Punches with no "
             "matching Attendance ID are kept but flagged, never silently dropped.")

    _sql_constraints = [
        ("sukkar_device_user_unique",
         "unique(sukkar_device_user_id, company_id)",
         "Two employees cannot share the same Attendance ID — their hours would be "
         "merged into one person."),
    ]

    sukkar_missing_out_count = fields.Integer(
        compute="_compute_sukkar_missing_out_count",
        string="Missed Clock-outs This Month",
        help="How many times this person has failed to clock out in the current "
             "calendar month. One is an accident; a pattern is a management "
             "conversation, which is why it is counted rather than just fixed.")

    def _compute_sukkar_missing_out_count(self):
        from datetime import date, timedelta
        today = date.today()
        start = today.replace(day=1)
        month_end = (start + timedelta(days=32)).replace(day=1)
        Punch = self.env["sukkar.attendance.punch"].sudo()
        counts = dict(Punch._read_group(
            [("employee_id", "in", self.ids), ("state", "=", "missing_out"),
             ("punched_at", ">=", start), ("punched_at", "<", month_end)],
            groupby=["employee_id"], aggregates=["__count"]))
        for employee in self:
            employee.sukkar_missing_out_count = counts.get(employee, 0)
