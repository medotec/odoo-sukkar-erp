from datetime import datetime, time as dt_time, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class SaleOrder(models.Model):
    """Gifting & delivery details for Sukkar orders.

    Sender  = the ordering customer (``partner_id``).
    Receiver= the person the gift is delivered to (``partner_shipping_id``),
              with an explicit phone for quick driver contact.
    """
    _inherit = "sale.order"

    # --- Delivery -----------------------------------------------------------
    # Deliveries are booked as a DATE plus a SLOT (a time window) — the shop never
    # promises an exact minute. The datetime below is DERIVED from the start of the
    # slot and kept because the kitchen board ordering, the Finish-By deadline, the
    # calendars, the reminder cron and every report already work off it.
    sukkar_delivery_date = fields.Date(string="Delivery Date")
    sukkar_delivery_slot_id = fields.Many2one(
        "sukkar.delivery.slot", string="Delivery Slot", ondelete="restrict",
        help="Time window promised to the customer. Its START is the delivery time "
             "the kitchen works back from.")

    sukkar_delivery_datetime = fields.Datetime(
        string="Delivery Date/Time",
        compute="_compute_sukkar_delivery_datetime", store=True, readonly=True,
        help="Derived: the delivery date at the start of the chosen slot. "
             "Set the date and the slot, not this.",
    )
    sukkar_delivery_time_range = fields.Char(
        string="Delivery Time Range",
        compute="_compute_sukkar_delivery_datetime", store=True, readonly=True,
        help="Derived from the chosen delivery slot, e.g. 18:00-20:00.",
    )

    @api.depends("sukkar_delivery_date", "sukkar_delivery_slot_id")
    def _compute_sukkar_delivery_datetime(self):
        for order in self:
            date, slot = order.sukkar_delivery_date, order.sukkar_delivery_slot_id
            if date and slot:
                order.sukkar_delivery_datetime = slot.to_utc(date)
                order.sukkar_delivery_time_range = slot.name
            elif date:
                # Date but no slot yet: schedule at the start of that day so the
                # order still sorts and shows on the calendar.
                order.sukkar_delivery_datetime = self.env["sukkar.delivery.slot"] \
                    ._tz_midnight_utc(date)
                order.sukkar_delivery_time_range = False
            else:
                order.sukkar_delivery_datetime = False
                order.sukkar_delivery_time_range = False
    sukkar_receiver_name = fields.Char(string="Receiver Name")
    sukkar_receiver_phone = fields.Char(string="Receiver Phone")

    # --- Where it goes ------------------------------------------------------
    # A gift usually goes somewhere other than the buyer's own address, and the
    # governorate decides which delivery method (and fee) applies. These fields
    # are captured here on the Gifting tab, but they are written through to
    # `partner_shipping_id` — the address Odoo actually prices delivery against.
    sukkar_deliver_to = fields.Selection(
        [("sender", "Sender's address"), ("other", "Another address")],
        string="Deliver To", default="sender", required=True)

    sukkar_addr_governorate_id = fields.Many2one(
        "res.country.state", string="Governorate",
        domain="[('country_id.code', '=', 'KW')]",
        help="Decides which delivery method and fee apply.")
    sukkar_addr_area = fields.Char(string="Area")
    sukkar_addr_block = fields.Char(string="Block")
    sukkar_addr_street = fields.Char(string="Street")
    sukkar_addr_avenue = fields.Char(string="Avenue")
    sukkar_addr_building = fields.Char(string="House / Building")
    sukkar_addr_floor = fields.Char(string="Floor")
    sukkar_addr_directions = fields.Text(
        string="Directions for the Driver",
        help="Landmark, gate colour, which entrance — anything that helps the driver find it.")

    @api.onchange("sukkar_deliver_to", "partner_id")
    def _onchange_sukkar_deliver_to(self):
        """Show the buyer's own address when delivering to the sender, so the
        fields are never silently blank."""
        for order in self:
            if order.sukkar_deliver_to == "sender" and order.partner_id:
                p = order.partner_id
                order.sukkar_addr_governorate_id = p.state_id
                order.sukkar_addr_area = p.city
                order.sukkar_addr_street = p.street
                order.sukkar_addr_building = p.street2

    def _sukkar_address_vals(self):
        """The delivery address as a res.partner would store it."""
        self.ensure_one()
        street = " ".join(p for p in [
            f"Block {self.sukkar_addr_block}" if self.sukkar_addr_block else "",
            f"Street {self.sukkar_addr_street}" if self.sukkar_addr_street else "",
            f"Avenue {self.sukkar_addr_avenue}" if self.sukkar_addr_avenue else "",
        ] if p)
        street2 = " ".join(p for p in [
            f"Building {self.sukkar_addr_building}" if self.sukkar_addr_building else "",
            f"Floor {self.sukkar_addr_floor}" if self.sukkar_addr_floor else "",
            f"Apt {self.sukkar_addr_apartment}" if self.sukkar_addr_apartment else "",
        ] if p)
        return {
            "name": self.sukkar_receiver_name or self.partner_id.name,
            "phone": self.sukkar_receiver_phone or self.partner_id.phone,
            "type": "delivery",
            "parent_id": self.partner_id.id,
            "street": street or False,
            "street2": street2 or False,
            "city": self.sukkar_addr_area or False,
            "state_id": self.sukkar_addr_governorate_id.id or False,
            "country_id": self.sukkar_addr_governorate_id.country_id.id
                          or self.partner_id.country_id.id or False,
            "comment": self.sukkar_addr_directions or False,
        }

    def _sukkar_has_address(self):
        """Has anybody typed an address on this order at all?"""
        self.ensure_one()
        return any([
            self.sukkar_addr_governorate_id, self.sukkar_addr_area,
            self.sukkar_addr_block, self.sukkar_addr_street,
            self.sukkar_addr_avenue, self.sukkar_addr_building,
            self.sukkar_addr_floor, self.sukkar_addr_apartment,
        ])

    def _sukkar_sync_shipping_address(self):
        """Push the Gifting-tab address onto `partner_shipping_id`.

        This is the whole point of the tab: unless the address reaches
        partner_shipping_id, Odoo keeps pricing delivery against the BUYER's
        governorate and the driver sheet prints the wrong address.
        """
        Partner = self.env["res.partner"]
        for order in self:
            if (order.sukkar_order_type == "pickup"
                    or order.sukkar_deliver_to == "sender"):
                if order.partner_id and order.partner_shipping_id != order.partner_id:
                    order.partner_shipping_id = order.partner_id
                continue
            if not order._sukkar_has_address():
                continue  # nothing meaningful to write yet
            vals = order._sukkar_address_vals()
            shipping = order.partner_shipping_id
            # Reuse the address record only if it belongs to this order alone.
            # Duplicating an order copies partner_shipping_id, so without this
            # check editing the copy would rewrite the original's address.
            reusable = (
                shipping and shipping != order.partner_id
                and shipping.type == "delivery"
                and not self.sudo().search_count([
                    ("partner_shipping_id", "=", shipping.id), ("id", "!=", order.id)])
            )
            if reusable:
                shipping.write(vals)
            else:
                order.partner_shipping_id = Partner.create(vals)

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._sukkar_sync_order_type()
        orders._sukkar_sync_shipping_address()
        return orders

    def write(self, vals):
        res = super().write(vals)
        if {"carrier_id", "sukkar_order_type"} & set(vals):
            self._sukkar_sync_order_type()
        watched = {
            "sukkar_deliver_to", "sukkar_addr_governorate_id", "sukkar_addr_area",
            "sukkar_addr_block", "sukkar_addr_street", "sukkar_addr_avenue",
            "sukkar_addr_building", "sukkar_addr_floor", "sukkar_addr_apartment",
            "sukkar_addr_directions", "sukkar_receiver_name",
            "sukkar_receiver_phone", "sukkar_order_type",
        }
        if watched & set(vals):
            self._sukkar_sync_shipping_address()
        if "sukkar_driver_id" in vals:
            self._sukkar_sync_driver()
        if {"order_line", "sukkar_delivery_date"} & set(vals):
            # An order that grew or shrank after it was confirmed holds the
            # wrong number of cake pieces until this runs.
            self.filtered(lambda o: o.state == "sale")._sukkar_release_pieces()
        return res

    def _sukkar_sync_driver(self):
        """The order and its kitchen ticket name the same driver.

        Dispatch assigns on the ticket, the back office assigns on the order,
        and the invoice prints the order's — so whichever end is set, the other
        follows and the customer's paperwork can never name the wrong man.
        """
        for order in self:
            tickets = order.sukkar_production_ids.filtered(
                lambda t: t.driver_id != order.sukkar_driver_id)
            if tickets:
                tickets.driver_id = order.sukkar_driver_id
    sukkar_sender_phone = fields.Char(
        string="Sender Phone",
        related="partner_id.phone",
        readonly=False,
    )

    # --- Gift ---------------------------------------------------------------
    sukkar_gift_from = fields.Char(string="From")
    sukkar_gift_to = fields.Char(string="To")
    sukkar_gift_message = fields.Char(
        string="Gift Message",
        help="Printed on the gift card. How long it may be is set in "
             "Settings → Sukkar → Card Message Limit.",
    )

    @api.constrains("sukkar_gift_message")
    def _check_sukkar_gift_message(self):
        """The card is a physical card: past the length the shop set, the text
        no longer fits on it. The register stops the cashier at the same number,
        read from the same setting."""
        for order in self.filtered("sukkar_gift_message"):
            limit = (order.company_id or self.env.company).sukkar_card_message_max
            if limit > 0 and len(order.sukkar_gift_message) > limit:
                raise ValidationError(_(
                    "The card message is %(length)s characters; the card holds "
                    "%(limit)s. Shorten it, or raise the limit in Settings → "
                    "Sukkar.",
                    length=len(order.sukkar_gift_message), limit=limit))

    # --- Notes --------------------------------------------------------------
    sukkar_special_requests = fields.Text(string="Special Requests")
    # The note the cashier types on the whole order at the till (Internal
    # Note), as opposed to a note on one line. It rides to the driver on the
    # delivery receipt. (Owner, 30 Sep 2026.)
    sukkar_order_note = fields.Text(string="Order Note")
    # The picture that goes with the internal note — added at the till from
    # the General Note box, or here in the office (owner, 10 Oct 2026).
    sukkar_note_image = fields.Image(
        string="Note Image", max_width=1920, max_height=1920)
    # What the cashier wants the driver to know about this delivery — typed
    # from the delivery popup at the till (owner, 2 Oct 2026). Printed on the
    # delivery slip and the driver receipt.
    sukkar_delivery_note = fields.Text(string="Delivery Note")

    # --- Pre-order reminders -----------------------------------------------
    sukkar_delivery_reminded = fields.Boolean(
        string="Delivery Reminder Sent", copy=False, default=False)

    @api.model
    def _cron_notify_upcoming_deliveries(self):
        """Daily: schedule a 'prepare order' activity for tomorrow's deliveries."""
        tomorrow = fields.Date.today() + timedelta(days=1)
        start = datetime.combine(tomorrow, dt_time.min)
        end = datetime.combine(tomorrow, dt_time.max)
        orders = self.search([
            ("state", "=", "sale"),
            ("sukkar_delivery_reminded", "=", False),
            ("sukkar_delivery_datetime", ">=", fields.Datetime.to_string(start)),
            ("sukkar_delivery_datetime", "<=", fields.Datetime.to_string(end)),
        ])
        for order in orders:
            order.activity_schedule(
                "mail.mail_activity_data_todo",
                date_deadline=tomorrow,
                summary="Delivery tomorrow — prepare order",
                note=order.sukkar_special_requests or "",
                user_id=order.user_id.id or self.env.uid,
            )
            order.sukkar_delivery_reminded = True
        return True

    # --- Kitchen production -------------------------------------------------
    sukkar_production_ids = fields.One2many(
        "sukkar.production", "sale_order_id", string="Kitchen Tickets")
    sukkar_production_count = fields.Integer(
        compute="_compute_sukkar_production_count", string="Kitchen Ticket Count")

    # --- Payment (section 6: COD / Bank link; unpaid link = no delivery) ----
    # "bank_link" was retired 29 Aug 2026 (owner's decision): it fully overlapped
    # Link Payment and zero orders ever used it. The value stays accepted in the
    # gating logic below so nothing historical could ever break.
    sukkar_payment_method = fields.Selection(
        [("cod", "Cash on Delivery"),
         ("link", "Link Payment"),
         ("knet", "KNET"),
         ("cash", "Cash")],
        string="Payment Method", default="cod")
    sukkar_paid = fields.Boolean(
        string="Payment Received", copy=False,
        help="Tick when a Bank-Link / prepayment has been received.")
    sukkar_knet_approval = fields.Char(
        string="KNET Approval No.", copy=False,
        help="KNET approval / reference number captured when payment is confirmed.")
    sukkar_payment_reference = fields.Char(
        string="Payment Link Reference", copy=False,
        help="The reference from the payment link, typed in by hand. There is no "
             "gateway integration, so the system cannot fetch or generate it.")
    sukkar_clear_to_deliver = fields.Boolean(
        string="Clear to Deliver", compute="_compute_sukkar_clear_to_deliver",
        help="Bank-Link orders are only deliverable once payment is received.")
    sukkar_payment_status = fields.Char(
        compute="_compute_sukkar_payment_status", string="Payment", store=True)

    @api.depends("sukkar_paid", "invoice_ids.payment_state")
    def _compute_sukkar_payment_status(self):
        paid_states = ("paid", "in_payment", "reversed")
        for order in self:
            states = order.invoice_ids.mapped("payment_state")
            invoiced_paid = bool(states) and all(s in paid_states for s in states)
            order.sukkar_payment_status = (
                "PAID" if (order.sukkar_paid or invoiced_paid) else "UNPAID")

    @api.depends("sukkar_payment_method", "sukkar_paid", "invoice_ids.payment_state")
    def _compute_sukkar_clear_to_deliver(self):
        paid_states = ("paid", "in_payment", "reversed")
        for order in self:
            invoiced_paid = bool(order.invoice_ids) and all(
                s in paid_states for s in order.invoice_ids.mapped("payment_state"))
            # Requirement 6.3: unpaid link orders must NOT be delivered. That covers
            # both link methods — Bank Link and Link Payment. COD/cash/KNET are fine.
            order.sukkar_clear_to_deliver = (
                order.sukkar_payment_method not in ("bank_link", "link")
                or order.sukkar_paid or invoiced_paid)

    @api.constrains("sukkar_payment_method", "sukkar_paid", "sukkar_payment_reference")
    def _check_sukkar_payment_reference(self):
        """A Link Payment marked paid must carry the reference that proves it."""
        for order in self:
            if (order.sukkar_payment_method == "link" and order.sukkar_paid
                    and not (order.sukkar_payment_reference or "").strip()):
                raise ValidationError(
                    "Enter the payment link reference before marking %s as paid."
                    % order.name)

    # --- Delivery fee -------------------------------------------------------
    # The carrier is chosen with Odoo's "Add shipping" wizard, which drops a
    # delivery line on the order. This surfaces that line's total as a field so
    # it can be shown on the driver sheet, grouped and reported on.
    sukkar_delivery_fee = fields.Monetary(
        string="Delivery Fee", compute="_compute_sukkar_delivery_fee",
        currency_field="currency_id", store=True)

    @api.depends("order_line.is_delivery", "order_line.price_total")
    def _compute_sukkar_delivery_fee(self):
        for order in self:
            order.sukkar_delivery_fee = sum(
                order.order_line.filtered("is_delivery").mapped("price_total"))

    # What the driver must collect on handover: the order total when payment is
    # cash/COD and still unpaid, zero otherwise. The driver receipt prints a
    # price ONLY when this is non-zero — a paid order never shows its value to
    # the receiver (a gift slip must not price the gift).
    sukkar_cash_due = fields.Monetary(
        string="Cash to Collect", compute="_compute_sukkar_cash_due",
        currency_field="currency_id")
    # The part of a till order left for the driver to collect — the till's
    # Cash on Delivery lines. Empty for an office order: the driver collects
    # the whole total (owner, 10 Oct 2026).
    sukkar_cod_amount = fields.Monetary(
        string="Cash on Delivery Amount", currency_field="currency_id", copy=False)

    @api.depends("amount_total", "sukkar_payment_method", "sukkar_payment_status",
                 "sukkar_cod_amount")
    def _compute_sukkar_cash_due(self):
        for order in self:
            order.sukkar_cash_due = (
                (order.sukkar_cod_amount or order.amount_total)
                if (order.sukkar_payment_method in ("cod", "cash")
                    and order.sukkar_payment_status == "UNPAID")
                else 0.0)

    def _prepare_invoice(self):
        # Copy the gifting details onto the invoice so they print on it.
        vals = super()._prepare_invoice()
        vals.update({
            "sukkar_delivery_datetime": self.sukkar_delivery_datetime,
            "sukkar_delivery_time_range": self.sukkar_delivery_time_range,
            "sukkar_receiver_name": self.sukkar_receiver_name,
            "sukkar_receiver_phone": self.sukkar_receiver_phone,
            "sukkar_gift_from": self.sukkar_gift_from,
            "sukkar_gift_to": self.sukkar_gift_to,
            "sukkar_gift_message": self.sukkar_gift_message,
            "sukkar_special_requests": self.sukkar_special_requests,
            "sukkar_payment_method": self.sukkar_payment_method,
            "sukkar_knet_approval": self.sukkar_knet_approval,
            "sukkar_payment_reference": self.sukkar_payment_reference,
            "sukkar_order_type": self.sukkar_order_type,
            "sukkar_driver_id": self.sukkar_driver_id.id,
            "sukkar_flavour_summary": self.sukkar_flavour_summary,
            "sukkar_address_summary": self.sukkar_address_summary,
        })
        return vals

    @api.depends("sukkar_production_ids")
    def _compute_sukkar_production_count(self):
        for order in self:
            order.sukkar_production_count = len(order.sukkar_production_ids)

    def _create_sukkar_production(self):
        Production = self.env["sukkar.production"]
        for order in self:
            if not order.sukkar_production_ids:
                Production.create({"sale_order_id": order.id})

    def action_confirm(self):
        self._sukkar_check_schedule()
        self.env["sukkar.cake.batch"]._sukkar_check_pieces(self)
        res = super().action_confirm()
        self._create_sukkar_production()
        self.env["sukkar.cake.batch"]._sukkar_reserve_for_orders(self)
        return res

    # --- The register's mirror order ----------------------------------------
    sukkar_pos_order_id = fields.Many2one(
        "pos.order", string="Register Order", readonly=True, copy=False,
        index=True, ondelete="set null",
        help="The counter sale this order was created from. The register has "
             "already taken the money and shipped the goods, so this order "
             "neither awards loyalty points nor raises a second delivery.")

    def _update_programs_and_rewards(self):
        """A counter sale earns its points once, at the register.

        The register awards them on validation; this order is the same sale
        written down a second time for the kitchen, so letting the loyalty
        programme run again would pay every customer twice.
        """
        if self.sukkar_pos_order_id:
            return
        return super()._update_programs_and_rewards()

    def _sukkar_release_pieces(self):
        self.env["sukkar.cake.batch"]._sukkar_sync_reservations(self)

    def _action_cancel(self):
        res = super()._action_cancel()
        # Cancelling gives the cake back. Written down as a movement rather than
        # left to a live read of the order's state, so deleting the order later
        # cannot bring the hold back to life.
        self._sukkar_release_pieces()
        return res

    def unlink(self):
        # Same reason: the movements outlive the order, so the order has to give
        # its pieces back before it goes.
        self.filtered(lambda o: o.state != "cancel")._sukkar_release_pieces()
        return super().unlink()

    def action_view_sukkar_production(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Kitchen Tickets",
            "res_model": "sukkar.production",
            "view_mode": "kanban,list,form",
            "domain": [("sale_order_id", "=", self.id)],
            "context": {"default_sale_order_id": self.id},
        }

    # === September 2026 batch ===============================================
    # Shared fields. Declared here, filled in by the builders who own each
    # feature — one declaration each, so no two of them can invent their own.

    # --- Delivery or pickup -------------------------------------------------
    sukkar_order_type = fields.Selection(
        [("delivery", "Delivery"),
         ("pickup", "Pickup")],
        string="Delivery or Pickup", default="delivery",
        help="Both carry a date and a time window: a delivery window for a "
             "delivery, a collection appointment for a pickup.")

    @api.onchange("carrier_id")
    def _onchange_sukkar_carrier_order_type(self):
        for order in self.filtered("carrier_id"):
            order.sukkar_order_type = (
                "pickup" if order.carrier_id.sukkar_is_pickup else "delivery")

    @api.onchange("sukkar_order_type")
    def _onchange_sukkar_order_type(self):
        Carrier = self.env["delivery.carrier"]
        for order in self:
            if order.sukkar_order_type == "pickup":
                order.carrier_id = Carrier._sukkar_pickup_carrier()
            elif order.carrier_id.sukkar_is_pickup:
                order.carrier_id = False

    def _sukkar_sync_order_type(self):
        """Delivery method and Delivery-or-Pickup say the same thing.

        The delivery method is what Odoo prices and what the order receipt
        reads; Delivery or Pickup is what the register and the kitchen screen
        read. Whichever one is set, the other follows, so the two can never
        disagree on the slip the customer is handed.
        """
        pickup_carrier = self.env["delivery.carrier"]._sukkar_pickup_carrier()
        for order in self:
            if order.carrier_id:
                expected = (
                    "pickup" if order.carrier_id.sukkar_is_pickup else "delivery")
                if order.sukkar_order_type != expected:
                    order.sukkar_order_type = expected
            elif order.sukkar_order_type == "pickup" and pickup_carrier:
                order.carrier_id = pickup_carrier

    def _sukkar_is_scheduled(self):
        """Is this an order the shop has to schedule?

        A bare counter sale — products and nothing else — is handed over on the
        spot and has nothing to promise. Everything else is a promise: the
        moment an order names a receiver, an address, a driver, a delivery
        method or a pickup, it has to say WHEN.
        """
        self.ensure_one()
        return bool(
            self.sukkar_delivery_date or self.sukkar_delivery_slot_id
            or self.sukkar_order_type == "pickup"
            or self.carrier_id or self.sukkar_driver_id
            or self.sukkar_receiver_name or self.sukkar_receiver_phone
            or self.sukkar_deliver_to == "other"
            or self.sukkar_addr_governorate_id or self.sukkar_addr_area)

    def _sukkar_check_schedule(self):
        """No order is confirmed without the day and the window it is promised for.

        A delivery needs a window because the receiver has to be home; a pickup
        needs one because the branch has to have it ready and boxed. Neither is
        allowed through half-booked.
        """
        for order in self:
            if not order._sukkar_is_scheduled():
                continue
            if order.sukkar_delivery_date and order.sukkar_delivery_slot_id:
                continue
            raise UserError(_(
                "%s cannot be confirmed yet: choose the date and the time "
                "window. A pickup is a collection appointment, so it needs "
                "them too.", order.name))

    # --- The rest of the address -------------------------------------------
    sukkar_addr_apartment = fields.Char(string="Apartment")
    sukkar_address_summary = fields.Char(
        string="Address", compute="_compute_sukkar_address_summary",
        help="The whole address on one line, for the kitchen ticket and the "
             "driver sheet.")

    @api.depends("sukkar_order_type", "sukkar_addr_area", "sukkar_addr_block",
                 "sukkar_addr_street", "sukkar_addr_avenue",
                 "sukkar_addr_building", "sukkar_addr_floor",
                 "sukkar_addr_apartment", "sukkar_addr_directions")
    def _compute_sukkar_address_summary(self):
        """The eight address parts on one line, assembled in exactly one place.

        The kitchen ticket, the driver sheet and the reports all print this, so
        the address reads identically everywhere. A pickup has no address: the
        customer comes to the branch.
        """
        for order in self:
            if order.sukkar_order_type == "pickup":
                order.sukkar_address_summary = False
                continue
            directions = " ".join((order.sukkar_addr_directions or "").split())
            parts = [
                order.sukkar_addr_area,
                f"Block {order.sukkar_addr_block}" if order.sukkar_addr_block else "",
                f"Street {order.sukkar_addr_street}" if order.sukkar_addr_street else "",
                f"Avenue {order.sukkar_addr_avenue}" if order.sukkar_addr_avenue else "",
                f"Building {order.sukkar_addr_building}" if order.sukkar_addr_building else "",
                f"Floor {order.sukkar_addr_floor}" if order.sukkar_addr_floor else "",
                f"Apt {order.sukkar_addr_apartment}" if order.sukkar_addr_apartment else "",
                directions,
            ]
            order.sukkar_address_summary = ", ".join(p for p in parts if p) or False

    # --- Cake flavours ------------------------------------------------------
    sukkar_flavour_summary = fields.Text(
        string="Cake Flavours",
        compute="_compute_sukkar_flavour_summary", store=True)

    @api.depends("order_line.sukkar_flavour_summary")
    def _compute_sukkar_flavour_summary(self):
        """Every bouquet's flavours, one bouquet per line.

        The kitchen ticket, the invoice and the register receipt all print this
        one text, so an order with two bouquets reads as two lines rather than
        one run-on list nobody can split back apart.
        """
        for order in self:
            summaries = [
                line.sukkar_flavour_summary for line in order.order_line
                if line.sukkar_flavour_summary]
            order.sukkar_flavour_summary = "\n".join(summaries) or False

    # --- Driver (so it reaches the invoice) ---------------------------------
    sukkar_driver_id = fields.Many2one(
        "res.users", string="Driver", copy=False,
        domain="[('sukkar_is_driver', '=', True)]",
        help="Kept in step with the kitchen ticket's driver, and copied onto the "
             "invoice so the customer's paperwork names who delivered.")

    # --- Cash on delivery ageing -------------------------------------------
    sukkar_cod_days_outstanding = fields.Integer(
        string="Days Unpaid",
        compute="_compute_sukkar_cod_overdue")
    sukkar_cod_overdue = fields.Boolean(
        string="COD Overdue",
        compute="_compute_sukkar_cod_overdue",
        search="_search_sukkar_cod_overdue")

    @api.depends("sukkar_payment_method", "sukkar_payment_status", "state",
                 "sukkar_delivery_date", "date_order",
                 "company_id.sukkar_cod_overdue_days")
    def _compute_sukkar_cod_overdue(self):
        """Cash the driver has not brought back yet, ageing by the day.

        The clock starts the day the order was due at the customer's door —
        that is the day the cash should have come back — and an order is chased
        once it has aged past the number of days set in Settings → Sukkar.
        """
        today = fields.Date.context_today(self)
        for order in self:
            due = order.sukkar_delivery_date or (
                order.date_order and order.date_order.date())
            outstanding = (
                order.sukkar_payment_method == "cod"
                and order.sukkar_payment_status == "UNPAID"
                and order.state in ("sale", "done"))
            days = (today - due).days if (outstanding and due and due < today) else 0
            order.sukkar_cod_days_outstanding = days
            order.sukkar_cod_overdue = bool(
                outstanding and days > order.company_id.sukkar_cod_overdue_days)

    def _search_sukkar_cod_overdue(self, operator, value):
        # Allow filtering the overdue collections from the search view.
        candidates = self.search([
            ("sukkar_payment_method", "=", "cod"),
            ("sukkar_payment_status", "=", "UNPAID"),
            ("state", "in", ("sale", "done")),
        ])
        overdue_ids = candidates.filtered("sukkar_cod_overdue").ids
        positive = (operator == "=" and value) or (operator == "!=" and not value)
        return [("id", "in" if positive else "not in", overdue_ids)]

    # --- Cake pieces: stock and reservation ---------------------------------
    sukkar_piece_total = fields.Float(
        string="Cake Pieces", digits="Product Unit of Measure",
        compute="_compute_sukkar_piece_total")
    sukkar_oversell_approved = fields.Boolean(
        string="Stock Override Approved", copy=False,
        help="A manager approved confirming this order although there were not "
             "enough cake pieces. The approval itself is in Stock Overrides.")
    sukkar_override_ids = fields.One2many(
        "sukkar.cake.override", "sale_order_id", string="Stock Overrides",
        copy=False)

    @api.depends("order_line.sukkar_piece_qty")
    def _compute_sukkar_piece_total(self):
        for order in self:
            order.sukkar_piece_total = sum(
                order.order_line.mapped("sukkar_piece_qty"))
