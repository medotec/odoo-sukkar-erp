"""The doorway the attendance machine talks to.

The shop's machine (ZKTeco BioPro SA40, bought 30 Sep 2026) sits in Kuwait; the
ERP sits on a server abroad. Nothing on the shop's router may be opened for a
server to reach in, so the machine does the calling: ZKTeco terminals can be
told a "cloud server" address, and from then on they dial out over plain HTTP
and post every punch as it happens. That protocol is what this file speaks.

How a conversation goes:

    GET  /iclock/cdata?SN=…&options=all   the machine says hello and asks for
                                          its settings; we answer with them
    POST /iclock/cdata?SN=…&table=ATTLOG  a punch (or a batch of them)
    POST /iclock/cdata?SN=…&table=OPERLOG a user was added, a finger enrolled…
    GET  /iclock/getrequest?SN=…          "anything for me to do?" — we say no
    POST /iclock/devicecmd?SN=…           the result of a command we sent

Every reply is plain text and must be exactly what the firmware expects, or the
machine decides the server is broken and stops sending. "OK" and "OK: <n>" are
the whole vocabulary.

Two rules keep this safe on an open port:

1. **Only a machine we already know is believed.** The serial number is the
   only thing a ZKTeco machine proves about itself, so an unknown serial is
   recorded as a new, switched-off machine and its punches are NOT stored.
   Someone has to tick it Active first — one click, and it cannot be done by
   whoever is knocking.
2. **Nothing here can fail loudly.** An exception would make the machine retry
   the same batch forever; every path answers, and problems go to the log and
   to the machine's own record.
"""
import logging

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

# What the machine is told at handshake. Delay is how often it calls back when
# idle, TransInterval how often it flushes; both in the units ZKTeco uses
# (seconds and minutes). Realtime=1 means "send a punch the moment it happens",
# which is the whole point of this.
DEVICE_OPTIONS = "\n".join([
    "GET OPTION FROM: %(sn)s",
    "Stamp=%(stamp)s",
    "OpStamp=%(stamp)s",
    "ErrorDelay=30",
    "Delay=10",
    "TransTimes=00:00;12:00",
    "TransInterval=1",
    "TransFlag=TransData AttLog OpLog AttPhoto EnrollUser ChgUser EnrollFP "
    "ChgFP UserPic",
    "TimeZone=3",
    "Realtime=1",
    "Encrypt=0",
])


class SukkarAttendanceAdms(http.Controller):

    # ------------------------------------------------------------------ utils
    def _device(self, serial, info=None):
        """The machine record for this serial, creating a switched-off one for
        a serial nobody has approved yet."""
        if not serial:
            return None
        Device = request.env["sukkar.attendance.device"].sudo()
        device = Device.with_context(active_test=False).search(
            [("serial_number", "=", serial)], limit=1)
        if not device:
            device = Device.create({
                "name": "Unknown machine %s" % serial,
                "serial_number": serial,
                "protocol": "adms",
                "active": False,
                "last_error": (
                    "This machine called in but nobody has approved it yet. "
                    "If it is the shop's own machine, tick Active — until "
                    "then its punches are ignored."),
            })
            _logger.warning(
                "Attendance: unknown machine %s called in; recorded as "
                "switched off.", serial)
        device.write({
            "last_seen": fields.Datetime.now(),
            "device_info": info or device.device_info,
        })
        return device

    def _body(self):
        """The posted block, whatever the firmware labelled it.

        A punch block is tab-separated plain text, but some firmwares post it
        with a form content-type. The web server then parses it as a form and
        the raw stream comes back empty, with each line sitting in the form as
        a key of its own — so both places are checked before giving up.
        """
        body = request.httprequest.get_data(as_text=True) or ""
        if body.strip():
            return body
        form = request.httprequest.form
        if not form:
            return ""
        return "\n".join(
            key if not value else "%s=%s" % (key, value)
            for key, value in form.items(multi=True))

    def _store(self, device, body):
        """Turn one posted ATTLOG block into punches.

        Each line is tab-separated and starts the same way on every ZKTeco
        firmware: the number the machine knows the person by, then the time it
        happened in the shop's own clock, then how it was read.
        """
        punches = []
        for line in (body or "").splitlines():
            parts = line.strip().split("\t")
            if len(parts) < 2 or not parts[0]:
                continue
            try:
                stamp = fields.Datetime.to_datetime(parts[1].strip())
            except (ValueError, TypeError):
                _logger.warning("Attendance: unreadable time %r from %s",
                                parts[1], device.serial_number)
                continue
            if not stamp:
                continue
            punches.append({
                "device_user_id": parts[0].strip(),
                "punched_at_local": stamp,
                "raw_status": "/".join(p.strip() for p in parts[2:4] if p.strip()),
            })
        if not punches:
            return 0
        stored = request.env["sukkar.attendance.punch"].sudo()._sukkar_store(
            device, punches)
        device.write({"last_sync": fields.Datetime.now(), "last_error": False})
        return len(stored)

    # --------------------------------------------------------------- handshake
    @http.route("/iclock/cdata", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def cdata_options(self, SN=None, **kw):
        """"Here I am — what are my settings?" — asked once at every start-up."""
        device = self._device(SN, info=kw.get("DeviceType") or kw.get("options"))
        if not device:
            return "OK"
        return DEVICE_OPTIONS % {"sn": SN, "stamp": "9999"}

    # ------------------------------------------------------------------ punches
    @http.route("/iclock/cdata", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def cdata_push(self, SN=None, table=None, **kw):
        """A punch, or a batch of them, or a note that a user changed."""
        device = self._device(SN)
        if not device:
            return "OK"
        body = self._body()
        if not device.active:
            _logger.warning(
                "Attendance: ignored %d bytes from unapproved machine %s",
                len(body), SN)
            return "OK: 0"
        if (table or "").upper() != "ATTLOG":
            # Users, fingerprints, photos and the rest are the machine's own
            # business. They are acknowledged so it does not retry, and left
            # alone: pairing is done in Odoo, against the Attendance ID.
            return "OK: 0"
        try:
            count = self._store(device, body)
        except Exception as exc:  # noqa: BLE001 — never make the machine retry forever
            device.sudo().write({"last_error": str(exc)})
            _logger.exception("Attendance: could not store punches from %s", SN)
            return "OK: 0"
        return "OK: %d" % count

    # ------------------------------------------------------------- housekeeping
    @http.route("/iclock/getrequest", type="http", auth="public",
                methods=["GET", "POST"], csrf=False, save_session=False)
    def getrequest(self, SN=None, **kw):
        """"Any orders for me?" — asked every few seconds. Anything the ERP
        queued for this machine (a new member of staff, a finger to enrol) is
        handed over here; otherwise the answer is simply OK."""
        device = self._device(SN)
        if not device or not device.active:
            return "OK"
        pending = request.env["sukkar.attendance.command"].sudo().search(
            [("device_id", "=", device.id), ("state", "=", "pending")],
            order="id", limit=20)
        if not pending:
            return "OK"
        pending.write({"state": "sent", "sent_at": fields.Datetime.now()})
        return pending._sukkar_wire()

    @http.route("/iclock/devicecmd", type="http", auth="public",
                methods=["GET", "POST"], csrf=False, save_session=False)
    def devicecmd(self, SN=None, **kw):
        """The machine reporting back on what it was asked to do, one line per
        instruction: ID=<number>&Return=<0 for success>&CMD=<what>."""
        device = self._device(SN)
        if not device:
            return "OK"
        Command = request.env["sukkar.attendance.command"].sudo()
        for line in self._body().splitlines():
            parts = dict(
                bit.split("=", 1) for bit in line.strip().split("&") if "=" in bit)
            try:
                command = Command.browse(int(parts.get("ID", "0")))
            except ValueError:
                continue
            if not command.exists() or command.device_id != device:
                continue
            ok = parts.get("Return", "").strip() in ("0", "")
            command.write({
                "state": "done" if ok else "failed",
                "result": line.strip()[:200],
                "done_at": fields.Datetime.now(),
            })
        return "OK"

    @http.route("/iclock/ping", type="http", auth="public",
                methods=["GET", "POST"], csrf=False, save_session=False)
    def ping(self, SN=None, **kw):
        self._device(SN)
        return "OK"
