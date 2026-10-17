from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class SukkarMissingPunchWizard(models.TransientModel):
    """What management decided about a day that ended on an odd punch.

    Until this is answered the day carries no hours at all, because nobody knows
    when the person left and the system will not guess. There are three honest
    answers and no fourth:

    * **Manual clock-out** — management knows, or has decided, when they left.
      The attendance is created with that time and the day pays normally.
    * **Penalty** — the time is not known and the lapse carries a cost. The
      minutes are deducted from **overtime, never from basic**, which is the rule
      the shop already applies to lateness.
    * **Cancel overtime** — the day stands, the scheduled hours are honoured, but
      it earns no overtime. The mildest option that still has a consequence.
    """
    _name = "sukkar.missing.punch.wizard"
    _description = "Missing Clock-out Decision"

    punch_ids = fields.Many2many(
        "sukkar.attendance.punch", string="Missing clock-outs", required=True)
    employee_summary = fields.Char(compute="_compute_summary", string="Who")
    occurrence_warning = fields.Char(compute="_compute_summary", string="Pattern")
    decision = fields.Selection(
        [("manual_out", "Enter a manual clock-out"),
         ("penalty", "Apply a penalty"),
         ("no_overtime", "Cancel overtime for that day")],
        required=True, default="manual_out")
    manual_check_out = fields.Datetime(string="They actually left at")
    penalty_minutes = fields.Integer(string="Penalty (minutes)", default=30)
    note = fields.Char(string="Reason", help="Kept on the record for later reference.")

    @api.depends("punch_ids")
    def _compute_summary(self):
        for wizard in self:
            employees = wizard.punch_ids.mapped("employee_id")
            wizard.employee_summary = ", ".join(employees.mapped("name")) or ""
            worst = max(wizard.punch_ids.mapped("occurrence_this_month") or [0])
            wizard.occurrence_warning = (
                _("%(n)d missed clock-outs this month", n=worst) if worst > 1 else "")

    @api.constrains("decision", "manual_check_out", "penalty_minutes")
    def _check_decision(self):
        for wizard in self:
            if wizard.decision == "manual_out":
                if not wizard.manual_check_out:
                    raise ValidationError(_("Enter the time they actually left."))
                for punch in wizard.punch_ids:
                    if wizard.manual_check_out <= punch.punched_at:
                        raise ValidationError(_(
                            "The clock-out must be after the clock-in (%s).",
                            punch.punched_at))
            if wizard.decision == "penalty" and wizard.penalty_minutes <= 0:
                raise ValidationError(_("A penalty of zero is not a decision."))

    def action_apply(self):
        self.ensure_one()
        Attendance = self.env["hr.attendance"].sudo().with_context(sukkar_pairing=True)
        for punch in self.punch_ids:
            if punch.state != "missing_out":
                raise UserError(_("%s no longer needs a decision.", punch.display_name))
            values = {
                "resolution": self.decision,
                "resolution_note": self.note,
                "resolved_by": self.env.uid,
                "resolved_on": fields.Datetime.now(),
            }
            if self.decision == "manual_out":
                Attendance.create({
                    "employee_id": punch.employee_id.id,
                    "check_in": punch.punched_at,
                    "check_out": self.manual_check_out,
                    # Not machine-managed: a person decided this, so a later sync
                    # must leave it alone.
                    "sukkar_punch_managed": False,
                })
                values.update({"manual_check_out": self.manual_check_out,
                               "state": "paired"})
            elif self.decision == "penalty":
                values["penalty_minutes"] = self.penalty_minutes
            punch.write(values)
            punch.employee_id.message_post(body=_(
                "Missing clock-out on %(when)s: %(decision)s.%(note)s",
                when=punch.punched_at,
                decision=dict(self._fields["decision"].selection)[self.decision],
                note=(" — %s" % self.note) if self.note else ""))
        return {"type": "ir.actions.act_window_close"}
