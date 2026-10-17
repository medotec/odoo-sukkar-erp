import calendar
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Codes used for the "Other Inputs" lines on a payslip. Until the attendance
# machine is bought these are typed in; once it is, they will be filled from
# work entries instead and nothing below has to change.
INPUT_OT_NORMAL = "OT_N"      # overtime hours on a normal working day
INPUT_OT_REST = "OT_R"        # hours worked on the weekly rest day (Friday)
INPUT_OT_HOLIDAY = "OT_H"     # hours worked on a public holiday
INPUT_LATE_MIN = "LATE_MIN"   # minutes of lateness in the month

OT_KINDS = ("normal", "rest", "holiday")

# Guards a whole-number of blocks against binary floating point: 3.0 hours must
# never floor to 2 because it is held as 2.9999999996.
_FLOAT_NUDGE = 1e-9


class HrPayslip(models.Model):
    """Sukkar overtime maths.

    The rules the shop works to (confirmed by the owner, 29 Aug 2026):

    * A working day is 9 hours **including a 60-minute unpaid break**, so 8 hours
      are paid. Six days a week, Saturday to Thursday, Friday is the rest day.
      8 x 6 = 48 hours, exactly Kuwait's statutory weekly maximum.
    * The hourly rate is **basic / 26 days / 8 hours** = basic / 208. Overtime is
      paid on **basic only**, never on allowances.
    * x1.25 normal, x1.5 on the rest day, x2 on a public holiday.
    * Lateness is deducted **from overtime, never from basic**.
    * Overtime counts in whole 15-minute blocks — anything short of a full block
      is not paid. The block size is a setting, not a constant.
    * Each contract carries its own monthly cap (Supervisor 40 h, Decorators 15 h).

    Every one of those numbers is a setting on the company or the contract, not a
    constant, so the shop can change them without a developer.
    """
    _inherit = "hr.payslip"

    sukkar_ot_hours_paid = fields.Float(
        string="Paid Overtime Hours", compute="_compute_sukkar_ot_summary",
        help="Overtime hours actually paid on this payslip, after lateness has "
             "been netted off, the minimum block applied and the monthly cap "
             "enforced.")
    sukkar_ot_hours_dropped = fields.Float(
        string="Overtime Hours Not Paid", compute="_compute_sukkar_ot_summary",
        help="Overtime worked but not paid — lost to lateness, to the minimum "
             "block, or to the monthly cap. Shown so the difference is visible "
             "rather than silently disappearing.")

    # ---------------------------------------------------------- rule sandbox
    def _get_tools_dict(self):
        """Expose this payslip to the salary rules as ``tools.sukkar``.

        OCA payroll documents this method as the extension point for adding tools
        to the rule sandbox, which is why the overtime maths lives in Python here
        and the salary rules stay one-liners. Logic that lives in a text box
        cannot be diffed, reviewed or tested, and payroll is the last place to
        accept that.
        """
        tools = super()._get_tools_dict()
        tools["sukkar"] = self
        return tools

    # ------------------------------------------------------------------ inputs
    def _sukkar_input_total(self, code):
        """Total entered against one input code, never below zero.

        Several lines with the same code are summed, so a negative line can be
        used deliberately to correct an earlier one. What it can never do is take
        the total below zero: a mistyped ``-5`` in an overtime box would otherwise
        deduct money from someone's pay, and a mistyped ``-600`` in the lateness
        box would hand them ten hours they never worked.
        """
        self.ensure_one()
        total = sum(
            line.amount for line in self.input_line_ids
            if (line.code or "").strip().upper() == code
        )
        return max(0.0, total)

    @api.model
    def _sukkar_upgrade_salary_rules(self):
        """Bring a shipped rule formula forward without touching the shop's rates.

        The rules load with noupdate="1" on purpose, so an upgrade never
        overwrites an overtime rate somebody changed on the screen. A formula is
        not a rate: only the exact text this module shipped is replaced, so a
        rule anyone has edited is left alone.
        """
        upgrades = {
            "sukkar.sukkar_rule_basic": (
                "result = contract.wage",
                "result = tools.sukkar.sukkar_basic_amount()"),
        }
        for xmlid, (shipped, current) in upgrades.items():
            rule = self.env.ref(xmlid, raise_if_not_found=False)
            if rule and (rule.amount_python_compute or "").strip() == shipped:
                rule.sudo().amount_python_compute = current
        return True

    # ------------------------------------------------------------------ basic
    def sukkar_basic_days(self):
        """(days of the month this payslip pays for, days in that month).

        Counted against the MONTH, not against the payslip's own dates: a wage
        is quoted per month, so two runs inside one month have to add up to one
        month's pay, and a payslip that only covers part of a month pays part of
        the wage. The days paid for are the days the payslip covers AND the
        contract covers.
        """
        self.ensure_one()
        start, end = self.date_from, self.date_to
        if not start or not end or end < start:
            return 0.0, 0.0
        in_month = float(calendar.monthrange(start.year, start.month)[1])
        contract = self.contract_id
        covered_from = max(start, contract.date_start) if contract.date_start else start
        covered_to = min(end, contract.date_end) if contract.date_end else end
        covered = (covered_to - covered_from).days + 1 if covered_to >= covered_from else 0
        return float(covered), in_month

    def sukkar_basic_amount(self):
        """Basic pay for the days this payslip actually covers.

        A flat ``contract.wage`` paid a joiner who started on the 25th a whole
        month, paid a leaver whose contract had ended, and paid the full wage on
        each of two payslips in the same month — 540 KWD for a 270 KWD person.
        Whether the shop prorates at all, and on what basis, is Settings →
        Sukkar → Payroll.
        """
        self.ensure_one()
        wage = max(0.0, self.contract_id.wage or 0.0)
        company = self.company_id or self.env.company
        if not company.sukkar_prorate_basic:
            return wage
        covered, in_month = self.sukkar_basic_days()
        if not in_month or covered >= in_month:
            return wage
        basis = company.sukkar_prorate_basis_days or 0
        divisor = float(basis) if basis > 0 else in_month
        return round(min(wage, wage * (covered / divisor)), 3)

    # ------------------------------------------------------------------ maths
    def _sukkar_ot_worked(self):
        """Overtime hours as entered, per kind, before any adjustment."""
        self.ensure_one()
        return {
            "normal": self._sukkar_input_total(INPUT_OT_NORMAL),
            "rest": self._sukkar_input_total(INPUT_OT_REST),
            "holiday": self._sukkar_input_total(INPUT_OT_HOLIDAY),
        }

    def _sukkar_ot_uncapped(self):
        """Hours after lateness and the minimum block, but before the cap.

        Kept separate from the cap on purpose: this part is a pure function of
        one payslip's own inputs, which is what lets the monthly cap look at
        sibling payslips below without the two computations chasing each other.
        """
        self.ensure_one()
        company = self.company_id or self.env.company
        hours = self._sukkar_ot_worked()

        # 1 · lateness comes off the cheapest hours, so being late never costs a
        #     rest-day or public-holiday premium.
        late_hours = self._sukkar_input_total(INPUT_LATE_MIN) / 60.0
        hours["normal"] = max(0.0, hours["normal"] - late_hours)

        # 2 · whole blocks only, per kind.
        block = company.sukkar_ot_min_block_hours or 0.0
        if block > 0:
            for kind in hours:
                hours[kind] = int(hours[kind] / block + _FLOAT_NUDGE) * block
        return hours

    def _sukkar_month_predecessors(self):
        """Payslips for the same contract, earlier in the same calendar month.

        The cap is a **monthly** ceiling, so it cannot be applied to one payslip
        in isolation — two half-month runs would otherwise pay the cap twice. Only
        payslips strictly *earlier* in the month are considered, which is what
        stops the lookup from recursing.
        """
        self.ensure_one()
        if not self.date_from or not self.contract_id:
            return self.browse()
        month_start = self.date_from.replace(day=1)
        candidates = self.search([
            ("contract_id", "=", self.contract_id.id),
            ("date_from", ">=", month_start),
            ("date_from", "<=", self.date_from),
            ("id", "!=", self.id),
            ("state", "!=", "cancel"),
        ])
        return candidates.filtered(
            lambda p: (p.date_from, p.id) < (self.date_from, self.id))

    def sukkar_ot_hours(self):
        """Overtime hours per kind, after every adjustment.

        The order is deliberate:

        1. **Lateness** comes off first, against normal-rate hours only.
        2. **The minimum block** is applied next, per kind.
        3. **The monthly cap** is applied last, and spends what is left of the
           month's allowance on the most valuable hours first (holiday, then rest
           day, then normal) — so a capped employee is not quietly paid for the
           cheap hours while the expensive ones are discarded.

        Returns ``(paid, worked)``, both dicts keyed by :data:`OT_KINDS`.
        """
        self.ensure_one()
        worked = self._sukkar_ot_worked()
        paid = self._sukkar_ot_uncapped()

        # The cap is CONFIGURATION first (Settings → Sukkar → Payroll), with the
        # contract as an optional per-person override — owner's rule: business
        # numbers live in settings, never hard-coded per employee card.
        company = self.company_id or self.env.company
        cap = (self.contract_id.sukkar_ot_cap_hours
               or company.sukkar_ot_cap_default_hours or 0.0)
        if cap > 0:
            already = sum(
                sum(slip.sukkar_ot_hours()[0].values())
                for slip in self._sukkar_month_predecessors()
            )
            remaining = max(0.0, cap - already)
            for kind in ("holiday", "rest", "normal"):
                allowed = min(paid[kind], remaining)
                paid[kind] = allowed
                remaining -= allowed
        return paid, worked

    def sukkar_hourly_rate(self):
        """Basic ÷ 26 days ÷ 8 hours. Overtime is paid on basic only."""
        self.ensure_one()
        company = self.company_id or self.env.company
        divisor = (company.sukkar_payroll_divisor_days
                   * company.sukkar_payroll_divisor_hours)
        if not divisor:
            return 0.0
        # A negative wage is nonsense but nothing in Odoo forbids one, and it
        # would turn every overtime line into a deduction.
        return max(0.0, self.contract_id.wage or 0.0) / divisor

    def sukkar_ot_amount(self, kind):
        """Money for one overtime kind. Called from the salary rules.

        Recomputed per kind rather than cached: three passes over a handful of
        input lines costs nothing at this shop's size, and a cache that goes stale
        mid-payroll is a far worse problem than the one it would solve.
        """
        self.ensure_one()
        company = self.company_id or self.env.company
        rate = {
            "normal": company.sukkar_ot_rate_normal,
            "rest": company.sukkar_ot_rate_rest,
            "holiday": company.sukkar_ot_rate_holiday,
        }[kind]
        paid, _worked = self.sukkar_ot_hours()
        return paid[kind] * self.sukkar_hourly_rate() * rate

    # ------------------------------------------------- attendance -> payroll
    def action_sukkar_fill_from_attendance(self):
        """Read the machine's attendance for this period and fill the inputs.

        This is the join between the attendance device and pay. Until a machine
        exists the four input codes are typed by hand; this fills exactly the same
        codes from ``hr.attendance``, so **nothing downstream changes** — the
        rates, the block, the caps and the lateness rule all behave identically
        whether the hours were typed or read from a door.

        How each day is classified:

        * **A scheduled working day** — anything beyond the hours the calendar
          says are paid becomes normal overtime.
        * **The weekly rest day** (Friday: no attendance lines on the calendar) —
          every hour worked is rest-day overtime.
        * **A public holiday** (a global leave on the calendar) — every hour
          worked is holiday overtime, and it takes precedence over the rest day.

        Lateness is measured against the first scheduled start of the day, less
        the grace period, and only on days the person was scheduled to work.
        """
        self.ensure_one()
        if not self.contract_id or not self.employee_id:
            raise UserError(_("Set the employee and contract before reading attendance."))
        company = self.company_id or self.env.company
        totals = self._sukkar_attendance_totals()
        codes = {
            INPUT_OT_NORMAL: totals["normal"],
            INPUT_OT_REST: totals["rest"],
            INPUT_OT_HOLIDAY: totals["holiday"],
            INPUT_LATE_MIN: totals["late_minutes"],
        }
        Input = self.env["hr.payslip.input"]
        # Replace rather than add, so re-reading attendance is safe to repeat.
        self.input_line_ids.filtered(lambda l: l.code in codes).unlink()
        for code, amount in codes.items():
            if not amount:
                continue
            Input.create({
                "payslip_id": self.id, "contract_id": self.contract_id.id,
                "name": code, "code": code, "amount": amount,
            })
        self.invalidate_recordset()
        message = _(
            "Read from attendance: %(n).2f h normal, %(r).2f h rest day, "
            "%(h).2f h public holiday, %(l).0f minutes late "
            "(grace %(g)d min, already deducted).",
            n=totals["normal"], r=totals["rest"], h=totals["holiday"],
            l=totals["late_minutes"], g=company.sukkar_late_grace_min)
        unresolved = self.env["sukkar.attendance.punch"].sudo().search_count([
            ("employee_id", "=", self.employee_id.id),
            ("state", "=", "missing_out"),
            ("resolution", "=", "pending"),
            ("punched_at", ">=", datetime.combine(self.date_from, time.min)),
            ("punched_at", "<=", datetime.combine(self.date_to, time.max)),
        ])
        if unresolved:
            # Not blocked — but nobody should post a payslip without knowing.
            message += _(
                " ⚠ %(n)d missing clock-out(s) in this period still have no "
                "decision; those days contribute no hours at all.", n=unresolved)
        self.message_post(body=message)
        return True

    def _sukkar_attendance_totals(self):
        """Overtime and lateness for this payslip's period, from attendance."""
        self.ensure_one()
        company = self.company_id or self.env.company
        calendar = (self.contract_id.resource_calendar_id
                    or self.employee_id.resource_calendar_id)
        tz = pytz.timezone(company.sukkar_tz or "Asia/Kuwait")
        grace = timedelta(minutes=company.sukkar_late_grace_min or 0)

        # check_in is stored in UTC and the days below are grouped by LOCAL
        # date, so the window has to be built in local time too. Kuwait is
        # UTC+3: a naive window put the shop's 00:00-03:00 on the 1st into the
        # PREVIOUS month's payslip, which by then was already paid.
        period_start = self._sukkar_local_to_utc(tz, self.date_from, time.min)
        period_end = self._sukkar_local_to_utc(tz, self.date_to, time.max)

        attendances = self.env["hr.attendance"].sudo().search([
            ("employee_id", "=", self.employee_id.id),
            ("check_in", ">=", period_start),
            ("check_in", "<=", period_end),
            ("check_out", "!=", False),
        ], order="check_in asc")

        # Public holidays are global leaves on the calendar — no resource attached.
        holidays = self.env["resource.calendar.leaves"].sudo().search([
            ("resource_id", "=", False),
            "|", ("calendar_id", "=", calendar.id), ("calendar_id", "=", False),
            ("date_to", ">=", period_start),
            ("date_from", "<=", period_end),
        ])

        # What management decided about any missing clock-out in this period.
        # A decision that does not change the pay is theatre, so both outcomes
        # that carry a consequence are applied here.
        decisions = self.env["sukkar.attendance.punch"].sudo().search([
            ("employee_id", "=", self.employee_id.id),
            ("state", "in", ("missing_out", "paired")),
            ("resolution", "in", ("penalty", "no_overtime")),
            ("punched_at", ">=", period_start),
            ("punched_at", "<=", period_end),
        ])
        penalty_minutes = sum(decisions.mapped("penalty_minutes"))
        no_overtime_days = {
            pytz.utc.localize(p.punched_at).astimezone(tz).date()
            for p in decisions if p.resolution == "no_overtime"
        }

        by_day = {}
        for att in attendances:
            local_in = pytz.utc.localize(att.check_in).astimezone(tz)
            day = local_in.date()
            entry = by_day.setdefault(day, {"hours": 0.0, "first_in": local_in})
            entry["hours"] += att.worked_hours
            if local_in < entry["first_in"]:
                entry["first_in"] = local_in

        totals = {"normal": 0.0, "rest": 0.0, "holiday": 0.0,
                  "late_minutes": float(penalty_minutes)}
        for day, entry in by_day.items():
            # Overtime cancelled by management for this day. The scheduled hours
            # still stand — the person worked them — but nothing above them pays.
            if day in no_overtime_days:
                continue
            scheduled = self._sukkar_scheduled_hours(calendar, day)
            # A holiday runs 00:00-24:00 LOCAL. Kuwait is UTC+3, so it is stored
            # as 21:00 the previous day through 21:00 — and comparing .date() on
            # those UTC values makes the day BEFORE a holiday pay at x2.
            is_holiday = any(
                pytz.utc.localize(h.date_from).astimezone(tz).date() <= day
                <= pytz.utc.localize(h.date_to).astimezone(tz).date()
                for h in holidays)
            if is_holiday:
                totals["holiday"] += entry["hours"]
                continue
            if scheduled <= 0:
                # Not a scheduled working day at all — Friday, the rest day.
                totals["rest"] += entry["hours"]
                continue
            totals["normal"] += max(0.0, entry["hours"] - scheduled)
            start = self._sukkar_scheduled_start(calendar, day, tz)
            if start and entry["first_in"] > start + grace:
                late = (entry["first_in"] - (start + grace)).total_seconds() / 60.0
                totals["late_minutes"] += late
        return totals

    @staticmethod
    def _sukkar_local_to_utc(tz, day, at):
        """A local wall-clock moment as the naive UTC value Odoo stores."""
        return tz.localize(datetime.combine(day, at)).astimezone(
            pytz.utc).replace(tzinfo=None)

    @staticmethod
    def _sukkar_scheduled_hours(calendar, day):
        """Paid hours the calendar says this weekday carries. 0 = not a work day.

        The unpaid break is on the calendar as a `lunch` line so the attendance
        machine subtracts it; it is not paid time, so it is not counted here
        either — counting it would hand every ordinary day an hour of overtime.
        """
        dow = str(day.weekday())
        return sum(a.hour_to - a.hour_from
                   for a in calendar.attendance_ids
                   if a.dayofweek == dow and a.day_period != "lunch")

    @staticmethod
    def _sukkar_scheduled_start(calendar, day, tz):
        """When the person was due to start, as an aware datetime in `tz`."""
        dow = str(day.weekday())
        starts = [a.hour_from for a in calendar.attendance_ids
                  if a.dayofweek == dow and a.day_period != "lunch"]
        if not starts:
            return None
        first = min(starts)
        return tz.localize(datetime.combine(
            day, time(hour=int(first), minute=int(round((first % 1) * 60)))))

    # ------------------------------------------------- accounting integration
    @api.model
    def _sukkar_setup_accounting(self):
        """Salary journal + accounts, wired onto the salary rules (idempotent).

        Called from data/sukkar_accounting_data.xml on every install/upgrade, so
        a fresh database posts payroll to accounting without anyone remembering
        a script. It only fills what is EMPTY — accounts or journals the
        accountant has since remapped are left alone.

        Posting shape (balanced by construction): BASIC and the three OT rules
        debit the salary expense account; NET credits Salaries Payable. GROSS
        carries no accounts on purpose — it is the sum of the debit lines, and
        giving it an account would post every payslip twice.
        """
        company = self.env.company
        # A brand-new database installs its chart of accounts AFTER every
        # module, and that install deletes the accounts and journals that were
        # there first — including these, leaving payroll posting nowhere. So
        # wait: sukkar.books.setup's cron calls this again once the chart is in.
        if not company.chart_template:
            return False
        Account = self.env["account.account"].with_company(company)

        def get_account(code, name, account_type, reconcile=False):
            acc = Account.search([("code", "=", code)], limit=1)
            return acc or Account.create({
                "code": code, "name": name,
                "account_type": account_type, "reconcile": reconcile})

        expense = get_account("6300", "Salaries & Overtime Expense", "expense")
        payable = get_account("2300", "Salaries Payable", "liability_current",
                              reconcile=True)

        Journal = self.env["account.journal"]
        journal = Journal.search([
            ("code", "=", "SLRY"), ("company_id", "=", company.id)], limit=1)
        if not journal:
            journal = Journal.create({
                "name": "Salaries", "code": "SLRY", "type": "general",
                "company_id": company.id})

        debit_rules = ("sukkar.sukkar_rule_basic", "sukkar.sukkar_rule_ot_normal",
                       "sukkar.sukkar_rule_ot_rest", "sukkar.sukkar_rule_ot_holiday")
        for xmlid in debit_rules:
            rule = self.env.ref(xmlid, raise_if_not_found=False)
            if rule is not None and not rule.account_debit:
                rule.account_debit = expense
        net = self.env.ref("sukkar.sukkar_rule_net", raise_if_not_found=False)
        if net is not None and not net.account_credit:
            net.account_credit = payable

        # The payslip's journal comes from the CONTRACT (payroll_account related
        # field). Fill it wherever it is blank so existing contracts post too.
        self.env["hr.contract"].search([("journal_id", "=", False)]).write(
            {"journal_id": journal.id})

        # The structure record is noupdate data, so rules added after it first
        # shipped (the NET bank/cash split) must be appended here instead —
        # idempotent, and it respects any manual rule changes.
        structure = self.env.ref("sukkar.sukkar_payroll_structure",
                                 raise_if_not_found=False)
        if structure is not None:
            for xmlid in ("sukkar.sukkar_rule_net_bank",
                          "sukkar.sukkar_rule_net_cash"):
                rule = self.env.ref(xmlid, raise_if_not_found=False)
                if rule is not None and rule not in structure.rule_ids:
                    structure.rule_ids = [(4, rule.id)]
        return True

    @api.depends("input_line_ids.amount", "input_line_ids.code",
                 "contract_id.wage", "contract_id.sukkar_ot_cap_hours",
                 "date_from", "state",
                 "company_id.sukkar_ot_min_block_hours")
    def _compute_sukkar_ot_summary(self):
        for slip in self:
            if not slip.contract_id:
                slip.sukkar_ot_hours_paid = 0.0
                slip.sukkar_ot_hours_dropped = 0.0
                continue
            paid, worked = slip.sukkar_ot_hours()
            slip.sukkar_ot_hours_paid = sum(paid.values())
            slip.sukkar_ot_hours_dropped = max(
                0.0, sum(worked.values()) - sum(paid.values()))
