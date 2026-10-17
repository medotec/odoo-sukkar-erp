from datetime import datetime

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarAttendanceAdms(HttpCase):
    """The attendance machine talking to the server on its own.

    The shop's machine is in Kuwait and the ERP is abroad, so the machine
    dials out and posts its punches. These tests drive that doorway exactly as
    the machine does — a plain HTTP call with a tab-separated body — because
    the part that can quietly lose someone's hours is the parsing, not the
    pairing.
    """

    SN = "TESTSN0001"

    def setUp(self):
        super().setUp()
        self.device = self.env["sukkar.attendance.device"].create({
            "name": "Test Door",
            "protocol": "adms",
            "serial_number": self.SN,
        })
        self.employee = self.env["hr.employee"].create({
            "name": "Punch Tester",
            "sukkar_device_user_id": "77",
        })

    def _post(self, body, serial=None, table="ATTLOG"):
        return self.url_open(
            "/iclock/cdata?SN=%s&table=%s&Stamp=9999" % (serial or self.SN, table),
            data=body.encode(), headers={"Content-Type": "text/plain"})

    def test_the_machine_is_told_its_settings_when_it_says_hello(self):
        response = self.url_open("/iclock/cdata?SN=%s&options=all" % self.SN)
        body = response.text
        self.assertIn("GET OPTION FROM: %s" % self.SN, body)
        # Without these two the machine batches punches instead of sending them
        # as they happen, which is the whole reason for this link.
        self.assertIn("Realtime=1", body)
        self.assertIn("TransFlag=", body)

    def test_a_punch_lands_as_kuwait_time_on_the_right_person(self):
        self._post("77\t2026-09-30 09:15:00\t0\t1\t0\t\n")
        punch = self.env["sukkar.attendance.punch"].search(
            [("device_id", "=", self.device.id)])
        self.assertEqual(len(punch), 1)
        self.assertEqual(punch.employee_id, self.employee)
        # 09:15 in the shop is 06:15 UTC — Kuwait never moves its clocks.
        self.assertEqual(punch.punched_at, datetime(2026, 9, 30, 6, 15))

    def test_a_number_nobody_is_paired_to_is_kept_and_flagged(self):
        """The hours of a person nobody paired must never be thrown away."""
        self._post("999\t2026-09-30 08:00:00\t0\t1\t0\t\n")
        punch = self.env["sukkar.attendance.punch"].search(
            [("device_id", "=", self.device.id)])
        self.assertEqual(punch.device_user_id, "999")
        self.assertFalse(punch.employee_id)
        self.assertEqual(punch.state, "unmatched")

    def test_a_machine_nobody_approved_is_recorded_but_not_believed(self):
        """An open door on the internet: a stranger's serial writes nothing."""
        response = self._post("77\t2026-09-30 09:15:00\t0\t1\t0\t\n",
                              serial="STRANGER1")
        self.assertEqual(response.text.strip(), "OK: 0")
        stranger = self.env["sukkar.attendance.device"].with_context(
            active_test=False).search([("serial_number", "=", "STRANGER1")])
        self.assertTrue(stranger, "the attempt is recorded so it can be seen")
        self.assertFalse(stranger.active)
        self.assertFalse(self.env["sukkar.attendance.punch"].search_count(
            [("device_id", "=", stranger.id)]))

    def test_the_same_batch_sent_twice_is_stored_once(self):
        """A machine repeats a batch it thinks failed; hours must not double."""
        line = "77\t2026-09-30 09:15:00\t0\t1\t0\t\n"
        self._post(line)
        self._post(line)
        self.assertEqual(self.env["sukkar.attendance.punch"].search_count(
            [("device_id", "=", self.device.id)]), 1)

    def test_anything_that_is_not_a_punch_is_acknowledged_and_ignored(self):
        """Users, fingerprints and photos are the machine's own business."""
        response = self._post("USER PIN=77\tName=Punch Tester\n", table="OPERLOG")
        self.assertEqual(response.text.strip(), "OK: 0")
        self.assertFalse(self.env["sukkar.attendance.punch"].search_count(
            [("device_id", "=", self.device.id)]))

    def test_the_machine_is_never_given_an_order_it_cannot_answer(self):
        response = self.url_open("/iclock/getrequest?SN=%s&INFO=2.4.1" % self.SN)
        self.assertEqual(response.text.strip(), "OK")
        self.device.invalidate_recordset()
        self.assertTrue(self.device.last_seen, "the call in is recorded")
