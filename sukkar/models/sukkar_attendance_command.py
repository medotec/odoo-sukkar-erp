from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SukkarAttendanceCommand(models.Model):
    """Something the ERP wants the attendance machine to do.

    The machine cannot be reached from the server — it sits behind the shop's
    router — but it calls in every few seconds asking "anything for me?". So an
    instruction is queued here, handed over the next time it asks, and the
    machine reports back whether it worked. That is how the staff list travels
    from the ERP to the machine (owner, 1 Oct 2026).
    """
    _name = "sukkar.attendance.command"
    _description = "Attendance Machine Instruction"
    _order = "id desc"

    device_id = fields.Many2one("sukkar.attendance.device", required=True,
                                ondelete="cascade", index=True)
    employee_id = fields.Many2one("hr.employee", string="Employee", index=True)
    name = fields.Char(string="What", required=True)
    command = fields.Char(string="Instruction", required=True)
    state = fields.Selection(
        [("pending", "Waiting for the machine"),
         ("sent", "Handed to the machine"),
         ("done", "Done"),
         ("failed", "The machine refused it")],
        default="pending", required=True, index=True)
    result = fields.Char(string="Machine's answer", readonly=True)
    sent_at = fields.Datetime(readonly=True)
    done_at = fields.Datetime(readonly=True)

    def _sukkar_wire(self):
        """The line the machine reads: C:<number>:<instruction>."""
        return "\n".join("C:%d:%s" % (cmd.id, cmd.command) for cmd in self)

    @api.model
    def _sukkar_user_line(self, employee):
        """The machine's own record for one person.

        The machine stores short plain-Latin names, so the English name is sent
        and cut to the 24 characters it holds.
        """
        name = (employee.name or "").strip()
        name = "".join(ch for ch in name if ord(ch) < 128)[:24] or employee.sukkar_device_user_id
        return ("DATA UPDATE USERINFO PIN=%s\tName=%s\tPri=0\tPasswd=\tCard=\t"
                "Grp=1\tTZ=0000000100000000\tVerify=0" % (
                    employee.sukkar_device_user_id, name))


class SukkarAttendanceDeviceCommands(models.Model):
    _inherit = "sukkar.attendance.device"

    command_ids = fields.One2many("sukkar.attendance.command", "device_id",
                                  string="Instructions")
    pending_command_count = fields.Integer(compute="_compute_pending_command_count")

    def _compute_pending_command_count(self):
        for device in self:
            device.pending_command_count = len(device.command_ids.filtered(
                lambda c: c.state in ("pending", "sent")))

    def action_send_all_staff(self):
        """Put every employee on the machine, numbering anyone who has no
        Attendance ID yet."""
        self.ensure_one()
        employees = self.env["hr.employee"].search([])
        employees._sukkar_ensure_device_id()
        employees._sukkar_queue_for_machines(devices=self)
        return self._sukkar_notify(_(
            "%(count)d staff queued. The machine picks them up within a few "
            "seconds.", count=len(employees)))

    def _sukkar_notify(self, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"message": message, "type": "success", "sticky": False},
        }


class HrEmployeeMachine(models.Model):
    _inherit = "hr.employee"

    sukkar_machine_command_ids = fields.One2many(
        "sukkar.attendance.command", "employee_id", string="Sent to machine")
    sukkar_machine_status = fields.Char(
        string="On the machine", compute="_compute_sukkar_machine_status")

    def _compute_sukkar_machine_status(self):
        labels = dict(self.env["sukkar.attendance.command"]._fields["state"].selection)
        for employee in self:
            last = employee.sukkar_machine_command_ids[:1]
            employee.sukkar_machine_status = (
                "%s — %s" % (last.name, labels.get(last.state)) if last
                else _("Not sent yet"))

    def _sukkar_ensure_device_id(self):
        """Give anyone without an Attendance ID the next free number.

        99 and above are left for managers' own test fingers, so staff are
        numbered from 1 upwards below that.
        """
        taken = set(self.sudo().search([("sukkar_device_user_id", "!=", False)])
                    .mapped("sukkar_device_user_id"))
        number = 1
        for employee in self.filtered(lambda e: not e.sukkar_device_user_id):
            while str(number) in taken:
                number += 1
            employee.sukkar_device_user_id = str(number)
            taken.add(str(number))

    def _sukkar_queue_for_machines(self, devices=None, enroll=False):
        devices = devices or self.env["sukkar.attendance.device"].search(
            [("protocol", "=", "adms")])
        if not devices:
            raise UserError(_(
                "No attendance machine is set up to receive staff. Add one "
                "under Attendances → Attendance Machines."))
        Command = self.env["sukkar.attendance.command"]
        for employee in self:
            for device in devices:
                Command.create({
                    "device_id": device.id,
                    "employee_id": employee.id,
                    "name": _("Add %s (no. %s)", employee.name,
                              employee.sukkar_device_user_id),
                    "command": Command._sukkar_user_line(employee),
                })
                if enroll:
                    # The machine asks this person to press their finger now.
                    Command.create({
                        "device_id": device.id,
                        "employee_id": employee.id,
                        "name": _("Enrol finger for %s", employee.name),
                        "command": "ENROLL_FP PIN=%s\tFID=0\tRETRY=3\tOVERWRITE=1"
                                   % employee.sukkar_device_user_id,
                    })

    def action_sukkar_send_to_machine(self):
        """Put this person on the attendance machine."""
        self._sukkar_ensure_device_id()
        self._sukkar_queue_for_machines()
        return self.env["sukkar.attendance.device"]._sukkar_notify(_(
            "Sent. %(name)s appears on the machine as no. %(number)s within a "
            "few seconds — then enrol the finger there.",
            name=self[:1].name, number=self[:1].sukkar_device_user_id))

    def action_sukkar_enroll_on_machine(self):
        """Put this person on the machine and make it ask for their finger."""
        self._sukkar_ensure_device_id()
        self._sukkar_queue_for_machines(enroll=True)
        return self.env["sukkar.attendance.device"]._sukkar_notify(_(
            "Sent. The machine will ask %(name)s to press a finger three times.",
            name=self[:1].name))
