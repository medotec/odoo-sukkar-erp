from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PosOrder(models.Model):
    """Delivery details captured at the register.

    A counter sale that is actually a DELIVERY (gift or otherwise) carries its
    delivery window, receiver and driver here, and on validation the order is
    handed to the same machinery every delivery already uses: a confirmed sale
    order → kitchen ticket → dispatch → driver receipt. One pipeline, not two.
    """
    _inherit = "pos.order"

    sukkar_delivery_date = fields.Date(string="Delivery Date")
    sukkar_delivery_slot_id = fields.Many2one(
        "sukkar.delivery.slot", string="Delivery Window", ondelete="restrict")
    sukkar_receiver_name = fields.Char(string="Receiver Name")
    sukkar_receiver_phone = fields.Char(string="Receiver Phone")
    # Who it goes to and which of their addresses, kept as the records the
    # counter picked (29 Sep 2026). The flat sukkar_addr_* fields below are
    # still filled from that address, so nothing downstream had to change.
    sukkar_recipient_id = fields.Many2one(
        "res.partner", string="Recipient",
        help="The person the gift goes to. Empty when the customer receives "
             "it themselves.")
    sukkar_address_id = fields.Many2one(
        "res.partner", string="Delivery Address",
        help="The saved address the counter picked.")
    sukkar_addr_governorate_id = fields.Many2one(
        "res.country.state", string="Governorate",
        domain="[('country_id.code', '=', 'KW')]")
    sukkar_addr_area = fields.Char(string="Area")
    sukkar_addr_block = fields.Char(string="Block")
    sukkar_addr_street = fields.Char(string="Street")
    sukkar_addr_building = fields.Char(string="House / Building")
    sukkar_addr_directions = fields.Text(string="Directions for the Driver")
    # What the cashier wants the driver to know about THIS delivery (owner,
    # 2 Oct 2026) — the address's own directions stay with the address.
    sukkar_delivery_note = fields.Text(string="Delivery Note")
    sukkar_driver_id = fields.Many2one(
        "res.users", string="Driver", domain="[('sukkar_is_driver', '=', True)]")
    sukkar_sale_order_id = fields.Many2one(
        "sale.order", string="Delivery Order", readonly=True, copy=False,
        help="The sale order created for the kitchen/dispatch pipeline.")

    # === September 2026 batch ===============================================
    # Shared fields, declared here so no two builders invent their own.

    # --- Delivery or pickup: the register forces the choice -----------------
    sukkar_order_type = fields.Selection(
        [("delivery", "Delivery"),
         ("pickup", "Pickup")],
        string="Delivery or Pickup")

    # --- The rest of the address (the back office already has all eight) ----
    sukkar_addr_avenue = fields.Char(string="Avenue")
    sukkar_addr_floor = fields.Char(string="Floor")
    sukkar_addr_apartment = fields.Char(string="Apartment")

    # --- Sender vs receiver, said out loud ----------------------------------
    sukkar_sender_name = fields.Char(string="Sender Name")
    sukkar_sender_phone = fields.Char(string="Sender Phone")

    # --- Delivery fee the cashier picks -------------------------------------
    sukkar_delivery_fee = fields.Float(
        string="Delivery Fee", digits="Product Price",
        help="The fee the cashier picked at the register. It is charged as its "
             "own line, so it shows separately on the receipt and the invoice.")

    # --- The register's own copy of the delivery choice ----------------------
    # The till keeps unpaid orders in the browser's database, and it only saves
    # them again when a real field changes. So the choice the cashier makes —
    # delivery or pickup, the day, the window, who receives it, where and the
    # fee — is held here as one field as well as in the fields above. Without
    # it, a page reload lost everything that had not yet been paid for.
    # (Owner's stop, 29 Sep 2026.)
    sukkar_delivery_state = fields.Text(
        string="Delivery Choice (register)", copy=False)

    # --- Cake flavours ------------------------------------------------------
    sukkar_flavour_summary = fields.Text(
        string="Cake Flavours",
        compute="_compute_sukkar_flavour_summary")

    @api.depends("lines.sukkar_flavour_summary")
    def _compute_sukkar_flavour_summary(self):
        """Every bouquet's flavours, one bouquet per line — the same shape the
        back-office order rolls up to, so the receipt and the invoice agree."""
        for order in self:
            summaries = [
                line.sukkar_flavour_summary for line in order.lines
                if line.sukkar_flavour_summary]
            order.sukkar_flavour_summary = "\n".join(summaries) or False

    # --- Cake pieces --------------------------------------------------------
    sukkar_oversell_approved = fields.Boolean(
        string="Stock Override Approved", copy=False)
    sukkar_override_ids = fields.One2many(
        "sukkar.cake.override", "pos_order_id", string="Stock Overrides",
        copy=False)

    @api.constrains("sukkar_order_type", "sukkar_delivery_date",
                    "sukkar_delivery_slot_id")
    def _check_sukkar_schedule(self):
        """Delivery or pickup, the register may not close it half-booked.

        The popup asks for the day and the window before the cashier can carry
        on; this is the backstop, so an order that reaches the server without
        them is refused rather than quietly becoming a counter sale.
        """
        for order in self:
            if not order.sukkar_order_type:
                continue
            if order.sukkar_delivery_date and order.sukkar_delivery_slot_id:
                continue
            raise ValidationError(_(
                "Choose the date and the time window before validating this "
                "order. A pickup is a collection appointment, so it needs them "
                "too."))

    @api.constrains("sukkar_delivery_fee")
    def _check_sukkar_delivery_fee(self):
        """The fee is either an area's own price or one of the amounts the
        owner listed in Settings. Anything else is a typo, and a typo in a
        delivery fee is money."""
        for order in self:
            if not order.sukkar_delivery_fee:
                continue
            company = order.company_id or self.env.company
            allowed = company._sukkar_pos_fee_amounts()
            areas = self.env["sukkar.area"].with_context(
                active_test=False).search([("sukkar_delivery_fee", ">", 0)])
            allowed += areas.mapped("sukkar_delivery_fee")
            precision = company.currency_id.rounding or 0.001
            if not any(abs(order.sukkar_delivery_fee - a) < precision
                       for a in allowed):
                raise ValidationError(_(
                    "%(fee)s %(currency)s is not a delivery fee this shop "
                    "charges. The amounts a cashier may pick are set in "
                    "Settings → Sukkar; area prices are set on the area.",
                    fee=f"{order.sukkar_delivery_fee:g}",
                    currency=company.currency_id.name))

    @api.model
    def _sukkar_fee_product(self):
        """The product the register charges the delivery fee on.

        One service item whose price the cashier picks, not one product per
        amount: changing the range in Settings → Sukkar then never means
        creating or retiring products. It is created once and written into the
        setting, so an admin can point the fee at a different item later.
        """
        company = self.env.company
        if company.sukkar_pos_fee_product_id:
            return company.sukkar_pos_fee_product_id
        product = self.env["product.product"].sudo().create({
            "name": "Delivery Fee",
            "type": "service",
            "sale_ok": True,
            "purchase_ok": False,
            "available_in_pos": True,
            "list_price": (company._sukkar_pos_fee_amounts() or [0])[0],
        })
        company.sudo().sukkar_pos_fee_product_id = product
        return product

    # The register sends these keys inside the order payload (see the client's
    # serialize patch); they are plain fields here, so create() takes them as-is.
    @api.model
    def _process_order(self, order, existing_order):
        order_id = super()._process_order(order, existing_order)
        new_order = self.browse(order_id)
        new_order._sukkar_create_delivery_order()
        new_order._sukkar_cancel_refunded_delivery()
        return order_id

    def _sukkar_cancel_refunded_delivery(self):
        """Money back means the cake is not made.

        A refund taken at the register left the original delivery order
        confirmed: the kitchen kept making it, the driver kept a run for it and
        its cake pieces stayed held. Refunding every line of the original order
        cancels it; a partial refund only re-sizes it, so it is left alone and
        the office decides.
        """
        for refund in self:
            refunded = refund.lines.mapped("refunded_orderline_id.order_id")
            for original in refunded:
                sale_order = original.sukkar_sale_order_id
                if not sale_order or sale_order.state == "cancel":
                    continue
                if not original._sukkar_fully_refunded():
                    continue
                sale_order._action_cancel()
        return True

    def _sukkar_fully_refunded(self):
        """Has every unit sold on this order been handed back?"""
        self.ensure_one()
        for line in self.lines.filtered(lambda l: l.qty > 0):
            returned = -sum(line.refund_orderline_ids.mapped("qty"))
            if returned + 1e-6 < line.qty:
                return False
        return True

    # ------------------------------------------------------------- delivery
    def _sukkar_walkin_partner(self):
        """Fallback customer so a delivery never dies on a missing partner."""
        partner = self.env["res.partner"].search(
            [("name", "=", "POS Walk-in Customer")], limit=1)
        return partner or self.env["res.partner"].create(
            {"name": "POS Walk-in Customer"})

    def _sukkar_payment_method(self):
        """Cash vs KNET vs Cash on Delivery, read from how the register was
        actually paid. Cash on Delivery wins: if any part waits for the driver,
        the order is not settled at the counter."""
        self.ensure_one()
        if self._sukkar_cod_amount():
            return "cod"
        if any(self.payment_ids.payment_method_id.mapped("sukkar_is_link")):
            return "link"
        names = " ".join(self.payment_ids.mapped("payment_method_id.name")).lower()
        return "knet" if "knet" in names else "cash"

    def _sukkar_cod_amount(self):
        """What the driver has to collect: the till's Cash on Delivery lines."""
        self.ensure_one()
        return sum(self.payment_ids.filtered(
            lambda p: p.payment_method_id.sukkar_is_cod).mapped("amount"))

    def _sukkar_create_delivery_order(self):
        """One confirmed sale order per scheduled POS order.

        A delivery and a branch pickup both take this road: the kitchen still
        has to make it, and the only difference is who carries it out of the
        door. Idempotent (skips orders that already have theirs), and a
        SALE order rather than a parallel structure: the payment gate, kitchen
        board, dispatch screen, calendars and driver receipt all keep working
        untouched. The sale order is never invoiced — the register already took
        the money — so the P&L does not double-count.
        """
        for pos_order in self:
            if (not pos_order.sukkar_delivery_date
                    or not pos_order.sukkar_delivery_slot_id
                    or pos_order.sukkar_sale_order_id):
                continue
            partner = pos_order.partner_id or pos_order._sukkar_walkin_partner()
            lines = pos_order.lines.filtered(
                lambda l: l.product_id and l.qty > 0)
            gift_message, notes = pos_order._sukkar_split_line_notes(lines)
            is_pickup = pos_order.sukkar_order_type == "pickup"
            # Any part of an address the cashier typed means the cake goes
            # somewhere other than the payer's own house. Reading the
            # governorate alone sent a full address — area, block, street — to
            # the driver receipt as the walk-in customer's name.
            has_address = (not is_pickup) and any([
                pos_order.sukkar_addr_governorate_id,
                pos_order.sukkar_addr_area, pos_order.sukkar_addr_block,
                pos_order.sukkar_addr_street, pos_order.sukkar_addr_avenue,
                pos_order.sukkar_addr_building, pos_order.sukkar_addr_floor,
                pos_order.sukkar_addr_apartment,
            ])
            fee_product = (pos_order.company_id
                           or self.env.company).sukkar_pos_fee_product_id
            sale_order = self.env["sale.order"].create({
                "partner_id": partner.id,
                "origin": pos_order.name,
                "sukkar_pos_order_id": pos_order.id,
                "sukkar_order_type": pos_order.sukkar_order_type or "delivery",
                "sukkar_delivery_date": pos_order.sukkar_delivery_date,
                "sukkar_delivery_slot_id": pos_order.sukkar_delivery_slot_id.id,
                "sukkar_receiver_name": pos_order.sukkar_receiver_name,
                "sukkar_receiver_phone": pos_order.sukkar_receiver_phone,
                "sukkar_deliver_to": "other" if has_address else "sender",
                "sukkar_addr_governorate_id": pos_order.sukkar_addr_governorate_id.id,
                "sukkar_addr_area": pos_order.sukkar_addr_area,
                "sukkar_addr_block": pos_order.sukkar_addr_block,
                "sukkar_addr_street": pos_order.sukkar_addr_street,
                "sukkar_addr_avenue": pos_order.sukkar_addr_avenue,
                "sukkar_addr_building": pos_order.sukkar_addr_building,
                "sukkar_addr_floor": pos_order.sukkar_addr_floor,
                "sukkar_addr_apartment": pos_order.sukkar_addr_apartment,
                "sukkar_addr_directions": pos_order.sukkar_addr_directions,
                "sukkar_delivery_note": pos_order.sukkar_delivery_note or False,
                "sukkar_gift_message": gift_message,
                "sukkar_special_requests": "\n".join(notes) or False,
                "sukkar_order_note": pos_order._sukkar_internal_notes(lines),
                "sukkar_payment_method": pos_order._sukkar_payment_method(),
                "sukkar_driver_id": pos_order.sukkar_driver_id.id,
                # An override given at the till is an override: without this the
                # back-office order is blocked a second time for the same pieces.
                "sukkar_oversell_approved": pos_order.sukkar_oversell_approved,
                # The register already collected the money — unless it was
                # Cash on Delivery: then the driver collects it, and the order
                # turns paid when he hands it in (owner, 10 Oct 2026).
                "sukkar_paid": not pos_order._sukkar_cod_amount(),
                "sukkar_cod_amount": pos_order._sukkar_cod_amount(),
                # A Link paid at the till was checked by the cashier there; the
                # till order is the reference that proves it.
                "sukkar_payment_reference": (
                    pos_order.pos_reference or pos_order.name)
                if pos_order._sukkar_payment_method() == "link" else False,
                "order_line": [
                    (0, 0, pos_order._sukkar_sale_line_vals(line, fee_product))
                    for line in lines],
            })
            # The register has already taken the money: the back office records
            # this order, it does not get to refuse it. See _sukkar_check_pieces.
            sale_order.with_context(
                sukkar_sale_already_taken=True).action_confirm()
            if pos_order.sukkar_driver_id:
                sale_order.sukkar_production_ids.driver_id = \
                    pos_order.sukkar_driver_id
            pos_order.sukkar_sale_order_id = sale_order
        return True

    def _sukkar_internal_notes(self, lines):
        """The register's Note tile writes an internal note on the selected
        line; Actions → General Note writes one on the whole order. Both are
        for the shop, never the customer, and the kitchen has to read them
        (owner, 2 Oct 2026) — they reached nobody before."""
        self.ensure_one()
        notes = [self.general_note] if self.general_note else []
        notes += [f"{line.product_id.display_name}: {line.note}"
                  for line in lines if line.note]
        return "\n".join(notes) or False

    def _sukkar_split_line_notes(self, lines):
        """What goes on the card, and what the kitchen is being asked to do.

        The register stores the greeting-card text as the card LINE's note.
        Filed with the rest of the notes it reached the kitchen as a red special
        request while the Card box on the wall screen, the kitchen ticket and
        the invoice all printed empty — so the card went out blank and the one
        band reserved for real instructions carried a private message instead.
        """
        self.ensure_one()
        Production = self.env["sukkar.production"]
        card_texts, notes = [], []
        for line in lines:
            note = line.customer_note
            if not note:
                continue
            if Production._sukkar_is_card(line.product_id):
                card_texts.append(note)
            else:
                notes.append(note)
        message = "\n".join(card_texts)
        # The register's own text box stops at the same limit; this only bites if
        # the setting was lowered after the order was taken, and a paid sale must
        # not be thrown away over the length of a card.
        limit = (self.company_id or self.env.company).sukkar_card_message_max
        if limit > 0:
            message = message[:limit]
        return message or False, notes

    def _sukkar_sale_line_vals(self, line, fee_product):
        """One register line as a line on the delivery order."""
        vals = {
            "product_id": line.product_id.id,
            "product_uom_qty": line.qty,
            "price_unit": line.price_unit,
            "discount": line.discount,
        }
        if fee_product and line.product_id == fee_product:
            # A delivery fee is money, not something the kitchen makes: flagged
            # as a delivery line it stays off the kitchen ticket, the driver
            # sheet and the order receipt's item list, and it is counted in the
            # order's Delivery Fee instead.
            vals["is_delivery"] = True
        if line.sukkar_note_image:
            vals["sukkar_note_image"] = line.sukkar_note_image
        if line.sukkar_flavour_line_ids:
            vals["sukkar_flavour_line_ids"] = [
                (0, 0, {"flavour_id": flavour.flavour_id.id, "qty": flavour.qty})
                for flavour in line.sukkar_flavour_line_ids]
        return vals
