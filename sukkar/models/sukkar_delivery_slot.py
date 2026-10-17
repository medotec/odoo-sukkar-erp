from datetime import datetime, time as dt_time

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SukkarDeliverySlot(models.Model):
    """A delivery time window, e.g. 18:00-20:00.

    The shop never promises an exact minute — every delivery is booked into a
    window. The window's START is what the kitchen works back from: an
    18:00-20:00 order is treated as an 18:00 delivery, and the Finish-By time is
    that minus the company's "finish before delivery" setting.

    Slots are records, not code, so they can be added, renamed, reordered or
    retired from Kitchen -> Configuration -> Delivery Slots.
    """
    _name = "sukkar.delivery.slot"
    _description = "Delivery Time Slot"
    _order = "sequence, start_hour, id"

    name = fields.Char(compute="_compute_name", store=True, string="Slot")
    start_hour = fields.Float(
        string="From", required=True, default=10.0,
        help="Start of the window, in the company's operating timezone. "
             "This is the time the delivery is scheduled against.")
    end_hour = fields.Float(string="To", required=True, default=12.0)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    order_count = fields.Integer(compute="_compute_order_count", string="Orders")

    _sql_constraints = [
        ("sukkar_slot_unique", "unique(start_hour, end_hour)",
         "That delivery slot already exists."),
    ]

    # ------------------------------------------------------------------ display
    @staticmethod
    def _format_hour(value):
        hours = int(value)
        minutes = int(round((value - hours) * 60))
        if minutes == 60:            # 9.999 -> 10:00, never 09:60
            hours, minutes = hours + 1, 0
        return f"{hours:02d}:{minutes:02d}"

    @api.depends("start_hour", "end_hour")
    def _compute_name(self):
        for rec in self:
            rec.name = f"{self._format_hour(rec.start_hour)}-{self._format_hour(rec.end_hour)}"

    # No @api.depends on purpose: this counts sale.order records pointing back at
    # the slot, and there is no inverse one2many to depend on. Without depends the
    # field is simply recomputed on every read, which is the behaviour we want for
    # a live count — adding a one2many just to satisfy the decorator would put a
    # field on the model that nothing else uses.
    def _compute_order_count(self):
        counts = dict(self.env["sale.order"]._read_group(
            [("sukkar_delivery_slot_id", "in", self.ids)],
            groupby=["sukkar_delivery_slot_id"], aggregates=["__count"]))
        for rec in self:
            rec.order_count = counts.get(rec, 0)

    @api.constrains("start_hour", "end_hour")
    def _check_hours(self):
        for rec in self:
            for value in (rec.start_hour, rec.end_hour):
                if not 0 <= value < 24:
                    raise ValidationError(_("Slot times must be between 00:00 and 23:59."))
            if rec.end_hour <= rec.start_hour:
                raise ValidationError(_(
                    "The end of a delivery slot must come after its start (%s).", rec.name))

    # ------------------------------------------------------------------ helpers
    def _tz(self):
        """The zone slot times are expressed in — a company setting, never the
        editing user's own timezone, so a delivery never shifts by who saved it."""
        return pytz.timezone(self.env.company.sukkar_tz or "Asia/Kuwait")

    def to_utc(self, date):
        """Combine `date` with this slot's start time and return naive UTC,
        which is how Odoo stores datetimes."""
        self.ensure_one()
        hours = int(self.start_hour)
        minutes = int(round((self.start_hour - hours) * 60))
        naive_local = datetime.combine(date, dt_time(hour=hours, minute=minutes))
        return self._tz().localize(naive_local).astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _tz_midnight_utc(self, date):
        """Start of `date` in the operating timezone, as naive UTC. Used when an
        order has a delivery date but no slot chosen yet."""
        naive_local = datetime.combine(date, dt_time.min)
        return self._tz().localize(naive_local).astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _find_for_time(self, local_dt):
        """The slot whose start is closest to `local_dt` — used to convert
        legacy free-text delivery times onto real slots."""
        target = local_dt.hour + local_dt.minute / 60.0
        slots = self.search([])
        if not slots:
            return self.browse()
        return min(slots, key=lambda s: abs(s.start_hour - target))
