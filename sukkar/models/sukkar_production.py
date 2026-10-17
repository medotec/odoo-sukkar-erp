from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .sukkar_production_stage import STATUS_SELECTION

# Fallback only. The real value is the company setting
# `sukkar_finish_before_min` (Settings -> Sukkar), so the shop can change it
# without touching code.
FINISH_BEFORE_DELIVERY_MIN = 30


class SukkarProduction(models.Model):
    """A kitchen production ticket for one order — the card on the kitchen board."""
    _name = "sukkar.production"
    _description = "Kitchen Production Ticket"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "delivery_datetime asc, priority desc, id"

    name = fields.Char(default="New", copy=False, readonly=True, index=True)
    sale_order_id = fields.Many2one(
        "sale.order", string="Order", ondelete="cascade", tracking=True, index=True)
    partner_id = fields.Many2one(
        related="sale_order_id.partner_id", string="Customer", store=True)
    delivery_datetime = fields.Datetime(
        related="sale_order_id.sukkar_delivery_datetime",
        string="Delivery", store=True)
    # Writable, so the Orders calendar can still be dragged to another day.
    # (delivery_datetime is derived from date + slot and therefore read-only.)
    delivery_date = fields.Date(
        related="sale_order_id.sukkar_delivery_date",
        string="Delivery Date", store=True, readonly=False)
    delivery_time_range = fields.Char(
        related="sale_order_id.sukkar_delivery_time_range", string="Time Range")
    special_requests = fields.Text(
        related="sale_order_id.sukkar_special_requests", string="Special Requests")
    # The till's internal notes — the Note tile and Actions → General Note —
    # which the kitchen has to see (owner, 2 Oct 2026).
    order_note = fields.Text(
        related="sale_order_id.sukkar_order_note", string="Internal Note")
    # The pictures that go with the notes: one per item from the till's
    # Internal Note box, and the office's own on the order (owner, 10 Oct 2026).
    has_note_image = fields.Boolean(compute="_compute_has_note_image")
    note_pictures_html = fields.Html(
        string="Note Pictures", compute="_compute_note_pictures_html",
        sanitize=False)

    # === September 2026 batch ===============================================
    # What the kitchen wall screen must show. Declared here as plain related
    # fields so the screen builder only has to lay them out.
    order_type = fields.Selection(
        related="sale_order_id.sukkar_order_type", string="Delivery or Pickup")
    order_state = fields.Selection(
        related="sale_order_id.state", string="Order Status", store=True,
        help="A cancelled order is not made: its ticket drops off the kitchen "
             "and dispatch boards.")
    address_summary = fields.Char(
        related="sale_order_id.sukkar_address_summary", string="Address")
    flavour_summary = fields.Text(
        related="sale_order_id.sukkar_flavour_summary", string="Cake Flavours")
    cod_overdue = fields.Boolean(
        related="sale_order_id.sukkar_cod_overdue", string="COD Overdue")
    cod_days_outstanding = fields.Integer(
        related="sale_order_id.sukkar_cod_days_outstanding", string="Days Unpaid")
    line_summary = fields.Text(string="Items", compute="_compute_line_summary")

    stage_id = fields.Many2one(
        "sukkar.production.stage", string="Stage",
        group_expand="_read_group_stage_ids", tracking=True,
        default=lambda self: self.env["sukkar.production.stage"].search([], limit=1).id)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, required=True)
    user_id = fields.Many2one("res.users", string="Assigned To", tracking=True)

    # --- Delivery (set by the driver) ---------------------------------------
    driver_id = fields.Many2one("res.users", string="Driver", tracking=True,
        group_expand="_read_group_driver_ids",
        help="The driver who delivers this order. The supervisor must assign "
             "one before the order can be picked up.")
    delivered_at = fields.Datetime(string="Delivered At", readonly=True, copy=False)
    delivered_by = fields.Many2one("res.users", string="Delivered By", readonly=True, copy=False)
    delivery_note = fields.Char(string="Delivery Note",
        help="e.g. received by, left at reception.")

    # --- Order facts surfaced on the Orders screen --------------------------
    currency_id = fields.Many2one(related="sale_order_id.currency_id")
    amount_total = fields.Monetary(
        related="sale_order_id.amount_total", string="Order Total",
        currency_field="currency_id")
    payment_status = fields.Char(
        related="sale_order_id.sukkar_payment_status", string="Payment")
    payment_method = fields.Selection(
        related="sale_order_id.sukkar_payment_method", string="Pay Method")
    knet_approval = fields.Char(
        related="sale_order_id.sukkar_knet_approval", string="KNET Approval")
    payment_reference = fields.Char(
        related="sale_order_id.sukkar_payment_reference", string="Payment Link Ref.")
    clear_to_deliver = fields.Boolean(
        related="sale_order_id.sukkar_clear_to_deliver", string="Clear to Deliver")
    priority = fields.Selection(
        [("0", "Normal"), ("1", "Urgent")], default="0", string="Priority")
    color = fields.Integer(string="Color")
    kanban_state = fields.Selection(
        [("normal", "In Progress"), ("done", "Ready"), ("blocked", "Blocked")],
        default="normal", string="Kanban State")

    # --- Dispatch (the Cashier/supervisor screen) ---------------------------
    # Orders in the same delivery window group together on the Dispatch kanban;
    # dragging between columns moves the order to another window (written
    # through to the sale order), dragging within a column reorders the run.
    delivery_slot_id = fields.Many2one(
        related="sale_order_id.sukkar_delivery_slot_id", string="Delivery Window",
        store=True, readonly=False, group_expand="_read_group_slot_ids")
    receiver_name = fields.Char(
        related="sale_order_id.sukkar_receiver_name", string="Receiver")
    receiver_phone = fields.Char(
        related="sale_order_id.sukkar_receiver_phone", string="Receiver Phone")
    gift_message = fields.Char(
        related="sale_order_id.sukkar_gift_message", string="Gift Message")
    ship_state_id = fields.Many2one(
        related="sale_order_id.partner_shipping_id.state_id", string="Governorate")
    # What the Drivers screen shows on the card (owner, 10 Oct 2026): where it
    # goes at a glance, and a date-and-time column to group the day by.
    addr_area = fields.Char(related="sale_order_id.sukkar_addr_area", string="Area")
    addr_block = fields.Char(related="sale_order_id.sukkar_addr_block", string="Block")
    delivery_when = fields.Char(
        string="Delivery Date & Time", compute="_compute_delivery_when", store=True,
        help="Day and window together, e.g. 2026-10-11 · 11:00-13:00 — sorts "
             "in time order. Read-only, so cards cannot be dragged between "
             "these columns: rescheduling is done on Dispatch.")
    cash_due = fields.Monetary(
        related="sale_order_id.sukkar_cash_due", string="Collect (Cash)",
        currency_field="currency_id")
    sequence = fields.Integer(
        string="Run Priority", compute="_compute_sequence", store=True,
        readonly=False, copy=False,
        help="Order of the driver's run: defaults to the start of the delivery "
             "window (earlier window = delivered first); drag cards on the "
             "Dispatch screen to override. The driver receipt prints in this "
             "order. Changing the delivery window resets it to the window's own "
             "priority.")

    @api.depends("delivery_slot_id.start_hour")
    def _compute_sequence(self):
        # Minutes-since-midnight of the window start, so 09:00-11:00 (540) always
        # outranks 18:00-20:00 (1080) until a supervisor drags things around.
        # No window sorts last (1440) but stays visible — hidden work is worse
        # than unsorted work.
        for rec in self:
            slot = rec.delivery_slot_id
            rec.sequence = int(round(slot.start_hour * 60)) if slot else 1440

    @api.model
    def _read_group_slot_ids(self, slots, domain, *args):
        # Dispatch board: every active window is a column even when empty, so
        # "nothing booked for 09:00-11:00" is visible rather than missing.
        return self.env["sukkar.delivery.slot"].search([])

    deadline = fields.Datetime(
        string="Finish By", compute="_compute_deadline", store=True,
        help="Production must be finished by this time (delivery − 30 min).")
    is_done = fields.Boolean(related="stage_id.is_done", store=True, string="Finished")
    is_late = fields.Boolean(string="Late", compute="_compute_is_late", search="_search_is_late")

    mrp_production_ids = fields.Many2many(
        "mrp.production", string="Manufacturing Orders", copy=False)
    mrp_production_count = fields.Integer(compute="_compute_mrp_count")

    @api.depends("mrp_production_ids")
    def _compute_mrp_count(self):
        for rec in self:
            rec.mrp_production_count = len(rec.mrp_production_ids)

    # --- Fulfillment status (auto-follows the stage) + time tracking --------
    status = fields.Selection(
        STATUS_SELECTION, related="stage_id.status", store=True, string="Status", tracking=True)
    stage_entered = fields.Datetime(default=fields.Datetime.now, copy=False)
    stage_log_ids = fields.One2many(
        "sukkar.production.stage.log", "production_id", string="Stage Times")
    total_minutes = fields.Float(compute="_compute_timing", string="Total Time (min)")
    current_minutes = fields.Float(compute="_compute_timing", string="In Current Step (min)")
    overrun_count = fields.Integer(compute="_compute_timing", string="Overrun Steps")

    @api.depends("stage_log_ids.entered_at", "stage_log_ids.exited_at",
                 "stage_log_ids.planned_min")
    def _compute_timing(self):
        now = fields.Datetime.now()
        for rec in self:
            total = cur = 0.0
            over = 0
            for log in rec.stage_log_ids:
                end = log.exited_at or now
                d = ((end - log.entered_at).total_seconds() / 60.0) if log.entered_at else 0.0
                total += d
                if log.planned_min and log.exited_at and d > log.planned_min:
                    over += 1
                if not log.exited_at:
                    cur = d
            rec.total_minutes = round(total, 1)
            rec.current_minutes = round(cur, 1)
            rec.overrun_count = over

    def _open_stage_log(self):
        self.ensure_one()
        if self.stage_id:
            self.env["sukkar.production.stage.log"].create({
                "production_id": self.id, "stage_id": self.stage_id.id,
                "entered_at": fields.Datetime.now(),
                "planned_min": self.stage_id.default_duration})

    def _close_open_stage_log(self):
        now = fields.Datetime.now()
        for log in self.stage_log_ids.filtered(lambda l: not l.exited_at):
            log.exited_at = now

    # --- Fulfillment shortcuts (buttons) ------------------------------------
    def _move_to_status(self, status):
        stage = self.env["sukkar.production.stage"].search(
            [("status", "=", status)], order="sequence", limit=1)
        if not stage:
            raise UserError(_(
                "No production stage is configured for status '%s'. "
                "Please add or restore a stage with that fulfillment status.", status))
        self.write({"stage_id": stage.id})

    def action_picked_up(self):
        self._move_to_status("in_delivery")

    def action_delivered(self):
        """Set by the driver when the order is handed over — stamps who & when."""
        now = fields.Datetime.now()
        for rec in self:
            vals = {"delivered_at": now, "delivered_by": self.env.uid}
            if not rec.driver_id:
                vals["driver_id"] = self.env.uid
            rec.write(vals)
        self._move_to_status("delivered")

    def action_close(self):
        self._move_to_status("closed")

    # --- The kitchen wall screen --------------------------------------------
    # One big button, and nothing that is not needed. Everything the kitchen does
    # on the board is "this step is finished, move it on" — so that is the only
    # action offered by default. Flagging a problem is the one exception, because
    # a ticket that cannot proceed has to be visible without anyone hunting for it.
    countdown = fields.Char(
        compute="_compute_countdown", string="Time Left",
        help="How long until this must be finished. A countdown rather than a "
             "clock time: '38 min' is actionable, '18:30' is arithmetic you should "
             "not have to do with your hands covered in cream.")
    urgency = fields.Selection(
        [("late", "Late"), ("soon", "Due soon"), ("ok", "On time"), ("none", "No deadline")],
        compute="_compute_countdown", string="Urgency")
    next_stage_id = fields.Many2one(
        "sukkar.production.stage", compute="_compute_next_stage", string="Next Step")
    done_label = fields.Char(compute="_compute_next_stage", string="Button")

    @api.depends("deadline", "is_done")
    def _compute_countdown(self):
        now = fields.Datetime.now()
        for rec in self:
            if not rec.deadline or rec.is_done:
                rec.countdown, rec.urgency = "", "none"
                continue
            minutes = (rec.deadline - now).total_seconds() / 60.0
            if minutes < 0:
                over = abs(minutes)
                rec.countdown = ("%d min late" % over if over < 60
                                 else "%dh %02dm late" % (over // 60, over % 60))
                rec.urgency = "late"
            else:
                rec.countdown = ("%d min" % minutes if minutes < 60
                                 else "%dh %02dm" % (minutes // 60, minutes % 60))
                rec.urgency = "soon" if minutes <= 30 else "ok"

    @api.depends("stage_id")
    def _compute_next_stage(self):
        Stage = self.env["sukkar.production.stage"]
        for rec in self:
            nxt = Stage.search([
                ("kitchen_stage", "=", True),
                ("sequence", ">", rec.stage_id.sequence or 0),
            ], order="sequence", limit=1)
            rec.next_stage_id = nxt
            rec.done_label = nxt.name if nxt else _("Ready")

    def action_sukkar_done(self):
        """The big button: this step is finished, move to the next one.

        Goes through the normal write(), so the payment gate, the stage log and
        the stock consumption all behave exactly as they do everywhere else — the
        wall screen is a faster way to do the same thing, never a way round it.
        """
        for rec in self:
            if not rec.next_stage_id:
                raise UserError(_(
                    "%s is already at the last kitchen step.", rec.name))
            rec.stage_id = rec.next_stage_id
        return True

    def action_sukkar_problem(self):
        """Flag a ticket that cannot proceed, so it is visible to everyone."""
        for rec in self:
            rec.kanban_state = "normal" if rec.kanban_state == "blocked" else "blocked"
        return True

    # --- Payment confirmation (straight from the Orders screen) -------------
    def action_mark_paid(self):
        """Open the payment prompt: pick KNET / Cash, and capture the KNET approval."""
        return {
            "type": "ir.actions.act_window",
            "name": _("Confirm Payment"),
            "res_model": "sukkar.mark.paid.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_production_ids": [(6, 0, self.ids)]},
        }

    def action_mark_unpaid(self):
        """Undo a manual payment confirmation."""
        orders = self.mapped("sale_order_id")
        if orders:
            orders.sudo().write({"sukkar_paid": False})

    # ------------------------------------------------------------------ computes
    @api.depends("delivery_datetime", "company_id.sukkar_finish_before_min")
    def _compute_deadline(self):
        for rec in self:
            minutes = (rec.company_id.sukkar_finish_before_min
                       or FINISH_BEFORE_DELIVERY_MIN)
            rec.deadline = (rec.delivery_datetime - timedelta(minutes=minutes)
                            ) if rec.delivery_datetime else False

    @api.depends("deadline", "is_done")
    def _compute_is_late(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.is_late = bool(rec.deadline and not rec.is_done and rec.deadline < now)

    def _search_is_late(self, operator, value):
        # Allow filtering "Late" tickets from the search view.
        now = fields.Datetime.now()
        late_ids = self.search([("is_done", "=", False), ("deadline", "<", now)]).ids
        positive = (operator == "=" and value) or (operator == "!=" and not value)
        return [("id", "in" if positive else "not in", late_ids)]

    # What goes INTO the order — every line's recipe components, aggregated, so
    # the wall screen can show the kitchen what to reach for without opening
    # anything. One level deep on purpose: "2 × Cupcake (Rose)" is the useful
    # answer at the counter; exploding to flour and sugar is noise here.
    components_summary = fields.Text(
        string="Ingredients", compute="_compute_components_summary")

    @api.depends("sale_order_id.order_line.product_id",
                 "sale_order_id.order_line.product_uom_qty")
    def _compute_components_summary(self):
        BoM = self.env["mrp.bom"]
        for rec in self:
            needs = {}
            lines = rec.sale_order_id.order_line.filtered(
                lambda l: not l.display_type and not l.is_delivery and l.product_id)
            for line in lines:
                bom = BoM._bom_find(line.product_id).get(line.product_id)
                if not bom:
                    continue
                factor = line.product_uom_qty / (bom.product_qty or 1.0)
                for bom_line in bom.bom_line_ids:
                    name = bom_line.product_id.display_name
                    needs[name] = needs.get(name, 0.0) + bom_line.product_qty * factor
            rec.components_summary = "\n".join(
                f"{qty:g} × {name}" for name, qty in needs.items())

    # The employee's confirmation view: tapping a wall-screen tile opens the WHOLE
    # order — a PHOTO per line (the size's own arrangement) with its flavours,
    # accessories (cards/toppers) split out, the card text, the requests that
    # change what is made, and where it goes: address, driver, payment. Rendered
    # server-side as HTML so the wall screen needs no extra JS to show it.
    # Ingredients are deliberately NOT here (owner, 19 Sep 2026): the kitchen
    # knows its own recipes, and the scrolling box they lived in swallowed taps.
    order_preview_html = fields.Html(
        string="Order Preview", compute="_compute_order_preview_html",
        sanitize=False)

    @staticmethod
    def _sukkar_is_accessory(product):
        names = " ".join(product.pos_categ_ids.mapped("name")) + " " + \
            (product.categ_id.name or "")
        return ("Topper" in names or "Card" in names or "كرت" in names
                or "كروت" in names)

    @staticmethod
    def _sukkar_is_card(product):
        names = " ".join(product.pos_categ_ids.mapped("name")) + " " + \
            (product.categ_id.name or "")
        return "Card" in names or "كرت" in names or "كروت" in names

    def _sukkar_selection_label(self, field_name):
        """The bilingual label a selection field gives the value it holds."""
        self.ensure_one()
        value = self[field_name]
        if not value:
            return ""
        return dict(
            self._fields[field_name]._description_selection(self.env)
        ).get(value, value)

    @api.depends("sale_order_id.order_line.product_id",
                 "sale_order_id.order_line.product_uom_qty",
                 "sale_order_id.order_line.sukkar_flavour_summary",
                 "sale_order_id.sukkar_order_type",
                 "sale_order_id.sukkar_delivery_date",
                 "sale_order_id.sukkar_delivery_time_range",
                 "sale_order_id.sukkar_flavour_summary",
                 "sale_order_id.sukkar_gift_message",
                 "sale_order_id.sukkar_special_requests",
                 "sale_order_id.sukkar_order_note",
                 "sale_order_id.sukkar_note_image",
                 "sale_order_id.order_line.sukkar_note_image",
                 "sale_order_id.sukkar_address_summary",
                 "sale_order_id.sukkar_receiver_name",
                 "sale_order_id.sukkar_receiver_phone",
                 "sale_order_id.sukkar_payment_method",
                 "sale_order_id.sukkar_payment_status",
                 "sale_order_id.sukkar_driver_id",
                 "driver_id")
    def _compute_order_preview_html(self):
        from markupsafe import Markup, escape

        def line_block(line):
            product = line.product_id
            block = Markup(
                '<div style="display:inline-block; width:190px; margin:6px; '
                'text-align:center; vertical-align:top;">'
                '<img src="/web/image/product.product/%s/image_256?unique=%s" '
                'style="max-width:180px; max-height:180px; border-radius:12px; '
                'object-fit:contain;"/>'
                '<div style="font-weight:700;">%s × %s</div>'
            ) % (product.id, escape(str(product.write_date or "")),
                 int(line.product_uom_qty), escape(product.display_name))
            if line.sukkar_flavour_summary:
                block += Markup(
                    '<div style="font-size:15px; font-weight:800; color:#B0175F;">'
                    '🍰 %s</div>') % line.sukkar_flavour_summary
            return block + Markup("</div>")

        def band(label, text, colour, background, size):
            return Markup(
                '<div style="border:3px solid %s; background:%s; border-radius:10px; '
                'padding:8px 12px; margin:8px 0;">'
                '<div style="font-size:12px; font-weight:800; letter-spacing:.08em; '
                'color:%s;">%s</div>'
                '<div style="font-size:%spx; font-weight:800; color:#111; '
                'white-space:pre-wrap;">%s</div></div>'
            ) % (colour, background, colour, label, size, text)

        def fact(label, value):
            return Markup(
                '<div style="margin:3px 0;">'
                '<span style="display:inline-block; min-width:170px; font-size:12px; '
                'font-weight:800; letter-spacing:.04em; color:#666;">%s</span>'
                '<span style="font-size:16px; font-weight:700;">%s</span></div>'
            ) % (label, value)

        for rec in self:
            order = rec.sale_order_id
            if not order:
                rec.order_preview_html = False
                continue
            lines = order.order_line.filtered(
                lambda l: not l.display_type and not l.is_delivery
                and l.product_id and l.product_uom_qty > 0)
            main = lines.filtered(lambda l: not self._sukkar_is_accessory(l.product_id))
            accessories = lines - main
            cards = accessories.filtered(lambda l: self._sukkar_is_card(l.product_id))
            pickup = rec.order_type == "pickup"

            html = Markup('<div style="font-size:16px;">')

            head = [order.name or ""]
            if rec.order_type:
                head.append(rec._sukkar_selection_label("order_type"))
            if rec.delivery_date:
                head.append(str(rec.delivery_date))
            if rec.delivery_time_range:
                head.append(rec.delivery_time_range)
            html += Markup(
                '<div style="font-size:21px; font-weight:800; margin-bottom:6px;">'
                '%s</div>') % Markup(" · ").join(escape(part) for part in head if part)

            if rec.flavour_summary:
                html += band("Cake Flavours", rec.flavour_summary,
                             "#D82B7D", "#FFF3F8", 21)
            # Every note on the kitchen screen is in red (owner, 10 Oct 2026).
            if rec.special_requests:
                html += band("⚠ Special Requests", Markup(
                    '<span style="color:#C0392B;">%s</span>') % rec.special_requests,
                             "#C0392B", "#FFFFFF", 20)

            # The till's internal notes and their pictures.
            if order.sukkar_order_note or rec.note_pictures_html:
                html += Markup(
                    '<div style="border:3px solid #C0392B; background:#FFFFFF; '
                    'border-radius:10px; padding:8px 12px; margin:8px 0;">'
                    '<div style="font-size:12px; font-weight:800; letter-spacing:.08em; '
                    'color:#C0392B;">📝 Note</div>')
                if order.sukkar_order_note:
                    html += Markup(
                        '<div style="font-size:18px; font-weight:800; color:#C0392B; '
                        'white-space:pre-wrap;">%s</div>') % order.sukkar_order_note
                if rec.note_pictures_html:
                    html += rec.note_pictures_html
                html += Markup("</div>")

            if cards or order.sukkar_gift_message:
                card_type = ", ".join(cards.mapped("product_id.display_name"))
                html += Markup(
                    '<div style="border:2px dashed #D82B7D; border-radius:10px; '
                    'padding:8px 12px; margin:8px 0;">'
                    '<div style="font-size:12px; font-weight:800; letter-spacing:.08em; '
                    'color:#D82B7D;">Card%s</div>'
                    '<div style="font-size:18px; font-weight:700; '
                    'white-space:pre-wrap;">🎁 %s</div></div>'
                ) % (" — %s" % card_type if card_type else "",
                     order.sukkar_gift_message or "")

            html += Markup("").join(line_block(line) for line in main)
            if accessories:
                html += Markup(
                    '<div style="font-weight:800; border-top:1px dashed #ccc; '
                    'margin-top:6px; padding-top:4px;">Accessories</div>')
                html += Markup("").join(line_block(line) for line in accessories)

            facts = Markup("")
            if rec.receiver_name:
                phone = f" ({rec.receiver_phone})" if rec.receiver_phone else ""
                facts += fact("Receiver", f"{rec.receiver_name}{phone}")
            if rec.address_summary:
                facts += fact("Address", rec.address_summary)
            driver = rec.driver_id or order.sukkar_driver_id
            if driver:
                facts += fact("Driver", driver.name)
            if rec.payment_method:
                facts += fact("Payment",
                              rec._sukkar_selection_label("payment_method"))
            paid = rec.payment_status == "PAID"
            facts += fact("Payment Status", Markup(
                '<span style="display:inline-block; padding:2px 12px; '
                'border-radius:6px; font-weight:800; background:%s; color:#fff;">'
                '%s</span>'
            ) % ("#1E7A3C" if paid else "#C0392B",
                 "PAID" if paid else "UNPAID"))
            html += Markup(
                '<div style="border-top:2px solid #111; margin-top:10px; '
                'padding-top:6px;">'
                '<div style="font-size:12px; font-weight:800; letter-spacing:.08em; '
                'color:#666;">%s</div>%s</div>'
            ) % ("Pickup" if pickup else "Delivery", facts)

            rec.order_preview_html = html + Markup("</div>")

    def _sukkar_note_pictures(self, bin_size=False):
        """(record, caption) for every note picture on the order: the office's
        own on the order, then one per item from the till's Internal Note box."""
        self.ensure_one()
        order = self.sudo().sale_order_id.with_context(bin_size=bin_size)
        pictures = []
        if order.sukkar_note_image:
            pictures.append((order, ""))
        for line in order.order_line:
            if line.sukkar_note_image:
                pictures.append((line, line.product_id.display_name))
        return pictures

    @api.depends("sale_order_id.sukkar_note_image",
                 "sale_order_id.order_line.sukkar_note_image")
    def _compute_has_note_image(self):
        # The card only says a picture is waiting inside: size only, no bytes.
        for rec in self:
            rec.has_note_image = bool(
                rec.sale_order_id and rec._sukkar_note_pictures(bin_size=True))

    @api.depends("sale_order_id.sukkar_note_image",
                 "sale_order_id.order_line.sukkar_note_image")
    def _compute_note_pictures_html(self):
        # Inlined rather than linked: a kitchen login may not be allowed to
        # open sales orders, and the picture must show for it all the same.
        from markupsafe import Markup, escape
        kinds = {"/9j/": "jpeg", "iVBO": "png", "R0lG": "gif", "UklG": "webp"}
        for rec in self:
            html = Markup("")
            for record, caption in (
                    rec._sukkar_note_pictures() if rec.sale_order_id else []):
                data = record.sukkar_note_image
                data = data.decode() if isinstance(data, bytes) else data
                kind = kinds.get(data[:4], "jpeg")
                html += Markup(
                    '<div style="display:inline-block; vertical-align:top; '
                    'margin:6px 10px 0 0; text-align:center;">'
                    '<img src="data:image/%s;base64,%s" '
                    'style="display:block; max-width:360px; max-height:360px; '
                    'border-radius:8px; object-fit:contain;"/>'
                    '<div style="font-size:13px; font-weight:700; color:#C0392B;">%s</div>'
                    '</div>'
                ) % (kind, data, escape(caption))
            rec.note_pictures_html = html or False

    @api.depends("sale_order_id.order_line.product_id",
                 "sale_order_id.order_line.product_uom_qty",
                 "sale_order_id.order_line.display_type")
    def _compute_line_summary(self):
        for rec in self:
            # Delivery-fee lines are money, not something the kitchen makes.
            lines = rec.sale_order_id.order_line.filtered(
                lambda l: not l.display_type and not l.is_delivery)
            rec.line_summary = "\n".join(
                f"{int(l.product_uom_qty)}× {l.product_id.display_name}" for l in lines) or ""

    @api.depends("delivery_date", "delivery_slot_id", "delivery_slot_id.name")
    def _compute_delivery_when(self):
        for rec in self:
            parts = [str(rec.delivery_date) if rec.delivery_date else "",
                     rec.delivery_slot_id.name or ""]
            rec.delivery_when = " · ".join(p for p in parts if p) or False

    @api.model
    def _web_read_group(self, domain, fields, groupby, limit=None, offset=0,
                        orderby=False, lazy=True):
        """The Drivers screen shows every column, empty or not (owner, 11 Oct
        2026): an Unassigned column even with nothing unassigned, and every
        delivery slot of the day(s) on show even with nothing booked."""
        groups = super()._web_read_group(
            domain, fields, groupby, limit=limit, offset=offset,
            orderby=orderby, lazy=lazy)
        if not self.env.context.get("sukkar_drivers_board") or not groupby or offset:
            return groups
        field = groupby[0] if isinstance(groupby, (list, tuple)) else groupby
        if field == "driver_id":
            unassigned = [g for g in groups if not g.get("driver_id")]
            groups = [g for g in groups if g.get("driver_id")]
            # Unassigned always comes first: it is where orders start from.
            groups.insert(0, unassigned[0] if unassigned else self._sukkar_empty_group(
                groups, field, False, domain))
        elif field == "delivery_when":
            present = {g.get("delivery_when") for g in groups}
            for value in self._sukkar_day_slots(domain):
                if value not in present:
                    groups.append(self._sukkar_empty_group(
                        groups, field, value, domain))
            groups.sort(key=lambda g: g.get("delivery_when") or "~")
        return groups

    @api.model
    def _sukkar_empty_group(self, groups, field, value, domain):
        # All the board reads from a column: its value, its count and its domain.
        return {
            field: value,
            f"{field}_count": 0,
            "__count": 0,
            "__domain": [(field, "=", value)] + list(domain or []),
        }

    @api.model
    def _sukkar_day_slots(self, domain):
        """Every active slot of every day the filter shows: the days named in
        the domain (Today, Tomorrow), or today when none is."""
        days = set()
        for leaf in domain or []:
            if (isinstance(leaf, (list, tuple)) and len(leaf) == 3
                    and leaf[0] == "delivery_date" and leaf[1] == "="
                    and leaf[2]):
                days.add(str(leaf[2]))
        if not days:
            days.add(str(fields.Date.context_today(self)))
        slots = self.env["sukkar.delivery.slot"].search([])
        return [f"{day} · {slot.name}" for day in sorted(days) for slot in slots]

    @api.model
    def _read_group_driver_ids(self, drivers, domain, *args):
        """Every driver gets a column on the Drivers screen, even with nothing
        assigned yet — a free driver is somewhere to drop an order."""
        return self.env["res.users"].search(
            [("sukkar_is_driver", "=", True)], order="name")

    @api.model
    def _read_group_stage_ids(self, stages, domain, *args):
        # Show the right columns per board (kitchen vs delivery), even when empty.
        board = self.env.context.get("board")
        dom = []
        if board == "kitchen":
            dom = [("kitchen_stage", "=", True)]
        elif board == "delivery":
            dom = [("delivery_stage", "=", True)]
        return self.env["sukkar.production.stage"].search(dom)

    # ------------------------------------------------------------------ write
    def write(self, vals):
        stage_change = bool(vals.get("stage_id"))
        moving_to_done = False
        if stage_change:
            stage = self.env["sukkar.production.stage"].browse(vals["stage_id"])
            # Leaving with a driver requires a driver: the run sheet and the
            # delivery receipt are per driver, so an unassigned order must not
            # leave the shop. A branch pickup never leaves it — the customer
            # comes and collects — so it is not asked for one.
            if stage.status == "in_delivery" and not vals.get("driver_id"):
                for rec in self.filtered(lambda r: r.order_type != "pickup"):
                    if not rec.driver_id:
                        raise UserError(_(
                            "%s has no driver assigned. Assign a driver (Dispatch "
                            "screen or the Orders list) before marking it picked "
                            "up.", rec.name))
            # Section 6: unpaid Bank-Link orders must NOT reach Ready / be delivered.
            if stage.is_done:
                moving_to_done = True
                for rec in self:
                    order = rec.sale_order_id
                    if order and not order.sukkar_clear_to_deliver:
                        raise UserError(_(
                            "Order %s is an unpaid link-payment order — it cannot be "
                            "marked Ready until payment is received.", order.name))
            # stamp the time we spent in the stage we're leaving
            self._close_open_stage_log()
        res = super().write(vals)
        if stage_change:
            now = fields.Datetime.now()
            for rec in self:
                rec.stage_entered = now
                rec._open_stage_log()
        if "driver_id" in vals:
            self._sukkar_sync_order_driver()
        if moving_to_done:
            # Reduce stock only after production: build the items now (once).
            done = self.filtered(lambda r: r.is_done)
            done._sukkar_produce()
            # The cake pieces the order held since it was confirmed are used up
            # now that it has actually been made. Safe to run more than once:
            # it eats the order's own reservation first, so nothing is counted
            # twice however many times a ticket is re-saved.
            Batch = self.env["sukkar.cake.batch"]
            for ticket in done.filtered("sale_order_id"):
                Batch._sukkar_consume_for_order(ticket.sale_order_id, ticket)
        return res

    def _sukkar_sync_order_driver(self):
        """Dispatch assigns the driver on the ticket; the order has to agree,
        because the invoice prints the order's driver and the cash hand-in
        lists a driver's orders. Taking the driver off (dragging a card back to
        Unassigned on the Drivers screen) takes him off the order too."""
        for rec in self.filtered("sale_order_id"):
            order = rec.sale_order_id
            if order.sukkar_driver_id != rec.driver_id:
                order.sukkar_driver_id = rec.driver_id

    def _sukkar_produce(self):
        """For each finished ticket, run a Manufacturing Order per BoM line product,
        consuming ingredients/packaging from stock and producing the finished good."""
        # sudo so a kitchen user without MRP rights can still trigger production
        Production = self.env["mrp.production"].sudo()
        BoM = self.env["mrp.bom"].sudo()
        for ticket in self:
            order = ticket.sale_order_id
            if not order or ticket.mrp_production_ids:
                continue
            made = Production
            for line in order.order_line:
                if line.display_type or not line.product_id or line.product_uom_qty <= 0:
                    continue
                bom = BoM._bom_find(line.product_id).get(line.product_id)
                if not bom:
                    continue
                mo = Production.create({
                    "product_id": line.product_id.id,
                    "product_qty": line.product_uom_qty,
                    "bom_id": bom.id,
                    "origin": ticket.name,
                })
                mo.action_confirm()
                mo.action_assign()            # reserve ingredients from stock
                mo.qty_producing = mo.product_qty
                mo._set_qty_producing()        # push the consumed quantities onto moves
                result = mo.button_mark_done()
                # Odoo may pop a consumption/backorder wizard — push it through.
                if isinstance(result, dict) and result.get("res_model"):
                    wiz = self.env[result["res_model"]].with_context(
                        result.get("context") or {}).create({})
                    if hasattr(wiz, "action_confirm"):
                        wiz.action_confirm()
                    elif hasattr(wiz, "action_backorder"):
                        wiz.action_backorder()
                made |= mo
            if made:
                ticket.mrp_production_ids = [(6, 0, made.ids)]

    def action_view_mrp(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Manufacturing Orders",
            "res_model": "mrp.production",
            "view_mode": "list,form",
            "domain": [("id", "in", self.mrp_production_ids.ids)],
        }

    # ------------------------------------------------------------------ create
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "sukkar.production") or "New"
            if not vals.get("stage_id"):
                first = self.env["sukkar.production.stage"].search([], limit=1)
                vals["stage_id"] = first.id
            vals.setdefault("stage_entered", fields.Datetime.now())
        records = super().create(vals_list)
        for rec in records:
            rec._open_stage_log()
        return records
