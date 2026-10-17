from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.base.models.res_partner import _tz_get


class ResCompany(models.Model):
    """Company-level operating settings.

    These live here rather than as constants in the code so the shop can change
    them from Settings without a developer.
    """
    _inherit = "res.company"

    sukkar_finish_before_min = fields.Integer(
        string="Finish Before Delivery (minutes)",
        default=30,
        help="How long before the delivery time the kitchen must have the order "
             "finished. This is the Finish-By time shown on every kitchen ticket.",
    )
    sukkar_tz = fields.Selection(
        _tz_get,
        string="Operating Timezone",
        default="Asia/Kuwait",
        help="Timezone the shop books deliveries in. Delivery slots are wall-clock "
             "times in this zone; Odoo stores the moment in UTC. Set at company "
             "level on purpose, so a delivery does not shift when a user in a "
             "different timezone edits the order.",
    )

    # --- Payroll (all confirmed by the owner, 29 Aug 2026) ------------------
    # Settings, not constants: every one of these is a business decision the shop
    # can revisit without a developer, and each is load-bearing for someone's pay.
    sukkar_payroll_divisor_days = fields.Integer(
        string="Salary Divisor — Days", default=26,
        help="Days the monthly basic is divided by to reach a daily rate. "
             "26 matches a six-day week (30 days less the four Fridays).")
    sukkar_payroll_divisor_hours = fields.Float(
        string="Salary Divisor — Hours", default=8.0,
        help="Paid hours in a working day. The day is 9 hours including a "
             "60-minute unpaid break, so 8 are paid. With 26 days this gives an "
             "hourly rate of basic / 208.")
    sukkar_ot_rate_normal = fields.Float(
        string="Overtime Rate — Normal Day", default=1.25)
    sukkar_ot_rate_rest = fields.Float(
        string="Overtime Rate — Rest Day", default=1.5,
        help="Friday, the weekly rest day for everyone.")
    sukkar_ot_rate_holiday = fields.Float(
        string="Overtime Rate — Public Holiday", default=2.0)
    sukkar_ot_min_block_hours = fields.Float(
        string="Minimum Overtime Block (hours)", default=0.25,
        help="Overtime is paid in whole blocks of this size; anything short of a "
             "full block is not paid. At 0.25 (15 minutes), 1h50m of overtime pays "
             "1h45m, and 10 minutes pays nothing. Set to 0 to pay to the exact "
             "minute. Expressed in HOURS, so 15 minutes is 0.25.")

    sukkar_ot_cap_default_hours = fields.Float(
        string="Monthly Overtime Cap (hours)", default=0.0,
        help="Company-wide ceiling on paid overtime hours per person per month. "
             "0 = no cap. A contract can still carry its own cap, which then "
             "overrides this default for that person only — the rule lives HERE, "
             "in configuration, not hard-coded per employee (owner's rule, "
             "29 Aug 2026).")
    sukkar_prorate_basic = fields.Boolean(
        string="Prorate Basic Pay for Part Months", default=True,
        help="A person who joins or leaves part-way through the month is paid "
             "for the days the contract actually covers, not the whole month. "
             "Switch it off to pay the full basic whatever the dates say.")
    sukkar_prorate_basis_days = fields.Integer(
        string="Part-Month Basis (days)", default=0,
        help="What a part month is divided by. 0 = the calendar days in the "
             "month itself (31 in October, 28 in February). Set 26 or 30 to use "
             "a fixed month instead — the Kuwait practice the shop follows.")
    sukkar_late_grace_min = fields.Integer(
        string="Lateness Grace (minutes)", default=15,
        help="Minutes a person may arrive late before any of it counts. Only the "
             "time beyond the grace is deducted, and it comes off overtime, never "
             "off basic pay.")

    @api.constrains("sukkar_payroll_divisor_days", "sukkar_payroll_divisor_hours",
                    "sukkar_ot_rate_normal", "sukkar_ot_rate_rest",
                    "sukkar_ot_rate_holiday", "sukkar_ot_min_block_hours")
    def _check_sukkar_payroll_settings(self):
        """A zero divisor would make every hourly rate zero, silently."""
        for company in self:
            if company.sukkar_payroll_divisor_days <= 0 or company.sukkar_payroll_divisor_hours <= 0:
                raise ValidationError(_(
                    "The salary divisors must both be greater than zero, or every "
                    "overtime hour would be worth nothing."))
            for label, rate in (("normal day", company.sukkar_ot_rate_normal),
                                ("rest day", company.sukkar_ot_rate_rest),
                                ("public holiday", company.sukkar_ot_rate_holiday)):
                if rate < 1.0:
                    raise ValidationError(_(
                        "The overtime rate for a %s cannot be below 1.0 — that would "
                        "pay an overtime hour less than a normal one.") % label)
            if company.sukkar_ot_min_block_hours < 0:
                raise ValidationError(_("The minimum overtime block cannot be negative."))
            if company.sukkar_ot_cap_default_hours < 0:
                raise ValidationError(_("The monthly overtime cap cannot be negative."))

    # --- Making the timezone setting actually mean something ------------------
    def _sukkar_propagate_tz(self):
        """Push `sukkar_tz` onto everything in Odoo that reads a timezone.

        Setting one field on the company achieves nothing on its own — Odoo reads
        the timezone from several unrelated places, and each defaults somewhere
        unhelpful:

        * ``company.partner_id.tz`` — blank by default. The stock_enhancement
          module reads exactly this for its scheduled-time column and falls back
          to **UTC**, which in Kuwait is three hours wrong.
        * ``resource.calendar.tz`` — Odoo's stock "Standard 40 hours/week"
          calendar is created in **UTC**. Any contract left on it has its hours,
          and therefore its overtime, computed in the wrong zone.
        * ``res.users.tz`` — drives every datetime a person sees, and every cron.
          The ``__system__`` user that runs all crons has none at all.

        Users who have deliberately chosen a different zone are left alone: only
        those with no timezone, or who were still following the company's previous
        one, are moved.
        """
        for company in self:
            tz = company.sukkar_tz
            if not tz:
                continue
            if company.partner_id.tz != tz:
                company.partner_id.sudo().tz = tz
            calendars = self.env["resource.calendar"].sudo().search([
                "|", ("company_id", "=", company.id), ("company_id", "=", False)])
            calendars.filtered(lambda c: c.tz != tz).write({"tz": tz})
            users = self.env["res.users"].sudo().with_context(active_test=False).search([
                ("company_ids", "in", company.id)])
            for user in users:
                if not user.tz:
                    user.tz = tz
        return True

    def write(self, vals):
        previous = {c.id: c.sukkar_tz for c in self}
        res = super().write(vals)
        if "sukkar_tz" in vals:
            # Users who were following the old company timezone move with it;
            # anyone who picked their own is left where they are.
            for company in self:
                old = previous.get(company.id)
                if old and old != company.sukkar_tz:
                    followers = self.env["res.users"].sudo().search([
                        ("company_ids", "in", company.id), ("tz", "=", old)])
                    followers.write({"tz": company.sukkar_tz})
            self._sukkar_propagate_tz()
        if "sukkar_stock_plan_hour" in vals or "sukkar_tz" in vals:
            self._sukkar_reschedule_stock_plan()
        return res

    def _sukkar_reschedule_stock_plan(self):
        """Move the nightly stock-plan job to the hour the owner chose.

        The job stores its next run in UTC, so the shop's hour is converted
        through the operating timezone rather than written straight in.
        """
        cron = self.env.ref(
            "sukkar.cron_daily_stock_plan", raise_if_not_found=False)
        if not cron:
            return
        company = self[:1] or self.env.company
        hour = min(max(company.sukkar_stock_plan_hour or 23, 0), 23)
        shop_tz = pytz.timezone(company.sukkar_tz or "Asia/Kuwait")
        local = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        run_at = local.astimezone(shop_tz).replace(
            hour=hour, minute=0, second=0, microsecond=0)
        if run_at <= local.astimezone(shop_tz):
            run_at += timedelta(days=1)
        cron.sudo().nextcall = run_at.astimezone(pytz.utc).replace(tzinfo=None)

    @api.constrains("sukkar_finish_before_min")
    def _check_sukkar_finish_before_min(self):
        """A negative value would put the Finish-By time AFTER the delivery."""
        for company in self:
            if company.sukkar_finish_before_min < 0:
                raise ValidationError(_(
                    "Finish Before Delivery cannot be negative — the kitchen would be "
                    "told to finish after the order has already gone out."))
            if company.sukkar_finish_before_min > 24 * 60:
                raise ValidationError(_(
                    "Finish Before Delivery cannot be more than 24 hours."))
