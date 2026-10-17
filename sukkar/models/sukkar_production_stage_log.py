from odoo import api, fields, models


class SukkarProductionStageLog(models.Model):
    """One row per stage a ticket passes through — records entry/exit time,
    actual minutes spent, and whether it overran the planned duration."""
    _name = "sukkar.production.stage.log"
    _description = "Kitchen Stage Time Log"
    _order = "entered_at, id"

    production_id = fields.Many2one(
        "sukkar.production", string="Ticket", required=True, ondelete="cascade", index=True)
    stage_id = fields.Many2one("sukkar.production.stage", string="Stage", required=True)
    partner_id = fields.Many2one(related="production_id.partner_id", store=True, string="Customer")
    entered_at = fields.Datetime(required=True)
    exited_at = fields.Datetime()
    planned_min = fields.Float(string="Planned (min)")
    duration_min = fields.Float(
        string="Actual (min)", compute="_compute_duration", store=True)
    overrun = fields.Boolean(compute="_compute_duration", store=True)
    is_open = fields.Boolean(compute="_compute_duration", store=True)

    @api.depends("entered_at", "exited_at", "planned_min")
    def _compute_duration(self):
        now = fields.Datetime.now()
        for log in self:
            end = log.exited_at or now
            log.duration_min = round(((end - log.entered_at).total_seconds() / 60.0), 1) \
                if log.entered_at else 0.0
            log.is_open = not log.exited_at
            log.overrun = bool(log.planned_min and log.exited_at and log.duration_min > log.planned_min)
