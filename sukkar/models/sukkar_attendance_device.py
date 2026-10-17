import logging
from datetime import datetime, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SukkarAttendanceDevice(models.Model):
    """The attendance machine at the staff door.

    The device has not been bought yet — the shop is choosing one against
    `docs/ATTENDANCE_MACHINE_SPEC.md`. This is written so that whichever of the
    two shortlisted families arrives, the only thing that changes is a selection
    field and an IP address:

    * **ZKTeco** — the family that dominates Kuwait. Speaks its own protocol on
      TCP 4370 and is read with the `pyzk` library.
    * **Hikvision** — reads over HTTP via ISAPI with digest authentication.

    A third option, **file import**, exists because it works *today*, with no
    device at all: point it at the export any machine's PC software produces and
    the same pipeline runs. That is deliberate — it means the payroll side can be
    proven against real punch data before a single riyal is spent on hardware.

    Nothing below has been run against real hardware, because there is none. The
    two network drivers are therefore the part to treat as unproven; the pairing
    and the payroll derivation, which is where a bug would actually cost someone
    money, are covered by tests.
    """
    _name = "sukkar.attendance.device"
    _description = "Attendance Device"
    _order = "sequence, id"

    name = fields.Char(required=True, default="Staff Door")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    protocol = fields.Selection(
        [("adms", "Cloud push — the machine sends to this server"),
         ("zk", "ZKTeco (TCP 4370)"),
         ("hikvision", "Hikvision ISAPI (HTTP)"),
         ("file", "File import (no live link)")],
        string="Protocol", required=True, default="file",
        help="How the punches arrive. 'Cloud push' is the one to use when the "
             "ERP is on the internet and the machine is in the shop: the "
             "machine dials out to the server on its own, so no router change "
             "and no shop PC are needed. The others read the machine over the "
             "shop network; 'File import' needs no machine at all.")
    serial_number = fields.Char(
        string="Serial Number", index=True, copy=False,
        help="Printed on the machine and on its box (ZKTeco: SN). A pushing "
             "machine identifies itself by this and nothing else, so it must "
             "match exactly.")
    host = fields.Char(string="IP Address",
        help="The device's fixed IP on the shop network. Ask the installer to "
             "reserve one — a device whose address moves cannot be polled.")
    port = fields.Integer(default=4370)
    device_password = fields.Char(
        string="Comm Key / Password",
        help="ZKTeco calls this the comm key (often 0). Hikvision uses the "
             "device's own username and password.")
    username = fields.Char(string="Username", default="admin")
    timeout = fields.Integer(default=10, string="Timeout (seconds)")

    last_sync = fields.Datetime(string="Last Successful Sync", readonly=True)
    last_seen = fields.Datetime(
        string="Last Heard From", readonly=True, copy=False,
        help="The last time the machine spoke to this server. A pushing "
             "machine calls in every few seconds even when nobody punches, so "
             "a stale time here means it is off, offline or pointed elsewhere.")
    device_info = fields.Text(
        string="What the machine reported", readonly=True, copy=False)
    last_error = fields.Text(string="Last Error", readonly=True)
    punch_count = fields.Integer(compute="_compute_punch_count", string="Punches")
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, required=True)

    def _compute_punch_count(self):
        counts = dict(self.env["sukkar.attendance.punch"]._read_group(
            [("device_id", "in", self.ids)],
            groupby=["device_id"], aggregates=["__count"]))
        for rec in self:
            rec.punch_count = counts.get(rec, 0)

    # ------------------------------------------------------------------ drivers
    def _driver_zk(self, since):
        """Read punches from a ZKTeco device over its own protocol.

        Imported inside the method on purpose: the library is only needed when a
        ZKTeco device is actually configured, and a missing import must never
        stop the module from loading.
        """
        self.ensure_one()
        try:
            from zk import ZK
        except ImportError as exc:
            raise UserError(_(
                "The ZKTeco library (pyzk) is not installed in this Odoo image. "
                "Add `pyzk` to the Dockerfile and rebuild.")) from exc
        conn = None
        try:
            zk = ZK(self.host, port=self.port or 4370, timeout=self.timeout or 10,
                    password=int(self.device_password or 0), force_udp=False,
                    ommit_ping=True)
            conn = zk.connect()
            records = conn.get_attendance() or []
        finally:
            if conn:
                try:
                    conn.disconnect()
                except Exception:  # noqa: BLE001 - never mask the real error
                    _logger.warning("Could not cleanly disconnect from %s", self.host)
        punches = []
        for rec in records:
            stamp = getattr(rec, "timestamp", None)
            if not stamp or (since and stamp <= since):
                continue
            punches.append({
                "device_user_id": str(getattr(rec, "user_id", "")),
                "punched_at_local": stamp,
                "raw_status": str(getattr(rec, "punch", getattr(rec, "status", ""))),
            })
        return punches

    def _driver_hikvision(self, since):
        """Read access-control events from a Hikvision device over ISAPI."""
        self.ensure_one()
        import requests
        from requests.auth import HTTPDigestAuth

        url = f"http://{self.host}:{self.port or 80}/ISAPI/AccessControl/AcsEvent?format=json"
        start = (since or (fields.Datetime.now() - timedelta(days=30)))
        payload = {
            "AcsEventCond": {
                "searchID": "sukkar",
                "searchResultPosition": 0,
                "maxResults": 1000,
                "major": 5,      # access-control event
                "minor": 0,
                "startTime": start.strftime("%Y-%m-%dT%H:%M:%S+03:00"),
                "endTime": fields.Datetime.now().strftime("%Y-%m-%dT%H:%M:%S+03:00"),
            }
        }
        response = requests.post(
            url, json=payload, timeout=self.timeout or 10,
            auth=HTTPDigestAuth(self.username or "admin", self.device_password or ""))
        response.raise_for_status()
        events = (response.json().get("AcsEvent", {}) or {}).get("InfoList", []) or []
        punches = []
        for event in events:
            raw_time = event.get("time")
            if not raw_time:
                continue
            # ISAPI returns an offset-aware string; keep the wall-clock reading and
            # let _to_utc apply the shop's zone, exactly as for ZKTeco.
            local = datetime.strptime(raw_time[:19], "%Y-%m-%dT%H:%M:%S")
            punches.append({
                "device_user_id": str(event.get("employeeNoString")
                                      or event.get("cardNo") or ""),
                "punched_at_local": local,
                "raw_status": str(event.get("attendanceStatus") or ""),
            })
        return punches

    def _driver_file(self, since):
        """No live link: punches arrive through the file-import wizard instead."""
        return []

    def _driver_adms(self, since):
        """Nothing to pull: the machine posts its punches to us as they happen."""
        return []

    def _fetch_punches(self, since=None):
        self.ensure_one()
        return {
            "adms": self._driver_adms,
            "zk": self._driver_zk,
            "hikvision": self._driver_hikvision,
            "file": self._driver_file,
        }[self.protocol](since)

    # ------------------------------------------------------------------ syncing
    def _to_utc(self, local_dt):
        """A device shows wall-clock time in the shop's zone; Odoo stores UTC."""
        tz = pytz.timezone(
            (self.company_id or self.env.company).sukkar_tz or "Asia/Kuwait")
        return tz.localize(local_dt).astimezone(pytz.utc).replace(tzinfo=None)

    def action_sync(self):
        """Pull new punches, store them raw, then rebuild attendance from them."""
        Punch = self.env["sukkar.attendance.punch"]
        for device in self:
            try:
                raw = device._fetch_punches(since=device.last_sync)
                created = Punch._sukkar_store(device, raw)
                device.write({
                    "last_sync": fields.Datetime.now(),
                    "last_error": False,
                })
                _logger.info("%s: %d new punches", device.name, len(created))
            except Exception as exc:  # noqa: BLE001 - surfaced on the device form
                device.last_error = str(exc)
                _logger.exception("Attendance sync failed for %s", device.name)
                raise
        return True

    def action_test_connection(self):
        self.ensure_one()
        if self.protocol == "adms":
            if not self.last_seen:
                raise UserError(_(
                    "This machine has not called in yet.\n\nOn the machine: "
                    "Menu → Comm → Cloud Server, set the server address and "
                    "port, switch it on, then wait a few seconds."))
            raise UserError(_(
                "%(name)s (serial %(sn)s) last called in at %(seen)s Kuwait "
                "time, and has sent %(count)d punch(es).",
                name=self.name, sn=self.serial_number or "-",
                seen=fields.Datetime.context_timestamp(self, self.last_seen)
                        .strftime("%d/%m/%Y %H:%M:%S"),
                count=self.punch_count))
        if self.protocol == "file":
            raise UserError(_(
                "This device is set to file import, so there is nothing to connect "
                "to. Import an export file instead."))
        if not self.host:
            raise UserError(_("Set the device's IP address first."))
        punches = self._fetch_punches(since=fields.Datetime.now() - timedelta(days=1))
        raise UserError(_(
            "Connected to %(name)s at %(host)s.\n\n%(count)d punch(es) in the last "
            "24 hours.",
            name=self.name, host=self.host, count=len(punches)))

    @api.model
    def _cron_sync_attendance(self):
        # A pushing machine is never polled — it calls us.
        devices = self.search([("protocol", "not in", ("file", "adms"))])
        for device in devices:
            try:
                device.action_sync()
            except Exception:  # noqa: BLE001 - one bad device must not stop the rest
                _logger.exception("Scheduled attendance sync failed for %s", device.name)
        return True

    def action_view_punches(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Punches — %s", self.name),
            "res_model": "sukkar.attendance.punch",
            "view_mode": "list,form",
            "domain": [("device_id", "=", self.id)],
            "context": {"default_device_id": self.id},
        }


class SukkarAttendancePunch(models.Model):
    """One raw punch, exactly as the machine reported it.

    Kept deliberately as a separate record rather than being turned straight into
    an ``hr.attendance``. The machine is the evidence: if a pairing turns out to
    be wrong, or someone disputes an hour, attendance has to be rebuildable from
    the original punches without going back to the device — which may by then
    have rotated its own memory.
    """
    _name = "sukkar.attendance.punch"
    _description = "Attendance Punch"
    _order = "punched_at desc, id desc"
    _rec_name = "punched_at"

    device_id = fields.Many2one("sukkar.attendance.device", required=True,
                                ondelete="cascade", index=True)
    device_user_id = fields.Char(string="Device User ID", required=True, index=True,
        help="The number the machine knows this person by. It is matched to an "
             "employee through the Attendance ID on their record.")
    employee_id = fields.Many2one("hr.employee", string="Employee", index=True)
    punched_at = fields.Datetime(string="Punched At (UTC)", required=True, index=True)
    raw_status = fields.Char(string="Device Status")
    state = fields.Selection(
        [("unmatched", "No matching employee"),
         ("new", "Not yet paired"),
         ("missing_out", "Missing clock-out"),
         ("paired", "Paired")],
        default="new", index=True, required=True,
        help="'Missing clock-out' means the day ended on an odd punch. No hours "
             "are counted for it until someone says what actually happened.")
    company_id = fields.Many2one(related="device_id.company_id", store=True)

    # --- Resolving a missing clock-out --------------------------------------
    # A flag nobody has to act on is just a tidier bug. A day that ended on an odd
    # punch has no hours at all until a manager says what happened, and there are
    # only three honest answers: they left at a known time, they left at an unknown
    # time and that carries a penalty, or the day stands but earns no overtime.
    resolution = fields.Selection(
        [("pending", "Awaiting decision"),
         ("manual_out", "Manual clock-out entered"),
         ("penalty", "Penalty applied"),
         ("no_overtime", "Overtime cancelled for the day")],
        default="pending", index=True, copy=False, string="Decision")
    manual_check_out = fields.Datetime(
        string="Manual Clock-out", copy=False,
        help="The time management says the person actually left.")
    penalty_minutes = fields.Integer(
        string="Penalty (minutes)", copy=False,
        help="Deducted from overtime, never from basic pay — the same rule the "
             "shop already applies to lateness.")
    resolution_note = fields.Char(string="Reason", copy=False)
    resolved_by = fields.Many2one("res.users", string="Decided By", readonly=True, copy=False)
    resolved_on = fields.Datetime(string="Decided On", readonly=True, copy=False)
    occurrence_this_month = fields.Integer(
        compute="_compute_occurrence_this_month", string="This Month",
        help="How many times this person has missed a clock-out in the same "
             "calendar month, this one included. A repeated pattern is a "
             "management problem, not a payroll one.")

    @api.depends("employee_id", "punched_at", "state")
    def _compute_occurrence_this_month(self):
        for punch in self:
            if punch.state != "missing_out" or not punch.employee_id or not punch.punched_at:
                punch.occurrence_this_month = 0
                continue
            start = punch.punched_at.replace(day=1, hour=0, minute=0, second=0)
            month_end = (start + timedelta(days=32)).replace(day=1)
            punch.occurrence_this_month = self.search_count([
                ("employee_id", "=", punch.employee_id.id),
                ("state", "=", "missing_out"),
                ("punched_at", ">=", start),
                ("punched_at", "<", month_end),
            ])

    def action_resolve(self):
        """Open the decision wizard for the selected missing clock-outs."""
        pending = self.filtered(lambda p: p.state == "missing_out")
        if not pending:
            raise UserError(_("Only a missing clock-out needs a decision."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Missing clock-out — decision"),
            "res_model": "sukkar.missing.punch.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_punch_ids": [(6, 0, pending.ids)]},
        }

    _sql_constraints = [
        # The same punch read twice — a re-sync, an overlapping window — must not
        # become two. This is what makes syncing safely repeatable.
        ("sukkar_punch_unique",
         "unique(device_id, device_user_id, punched_at)",
         "That punch has already been recorded."),
    ]

    @api.model
    def _sukkar_store(self, device, raw_punches):
        """Store raw punches, skipping any already seen, and resolve employees."""
        Employee = self.env["hr.employee"]
        by_device_id = {
            e.sukkar_device_user_id: e
            for e in Employee.sudo().search([("sukkar_device_user_id", "!=", False)])
        }
        created = self.browse()
        for punch in raw_punches:
            stamp = device._to_utc(punch["punched_at_local"])
            employee = by_device_id.get(punch["device_user_id"])
            exists = self.search([
                ("device_id", "=", device.id),
                ("device_user_id", "=", punch["device_user_id"]),
                ("punched_at", "=", stamp),
            ], limit=1)
            if exists:
                continue
            created |= self.create({
                "device_id": device.id,
                "device_user_id": punch["device_user_id"],
                "employee_id": employee.id if employee else False,
                "punched_at": stamp,
                "raw_status": punch.get("raw_status"),
                "state": "new" if employee else "unmatched",
            })
        created._sukkar_pair()
        return created

    def _sukkar_pair(self):
        """Turn punches into hr.attendance records, in / out / in / out.

        Two rules, both learned the hard way:

        **Pairing never crosses a day.** A person who forgets to clock out leaves
        an odd punch; if the next day's first punch is allowed to close it, the two
        merge into a twenty-four-hour attendance and the payroll reads sixteen hours
        of overtime nobody worked. So each local day is paired on its own, and an
        odd punch is left as an **open attendance for a manager to correct** — which
        is what the questionnaire asked for, and is honest about what the machine
        actually knows.

        **A correction is never overwritten.** Only records still flagged as built
        from punches are rebuilt. The moment someone edits the times by hand the
        flag clears, and the rebuild leaves that record alone — otherwise the next
        sync would silently undo the fix.
        """
        Attendance = self.env["hr.attendance"].sudo().with_context(sukkar_pairing=True)
        Slot = self.env["sukkar.delivery.slot"]
        tz = pytz.timezone(
            (self.env.company.sukkar_tz) or "Asia/Kuwait")

        for employee in self.mapped("employee_id"):
            # Only the days this sync actually touched need rebuilding.
            touched = {
                pytz.utc.localize(p.punched_at).astimezone(tz).date()
                for p in self.filtered(lambda p: p.employee_id == employee)
            }
            for day in sorted(touched):
                day_start = Slot._tz_midnight_utc(day)
                day_end = Slot._tz_midnight_utc(day + timedelta(days=1))
                punches = self.env["sukkar.attendance.punch"].search([
                    ("employee_id", "=", employee.id),
                    ("punched_at", ">=", day_start),
                    ("punched_at", "<", day_end),
                ], order="punched_at asc")
                if not punches:
                    continue
                # If a person has corrected any attendance on this day, the day is
                # theirs. Rebuilding around their record would either collide with
                # Odoo's "already checked in" rule or quietly contradict the fix
                # they made. The machine defers; the punches are still recorded.
                if Attendance.search_count([
                    ("employee_id", "=", employee.id),
                    ("check_in", ">=", day_start),
                    ("check_in", "<", day_end),
                    ("sukkar_punch_managed", "=", False),
                ]):
                    punches.write({"state": "paired"})
                    continue
                Attendance.search([
                    ("employee_id", "=", employee.id),
                    ("check_in", ">=", day_start),
                    ("check_in", "<", day_end),
                    ("sukkar_punch_managed", "=", True),
                ]).unlink()
                pending = None
                for punch in punches:
                    if pending is None:
                        pending = punch
                        continue
                    Attendance.create({
                        "employee_id": employee.id,
                        "check_in": pending.punched_at,
                        "check_out": punch.punched_at,
                        "sukkar_punch_managed": True,
                    })
                    pending = None
                punches.write({"state": "paired"})
                if pending is not None:
                    # An odd punch means we do not know when the person left, and
                    # no amount of processing can invent it. So NO attendance is
                    # created: no record, no hours, and the punch is flagged for a
                    # human to resolve.
                    #
                    # An earlier version created an open attendance instead. Odoo
                    # refuses to create any further attendance for an employee who
                    # has not checked out, so one forgotten punch would have blocked
                    # every following day — and where the constraint did not bite,
                    # the next day's punch closed the record and produced a
                    # twenty-four-hour shift with sixteen hours of overtime.
                    pending.state = "missing_out"
        return True
