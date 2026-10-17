from datetime import date, datetime, timedelta

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarAttendance(TransactionCase):
    """Punches in, payroll figures out.

    The two network drivers cannot be tested — there is no device yet — so what
    is pinned here is everything after the punches arrive: how they pair into
    attendance, and how attendance becomes the four payroll inputs. That is where
    a mistake would quietly change what somebody is paid.

    Kuwait is UTC+3 and the shop starts at 09:00 local, so 06:00 UTC throughout.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({"sukkar_tz": "Asia/Kuwait", "sukkar_late_grace_min": 15})
        cls.calendar = cls.env.ref("sukkar.sukkar_resource_calendar")
        cls.structure = cls.env.ref("sukkar.sukkar_payroll_structure")
        cls.employee = cls.env["hr.employee"].create({
            "name": "Attendance Test", "resource_calendar_id": cls.calendar.id,
            "sukkar_device_user_id": "101"})
        cls.contract = cls.env["hr.contract"].create({
            "name": "Attendance contract", "employee_id": cls.employee.id,
            "wage": 300.0, "date_start": date(2026, 1, 1),
            "resource_calendar_id": cls.calendar.id, "struct_id": cls.structure.id,
            "state": "open", "sukkar_ot_cap_hours": 0.0})
        cls.device = cls.env["sukkar.attendance.device"].create({
            "name": "Test Door", "protocol": "file"})
        # A real Monday and a real Friday in the payslip period.
        cls.monday = date(2026, 7, 1)
        while cls.monday.weekday() != 0:
            cls.monday += timedelta(days=1)
        cls.friday = cls.monday + timedelta(days=4)

    # ------------------------------------------------------------------ helpers
    def _punch(self, day, hour, minute=0, user="101"):
        """Store one punch, expressed in Kuwait local time as the device reports it."""
        return self.env["sukkar.attendance.punch"]._sukkar_store(
            self.device, [{"device_user_id": user,
                           "punched_at_local": datetime.combine(day, datetime.min.time())
                           + timedelta(hours=hour, minutes=minute),
                           "raw_status": "0"}])

    def _attendances(self):
        return self.env["hr.attendance"].search(
            [("employee_id", "=", self.employee.id)], order="check_in")

    def _payslip(self, start, end):
        return self.env["hr.payslip"].create({
            "employee_id": self.employee.id, "contract_id": self.contract.id,
            "struct_id": self.structure.id, "date_from": start, "date_to": end,
            "name": "Attendance payslip"})

    # ------------------------------------------------------------ storing punches
    def test_local_device_time_is_stored_as_utc(self):
        """The machine shows Kuwait wall-clock; Odoo stores UTC, three hours back."""
        punch = self._punch(self.monday, 9)
        self.assertEqual(punch.punched_at.hour, 6)

    def test_the_same_punch_is_never_stored_twice(self):
        """Syncs overlap and get re-run; a repeated punch must not become two."""
        self._punch(self.monday, 9)
        again = self._punch(self.monday, 9)
        self.assertFalse(again, "a punch already seen must be skipped")
        self.assertEqual(self.env["sukkar.attendance.punch"].search_count(
            [("employee_id", "=", self.employee.id)]), 1)

    def test_an_unknown_device_id_is_flagged_not_dropped(self):
        punch = self._punch(self.monday, 9, user="999")
        self.assertEqual(punch.state, "unmatched")
        self.assertFalse(punch.employee_id)

    # ------------------------------------------------------------------ pairing
    def test_two_punches_make_one_attendance(self):
        self._punch(self.monday, 9)
        self._punch(self.monday, 18)
        attendances = self._attendances()
        self.assertEqual(len(attendances), 1)
        self.assertEqual(attendances.check_in.hour, 6)
        self.assertEqual(attendances.check_out.hour, 15)

    def test_an_odd_punch_is_flagged_and_no_hours_are_invented(self):
        """Someone forgot to clock out.

        No attendance is created for the orphan: we do not know when they left,
        and inventing a record would either block the following days — Odoo
        refuses a second attendance while one is open — or hand them hours nobody
        worked. The punch is flagged instead, for a human to resolve.
        """
        self._punch(self.monday, 9)
        self._punch(self.monday, 13)
        self._punch(self.monday, 14)
        attendances = self._attendances()
        self.assertEqual(len(attendances), 1, "only the complete pair becomes attendance")
        self.assertTrue(attendances.check_out)
        orphan = self.env["sukkar.attendance.punch"].search([
            ("employee_id", "=", self.employee.id), ("state", "=", "missing_out")])
        self.assertEqual(len(orphan), 1, "the odd punch must be flagged, not dropped")

    # -------------------------------------------------------------- derivation
    def test_a_normal_day_beyond_eight_hours_is_overtime(self):
        self._punch(self.monday, 9)
        self._punch(self.monday, 20)          # 11 hours on the premises
        slip = self._payslip(self.monday, self.monday)
        totals = slip._sukkar_attendance_totals()
        # The day is 9 hours INCLUDING the unpaid hour, so 11 hours present is
        # 10 worked against 8 paid.
        self.assertAlmostEqual(totals["normal"], 2.0, places=2,
                               msg="11 hours present, less the unpaid break, is 2 hours overtime")
        self.assertEqual(totals["rest"], 0.0)

    def test_a_normal_day_within_hours_is_not_overtime(self):
        self._punch(self.monday, 9)
        self._punch(self.monday, 17)
        slip = self._payslip(self.monday, self.monday)
        self.assertEqual(slip._sukkar_attendance_totals()["normal"], 0.0)

    def test_every_hour_on_friday_is_rest_day_overtime(self):
        """Friday has no attendance lines on the calendar — that is what makes it
        the rest day, and every hour worked on it premium."""
        self._punch(self.friday, 10)
        self._punch(self.friday, 15)
        slip = self._payslip(self.friday, self.friday)
        totals = slip._sukkar_attendance_totals()
        self.assertAlmostEqual(totals["rest"], 5.0, places=2)
        self.assertEqual(totals["normal"], 0.0)

    def test_a_public_holiday_takes_precedence(self):
        self.env["resource.calendar.leaves"].create({
            "name": "National Day", "calendar_id": self.calendar.id,
            "date_from": datetime.combine(self.monday, datetime.min.time()),
            "date_to": datetime.combine(self.monday, datetime.max.time())})
        self._punch(self.monday, 9)
        self._punch(self.monday, 15)          # 6 present, less the unpaid hour
        slip = self._payslip(self.monday, self.monday)
        totals = slip._sukkar_attendance_totals()
        self.assertAlmostEqual(totals["holiday"], 5.0, places=2)
        self.assertEqual(totals["normal"], 0.0)

    # ------------------------------------------------------------------ lateness
    def test_arriving_within_the_grace_is_not_late(self):
        self._punch(self.monday, 9, 14)       # 14 minutes, grace is 15
        self._punch(self.monday, 18)
        slip = self._payslip(self.monday, self.monday)
        self.assertEqual(slip._sukkar_attendance_totals()["late_minutes"], 0.0)

    def test_only_the_time_beyond_the_grace_counts(self):
        self._punch(self.monday, 9, 45)       # 45 late, 15 forgiven
        self._punch(self.monday, 18)
        slip = self._payslip(self.monday, self.monday)
        self.assertAlmostEqual(
            slip._sukkar_attendance_totals()["late_minutes"], 30.0, places=1)

    def test_lateness_is_not_measured_on_a_rest_day(self):
        self._punch(self.friday, 14)
        self._punch(self.friday, 18)
        slip = self._payslip(self.friday, self.friday)
        self.assertEqual(slip._sukkar_attendance_totals()["late_minutes"], 0.0)

    # ------------------------------------------------------- onto the payslip
    def test_reading_attendance_fills_the_payslip_inputs(self):
        self._punch(self.monday, 9, 45)       # 30 min late after grace
        self._punch(self.monday, 20)          # 10h15 present
        slip = self._payslip(self.monday, self.monday)
        slip.action_sukkar_fill_from_attendance()
        codes = {line.code: line.amount for line in slip.input_line_ids}
        self.assertIn("OT_N", codes)
        self.assertIn("LATE_MIN", codes)
        self.assertAlmostEqual(codes["LATE_MIN"], 30.0, places=1)

    def test_reading_attendance_twice_does_not_double_the_inputs(self):
        self._punch(self.monday, 9)
        self._punch(self.monday, 20)
        slip = self._payslip(self.monday, self.monday)
        slip.action_sukkar_fill_from_attendance()
        first = {l.code: l.amount for l in slip.input_line_ids}
        slip.action_sukkar_fill_from_attendance()
        second = {l.code: l.amount for l in slip.input_line_ids}
        self.assertEqual(first, second, "re-reading must replace, never accumulate")

    def test_the_machine_and_hand_entry_produce_the_same_pay(self):
        """The whole point: nothing downstream changes when hours come from a door."""
        self._punch(self.monday, 9)
        self._punch(self.monday, 21)          # 12 present, less the break: 3 h
        from_machine = self._payslip(self.monday, self.monday)
        from_machine.action_sukkar_fill_from_attendance()
        by_hand = self._payslip(self.monday + timedelta(days=1),
                                self.monday + timedelta(days=1))
        self.env["hr.payslip.input"].create({
            "payslip_id": by_hand.id, "contract_id": self.contract.id,
            "name": "OT_N", "code": "OT_N", "amount": 3.0})
        by_hand.invalidate_recordset()
        self.assertAlmostEqual(from_machine.sukkar_ot_amount("normal"),
                               by_hand.sukkar_ot_amount("normal"), places=3)

    # --------------------------------------------- regressions found by grilling
    def test_a_forgotten_clock_out_does_not_swallow_the_next_day(self):
        """The worst attendance bug there is, and the commonest cause.

        Someone clocks in and forgets to clock out. If the next day's first punch
        is allowed to close that record, the two merge into a 24-hour attendance
        and payroll reads sixteen hours of overtime nobody worked.
        """
        tuesday = self.monday + timedelta(days=1)
        self._punch(self.monday, 9)            # in, never out
        self._punch(tuesday, 9)
        self._punch(tuesday, 20)
        slip = self._payslip(self.monday, tuesday)
        totals = slip._sukkar_attendance_totals()
        # Tuesday genuinely earns two hours (11 present, less the unpaid hour,
        # against an 8-hour day). What must never appear is Monday's phantom sixteen.
        self.assertAlmostEqual(totals["normal"], 2.0, places=2,
                               msg="only Tuesday's real overtime may be counted")
        monday_records = self.env["hr.attendance"].search([
            ("employee_id", "=", self.employee.id),
            ("check_in", "<", datetime.combine(tuesday, datetime.min.time()))])
        self.assertFalse(monday_records,
                         "no attendance may be invented for a day that never closed")
        self.assertTrue(self.env["sukkar.attendance.punch"].search([
            ("employee_id", "=", self.employee.id), ("state", "=", "missing_out")]),
            "Monday's orphan punch must be flagged for a human")

    def test_a_managers_correction_survives_the_next_sync(self):
        """The one edit that must never be overwritten — it exists because the
        machine was wrong."""
        self._punch(self.monday, 9)
        self._punch(self.monday, 13)           # one complete pair
        attendance = self.env["hr.attendance"].search([
            ("employee_id", "=", self.employee.id)])
        self.assertTrue(attendance.sukkar_punch_managed)
        corrected = datetime.combine(self.monday, datetime.min.time()) + timedelta(hours=15)
        attendance.check_out = corrected       # a manager corrects the times
        self.assertFalse(attendance.sukkar_punch_managed,
                         "a hand edit takes ownership of the record")
        self._punch(self.monday, 19)           # a later punch arrives for that day
        attendance.invalidate_recordset()
        self.assertTrue(attendance.exists(), "the corrected record must survive")
        self.assertEqual(len(self._attendances()), 1,
                         "the machine must not rebuild a day a person has corrected")
        self.assertEqual(attendance.check_out, corrected,
                         "the correction must not be silently undone")

    def test_a_holiday_does_not_spill_onto_the_day_before(self):
        """Kuwait is UTC+3, so a holiday is stored from 21:00 the previous day.
        Comparing those UTC values by date paid the day BEFORE a holiday at ×2."""
        tuesday = self.monday + timedelta(days=1)
        self.env["resource.calendar.leaves"].create({
            "name": "Tuesday only", "calendar_id": self.calendar.id,
            "date_from": datetime.combine(tuesday, datetime.min.time()) - timedelta(hours=3),
            "date_to": datetime.combine(tuesday, datetime.min.time()) + timedelta(hours=21)})
        self._punch(self.monday, 9)
        self._punch(self.monday, 20)
        slip = self._payslip(self.monday, self.monday)
        totals = slip._sukkar_attendance_totals()
        self.assertEqual(totals["holiday"], 0.0,
                         "Monday is an ordinary working day, not a holiday")
        self.assertAlmostEqual(totals["normal"], 2.0, places=2)

    # ------------------------------------ deciding what to do about a missed punch
    def _orphan(self, day=None):
        """Create a day that ended on an odd punch, and return that punch."""
        self._punch(day or self.monday, 9)
        return self.env["sukkar.attendance.punch"].search([
            ("employee_id", "=", self.employee.id), ("state", "=", "missing_out")],
            order="punched_at desc", limit=1)

    def _decide(self, punch, decision, **kw):
        wizard = self.env["sukkar.missing.punch.wizard"].create(
            dict({"punch_ids": [(6, 0, punch.ids)], "decision": decision}, **kw))
        wizard.action_apply()
        punch.invalidate_recordset()
        return wizard

    def test_an_undecided_day_contributes_no_hours(self):
        """Until someone decides, the day is worth nothing — not a guess."""
        punch = self._orphan()
        self.assertEqual(punch.resolution, "pending")
        slip = self._payslip(self.monday, self.monday)
        totals = slip._sukkar_attendance_totals()
        self.assertEqual(totals["normal"], 0.0)
        self.assertEqual(totals["late_minutes"], 0.0)

    def test_manual_clock_out_makes_the_day_pay_normally(self):
        punch = self._orphan()
        # Datetime fields are stored naive UTC — the form converts for the user,
        # so a test has to do the same. 11 hours after the 09:00 clock-in.
        out = punch.punched_at + timedelta(hours=11)
        self._decide(punch, "manual_out", manual_check_out=out)
        self.assertEqual(punch.state, "paired")
        slip = self._payslip(self.monday, self.monday)
        # 11 hours present, less the unpaid hour, against an 8-hour day.
        self.assertAlmostEqual(
            slip._sukkar_attendance_totals()["normal"], 2.0, places=2)

    def test_a_manual_clock_out_survives_a_later_sync(self):
        """A person decided this; the machine must not overwrite it."""
        punch = self._orphan()
        out = punch.punched_at + timedelta(hours=11)
        self._decide(punch, "manual_out", manual_check_out=out)
        self._punch(self.monday, 21)
        attendance = self._attendances()
        self.assertEqual(len(attendance), 1)
        self.assertTrue(attendance.check_out)

    def test_a_penalty_comes_off_overtime(self):
        """Never off basic — the same rule the shop applies to lateness."""
        self._punch(self.monday, 9)
        self._punch(self.monday, 20)          # a full day, 2 hours overtime
        punch = self._orphan(self.monday + timedelta(days=1))
        self._decide(punch, "penalty", penalty_minutes=45)
        slip = self._payslip(self.monday, self.monday + timedelta(days=1))
        totals = slip._sukkar_attendance_totals()
        self.assertEqual(totals["late_minutes"], 45.0)
        self.assertAlmostEqual(totals["normal"], 2.0, places=2,
                               msg="the penalty is applied as minutes, not by deleting hours")

    def test_cancelling_overtime_keeps_the_day_but_not_the_premium(self):
        punch = self._orphan()
        self._decide(punch, "no_overtime")
        # A second, complete day proves only the cancelled day is affected.
        tuesday = self.monday + timedelta(days=1)
        self._punch(tuesday, 9)
        self._punch(tuesday, 20)              # 2 hours overtime
        slip = self._payslip(self.monday, tuesday)
        self.assertAlmostEqual(
            slip._sukkar_attendance_totals()["normal"], 2.0, places=2,
            msg="only the cancelled day loses its overtime")

    def test_a_manual_clock_out_before_the_clock_in_is_refused(self):
        punch = self._orphan()
        earlier = punch.punched_at - timedelta(hours=1)
        with self.assertRaises(ValidationError):
            self._decide(punch, "manual_out", manual_check_out=earlier)

    def test_a_penalty_of_zero_is_refused(self):
        punch = self._orphan()
        with self.assertRaises(ValidationError):
            self._decide(punch, "penalty", penalty_minutes=0)

    # ------------------------------------------------------- counting the pattern
    def test_repeat_offences_are_counted_for_the_month(self):
        """One is an accident. A pattern is a management conversation."""
        first = self._orphan(self.monday)
        second = self._orphan(self.monday + timedelta(days=2))
        third = self._orphan(self.monday + timedelta(days=4))
        for punch in (first, second, third):
            punch.invalidate_recordset()
        self.assertEqual(third.occurrence_this_month, 3,
                         "the punch counts offences in the month it belongs to")

    def test_the_employee_card_counts_the_current_month(self):
        """The employee field answers 'how often lately', so it tracks today's
        month rather than whichever month the punch sits in."""
        today = date.today()
        for offset in (0, 1, 2):
            day = today.replace(day=1) + timedelta(days=offset)
            self._punch(day, 9)
        self.employee.invalidate_recordset()
        self.assertEqual(self.employee.sukkar_missing_out_count, 3)

    def test_the_count_does_not_bleed_across_months(self):
        self._orphan(self.monday)
        next_month = (self.monday.replace(day=1) + timedelta(days=40)).replace(day=5)
        later = self._orphan(next_month)
        later.invalidate_recordset()
        self.assertEqual(later.occurrence_this_month, 1,
                         "a new month starts the count again")
