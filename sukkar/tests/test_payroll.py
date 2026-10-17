from datetime import date

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarPayroll(TransactionCase):
    """Every overtime scenario, checked against a hand-calculated figure.

    These began as throwaway scripts. They live here so that the next person to
    touch the overtime maths — or the next Odoo upgrade — finds out immediately
    if any of it changes. Payroll is the one area where a quiet rounding
    difference becomes a serious problem.
    """

    BASIC = 300.0          # a round number so the arithmetic can be checked by hand
    HOURLY = 300.0 / 208.0  # basic / 26 days / 8 hours = 1.442307...

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({
            "sukkar_payroll_divisor_days": 26,
            "sukkar_payroll_divisor_hours": 8.0,
            "sukkar_ot_rate_normal": 1.25,
            "sukkar_ot_rate_rest": 1.5,
            "sukkar_ot_rate_holiday": 2.0,
            "sukkar_ot_min_block_hours": 0.25,
        })
        cls.calendar = cls.env.ref("sukkar.sukkar_resource_calendar")
        cls.structure = cls.env.ref("sukkar.sukkar_payroll_structure")
        cls.employee = cls.env["hr.employee"].create({
            "name": "Payroll Test", "resource_calendar_id": cls.calendar.id})
        cls.contract = cls.env["hr.contract"].create({
            "name": "Payroll Test contract",
            "employee_id": cls.employee.id,
            "wage": cls.BASIC,
            "date_start": date(2026, 1, 1),
            "resource_calendar_id": cls.calendar.id,
            "struct_id": cls.structure.id,
            "state": "open",
            "sukkar_ot_cap_hours": 40.0,
        })

    # ------------------------------------------------------------------ helpers
    def _payslip(self, inputs=None, cap=40.0, wage=None,
                 date_from=date(2026, 7, 1), date_to=date(2026, 7, 31)):
        self.contract.sukkar_ot_cap_hours = cap
        if wage is not None:
            self.contract.wage = wage
        slip = self.env["hr.payslip"].create({
            "employee_id": self.employee.id,
            "contract_id": self.contract.id,
            "struct_id": self.structure.id,
            "date_from": date_from,
            "date_to": date_to,
            "name": "Test payslip",
        })
        for code, amount in (inputs or {}).items():
            self.env["hr.payslip.input"].create({
                "payslip_id": slip.id, "contract_id": self.contract.id,
                "name": code, "code": code, "amount": amount,
            })
        slip.invalidate_recordset()
        return slip

    def _lines(self, slip):
        slip.compute_sheet()
        return {line.code: line.total for line in slip.line_ids}

    def _assert_ot(self, slip, kind, hours, msg=None):
        """Overtime money for `kind` must equal `hours` at the right multiplier."""
        rate = {"normal": 1.25, "rest": 1.5, "holiday": 2.0}[kind]
        self.assertAlmostEqual(
            slip.sukkar_ot_amount(kind), hours * self.HOURLY * rate, places=2,
            msg=msg or f"{kind} overtime should be {hours} h")

    # ------------------------------------------------------------------ basics
    def test_basic_only(self):
        slip = self._payslip()
        self.assertAlmostEqual(self._lines(slip)["NET"], self.BASIC, places=2)

    def test_normal_overtime(self):
        slip = self._payslip({"OT_N": 10.0})
        self._assert_ot(slip, "normal", 10.0)
        self.assertAlmostEqual(
            self._lines(slip)["NET"], self.BASIC + 10 * self.HOURLY * 1.25, places=2)

    def test_all_three_rates(self):
        slip = self._payslip({"OT_N": 2.0, "OT_R": 3.0, "OT_H": 4.0}, cap=0.0)
        self._assert_ot(slip, "normal", 2.0)
        self._assert_ot(slip, "rest", 3.0)
        self._assert_ot(slip, "holiday", 4.0)

    # ------------------------------------------------------ the minimum block
    def test_block_floors_to_the_quarter_hour(self):
        """With a 15-minute block, 1 h 50 m of overtime pays 1 h 45 m."""
        slip = self._payslip({"OT_N": 1.8333})
        self._assert_ot(slip, "normal", 1.75)
        self.assertAlmostEqual(slip.sukkar_ot_hours_dropped, 0.0833, places=2)

    def test_block_drops_anything_under_one_block(self):
        """Ten minutes is short of a 15-minute block, so it pays nothing."""
        slip = self._payslip({"OT_N": 0.1667})
        self._assert_ot(slip, "normal", 0.0)
        self.assertAlmostEqual(self._lines(slip)["NET"], self.BASIC, places=2)

    def test_half_an_hour_is_two_blocks(self):
        """Under the old one-hour block this paid nothing. Now it pays 30 minutes."""
        slip = self._payslip({"OT_N": 0.5})
        self._assert_ot(slip, "normal", 0.5)

    def test_exact_hours_not_lost_to_float_error(self):
        """3.0 h must never floor to 2 because of binary floating point."""
        slip = self._payslip({"OT_N": 3.0}, cap=0.0)
        self._assert_ot(slip, "normal", 3.0)

    def test_block_can_be_disabled(self):
        self.company.sukkar_ot_min_block_hours = 0.0
        slip = self._payslip({"OT_N": 1.8333}, cap=0.0)
        self._assert_ot(slip, "normal", 1.8333)

    def test_block_size_is_a_setting(self):
        """Changing the block changes the rounding — it is not baked into the code."""
        self.company.sukkar_ot_min_block_hours = 0.5
        slip = self._payslip({"OT_N": 1.8333}, cap=0.0)
        self._assert_ot(slip, "normal", 1.5)

    # ---------------------------------------------------------------- lateness
    def test_lateness_comes_off_overtime(self):
        """30 minutes late off 10 hours leaves 9.5 — exactly 38 quarter-hour blocks."""
        slip = self._payslip({"OT_N": 10.0, "LATE_MIN": 30.0})
        self._assert_ot(slip, "normal", 9.5)

    def test_lateness_never_takes_pay_below_basic(self):
        slip = self._payslip({"OT_N": 1.0, "LATE_MIN": 120.0})
        self._assert_ot(slip, "normal", 0.0)
        self.assertAlmostEqual(self._lines(slip)["NET"], self.BASIC, places=2)

    def test_lateness_spares_premium_hours(self):
        """Being late must not cost a rest-day or holiday premium."""
        slip = self._payslip({"OT_N": 5.0, "OT_R": 5.0, "LATE_MIN": 120.0}, cap=0.0)
        self._assert_ot(slip, "normal", 3.0)
        self._assert_ot(slip, "rest", 5.0)

    # -------------------------------------------------------------------- caps
    def test_cap_limits_hours(self):
        slip = self._payslip({"OT_N": 50.0}, cap=40.0)
        self._assert_ot(slip, "normal", 40.0)
        self.assertAlmostEqual(slip.sukkar_ot_hours_dropped, 10.0, places=2)

    def test_cap_spends_the_expensive_hours_first(self):
        slip = self._payslip({"OT_H": 5.0, "OT_R": 10.0, "OT_N": 40.0}, cap=40.0)
        self._assert_ot(slip, "holiday", 5.0)
        self._assert_ot(slip, "rest", 10.0)
        self._assert_ot(slip, "normal", 25.0)

    def test_cap_smaller_than_holiday_hours(self):
        slip = self._payslip({"OT_H": 5.0, "OT_R": 5.0, "OT_N": 5.0}, cap=2.0)
        self._assert_ot(slip, "holiday", 2.0)
        self._assert_ot(slip, "rest", 0.0)
        self._assert_ot(slip, "normal", 0.0)

    def test_zero_cap_means_no_cap(self):
        slip = self._payslip({"OT_N": 30.0}, cap=0.0)
        self._assert_ot(slip, "normal", 30.0)

    def test_cap_is_monthly_not_per_payslip(self):
        """Two runs in one month share one allowance — the whole point of a cap."""
        first = self._payslip({"OT_N": 30.0}, cap=40.0,
                              date_from=date(2026, 7, 1), date_to=date(2026, 7, 15))
        self._assert_ot(first, "normal", 30.0)
        second = self._payslip({"OT_N": 30.0}, cap=40.0,
                               date_from=date(2026, 7, 16), date_to=date(2026, 7, 31))
        self._assert_ot(second, "normal", 10.0,
                        "the second run may only use what the first left of the cap")

    def test_cap_resets_the_following_month(self):
        self._payslip({"OT_N": 40.0}, cap=40.0,
                      date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))
        august = self._payslip({"OT_N": 40.0}, cap=40.0,
                               date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
        self._assert_ot(august, "normal", 40.0)

    # ------------------------------------------------------- rubbish in the box
    def test_negative_overtime_never_deducts_pay(self):
        """A mistyped -5 must not take money off someone's payslip."""
        slip = self._payslip({"OT_R": -2.0, "OT_H": -5.0}, cap=0.0)
        self._assert_ot(slip, "rest", 0.0)
        self._assert_ot(slip, "holiday", 0.0)
        self.assertGreaterEqual(slip.sukkar_ot_hours_paid, 0.0)

    def test_negative_lateness_is_not_free_overtime(self):
        """A mistyped -600 must not hand someone ten hours they never worked."""
        slip = self._payslip({"OT_N": 1.0, "LATE_MIN": -600.0}, cap=0.0)
        self._assert_ot(slip, "normal", 1.0)

    def test_correction_lines_still_net_off(self):
        """Deliberate negative corrections are allowed, down to zero but no further."""
        slip = self._payslip(cap=0.0)
        for amount in (10.0, -3.0):
            self.env["hr.payslip.input"].create({
                "payslip_id": slip.id, "contract_id": self.contract.id,
                "name": "OT_N", "code": "OT_N", "amount": amount})
        slip.invalidate_recordset()
        self._assert_ot(slip, "normal", 7.0)

    def test_negative_wage_pays_no_overtime(self):
        slip = self._payslip({"OT_N": 10.0}, cap=0.0, wage=-300.0)
        self._assert_ot(slip, "normal", 0.0)

    def test_zero_wage_does_not_crash(self):
        slip = self._payslip({"OT_N": 10.0}, cap=0.0, wage=0.0)
        self.assertEqual(slip.sukkar_ot_amount("normal"), 0.0)

    def test_duplicate_input_codes_are_summed(self):
        slip = self._payslip(cap=0.0)
        for amount in (3.0, 4.0):
            self.env["hr.payslip.input"].create({
                "payslip_id": slip.id, "contract_id": self.contract.id,
                "name": "OT_N", "code": "OT_N", "amount": amount})
        slip.invalidate_recordset()
        self._assert_ot(slip, "normal", 7.0)

    # ----------------------------------------------------------- the settings
    def test_changing_the_divisor_changes_the_rate(self):
        self.company.sukkar_payroll_divisor_days = 30
        slip = self._payslip({"OT_N": 10.0}, cap=0.0)
        self.assertAlmostEqual(
            slip.sukkar_ot_amount("normal"), 10 * (self.BASIC / 240.0) * 1.25, places=2)

    def test_zero_divisor_is_refused(self):
        """A zero divisor would silently make every overtime hour worth nothing."""
        with self.assertRaises(ValidationError):
            self.company.sukkar_payroll_divisor_days = 0
            self.company.flush_recordset()

    def test_overtime_rate_below_one_is_refused(self):
        with self.assertRaises(ValidationError):
            self.company.sukkar_ot_rate_normal = 0.5
            self.company.flush_recordset()

    def test_negative_minimum_block_is_refused(self):
        with self.assertRaises(ValidationError):
            self.company.sukkar_ot_min_block_hours = -1.0
            self.company.flush_recordset()

    def test_negative_cap_is_refused(self):
        with self.assertRaises(ValidationError):
            self.contract.sukkar_ot_cap_hours = -5.0
            self.contract.flush_recordset()

    # -------------------------------------------------------- the schedule itself
    def test_friday_is_not_a_working_day(self):
        """Friday's absence from the calendar is what routes it to the ×1.5 rate."""
        days = set(self.calendar.attendance_ids.mapped("dayofweek"))
        self.assertNotIn("4", days, "Friday must have no attendance lines")
        self.assertEqual(len(days), 6, "Saturday to Thursday, six working days")

    def test_schedule_is_forty_eight_paid_hours(self):
        total = sum(a.hour_to - a.hour_from
                    for a in self.calendar.attendance_ids
                    if a.day_period != "lunch")
        self.assertAlmostEqual(total, 48.0, places=2,
                               msg="8 paid hours x 6 days — Kuwait's statutory maximum")

    def test_the_unpaid_break_is_on_the_calendar(self):
        """The hour is unpaid, and the attendance machine only knows that if the
        calendar says so. Without it every ordinary day pays an hour of overtime."""
        lunch = self.calendar.attendance_ids.filtered(
            lambda a: a.day_period == "lunch")
        self.assertEqual(len(lunch), 6, "one unpaid hour on each working day")
        self.assertAlmostEqual(
            sum(a.hour_to - a.hour_from for a in lunch), 6.0, places=2)

    # ------------------------------------------------------- part months (wages)
    def test_a_joiner_is_paid_for_the_days_he_worked(self):
        """A flat contract.wage paid somebody who started on the 25th a whole
        month: 270 KWD for five days."""
        self.company.write({"sukkar_prorate_basic": True,
                            "sukkar_prorate_basis_days": 0})
        self.contract.date_start = date(2026, 7, 25)
        slip = self._payslip(date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))
        # 7 days of a 31-day month.
        self.assertAlmostEqual(self._lines(slip)["BASIC"],
                               round(self.BASIC * 7 / 31, 3), places=2)
        self.contract.date_start = date(2026, 1, 1)

    def test_a_leaver_is_not_paid_past_the_end_of_his_contract(self):
        self.company.write({"sukkar_prorate_basic": True,
                            "sukkar_prorate_basis_days": 0})
        self.contract.date_end = date(2026, 7, 10)
        slip = self._payslip(date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))
        self.assertAlmostEqual(self._lines(slip)["BASIC"],
                               round(self.BASIC * 10 / 31, 3), places=2)
        self.contract.date_end = False

    def test_two_runs_in_one_month_do_not_pay_the_wage_twice(self):
        """The overtime cap is already shared across a split month; the basic
        was not, so a correction run paid the whole wage bill again."""
        self.company.write({"sukkar_prorate_basic": True,
                            "sukkar_prorate_basis_days": 0})
        first = self._payslip(date_from=date(2026, 7, 1), date_to=date(2026, 7, 15))
        second = self._payslip(date_from=date(2026, 7, 16), date_to=date(2026, 7, 31))
        total = self._lines(first)["BASIC"] + self._lines(second)["BASIC"]
        self.assertAlmostEqual(total, self.BASIC, places=2,
                               msg="one month of work is one month of basic pay")

    def test_a_full_month_is_still_the_full_wage(self):
        self.company.write({"sukkar_prorate_basic": True,
                            "sukkar_prorate_basis_days": 0})
        slip = self._payslip(date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))
        self.assertAlmostEqual(self._lines(slip)["BASIC"], self.BASIC, places=2)

    def test_the_part_month_basis_is_a_setting(self):
        """Kuwait practice divides by a fixed 26 or 30; the shop chooses."""
        self.company.write({"sukkar_prorate_basic": True,
                            "sukkar_prorate_basis_days": 26})
        self.contract.date_start = date(2026, 7, 26)
        slip = self._payslip(date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))
        self.assertAlmostEqual(self._lines(slip)["BASIC"],
                               round(self.BASIC * 6 / 26, 3), places=2)
        self.contract.date_start = date(2026, 1, 1)
        self.company.write({"sukkar_prorate_basis_days": 0})

    def test_proration_can_be_switched_off(self):
        self.company.write({"sukkar_prorate_basic": False})
        self.contract.date_start = date(2026, 7, 25)
        slip = self._payslip(date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))
        self.assertAlmostEqual(self._lines(slip)["BASIC"], self.BASIC, places=2)
        self.contract.date_start = date(2026, 1, 1)
        self.company.write({"sukkar_prorate_basic": True})

    # -------------------------------------------------- the four input codes
    def test_the_four_input_codes_are_offered_not_remembered(self):
        """Typed from memory, one lowercase letter paid a month of overtime
        nothing — with no line on the payslip and no warning."""
        codes = set(self.structure.rule_ids.mapped("input_ids.code"))
        self.assertEqual(codes, {"OT_N", "OT_R", "OT_H", "LATE_MIN"})

    def test_a_code_typed_in_lower_case_still_pays(self):
        slip = self._payslip({"ot_n": 10.0})
        self._assert_ot(slip, "normal", 10.0)

    def test_a_code_with_a_stray_space_still_pays(self):
        slip = self._payslip({"OT_N ": 10.0})
        self._assert_ot(slip, "normal", 10.0)
