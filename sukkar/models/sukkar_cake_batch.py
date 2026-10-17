from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

# The five states a cake piece can be in. Imported by sukkar_cake_move so both
# models can never drift apart.
PIECE_STATES = [
    ("undecorated", "Undecorated"),
    ("decorated", "Decorated (display fridge)"),
    ("reserved", "Reserved for Tomorrow"),
    ("available", "Available"),
    ("waste", "Expired / Damaged / Wasted"),
]


class SukkarCakeBatch(models.Model):
    """One daily delivery of cake pieces — its own batch, start to finish.

    Pieces arrive on a supplier invoice, are counted in, and from then on every
    piece in the batch sits in exactly one of the five states above. Moving
    pieces between states never changes how many pieces physically exist; only
    receiving, selling and wasting do.

    Two of the five states describe WHERE a piece is — Undecorated (in the back)
    and Decorated (in the display fridge) — and those two always add up to
    Physical Remaining. "Reserved for Tomorrow" sits on top of them: a reserved
    piece is still physically in the shop, and still has to be decorated, which
    is exactly why the owner asked for the two figures side by side.
    """
    _name = "sukkar.cake.batch"
    _description = "Daily Cake Piece Batch"
    _inherit = ["mail.thread"]
    _order = "receipt_date desc, id desc"

    name = fields.Char(default="New", copy=False, readonly=True, index=True)
    receipt_date = fields.Date(
        string="Received On", required=True,
        default=fields.Date.context_today, tracking=True, index=True,
        help="The day this delivery arrived. Each day's delivery is its own batch.")
    production_date = fields.Date(
        string="Produced On", tracking=True,
        help="The day the pieces were baked, when it differs from the day they arrived.")
    expiry_date = fields.Date(string="Use By", tracking=True)

    product_id = fields.Many2one(
        "product.product", string="Cake Piece",
        default=lambda self: self.env.company.sukkar_cake_piece_product_id,
        help="The stock item a piece is. Defaults to the company's cake-piece "
             "product (Settings → Sukkar).")
    partner_id = fields.Many2one("res.partner", string="Supplier")
    supplier_invoice_id = fields.Many2one(
        "account.move", string="Supplier Invoice",
        domain="[('move_type', '=', 'in_invoice')]", copy=False)

    state = fields.Selection(
        [("draft", "Draft"), ("open", "Open"), ("closed", "Closed")],
        default="draft", tracking=True, string="Status", copy=False)

    # --- The counts ---------------------------------------------------------
    qty_opening = fields.Float(
        string="Opening", digits="Product Unit of Measure",
        help="Pieces carried into this batch from an earlier one.")
    qty_received = fields.Float(
        string="Received", digits="Product Unit of Measure", tracking=True)
    qty_sold = fields.Float(
        string="Sold", digits="Product Unit of Measure",
        compute="_compute_sukkar_quantities", store=True)
    qty_waste = fields.Float(
        string="Wasted / Expired", digits="Product Unit of Measure",
        compute="_compute_sukkar_quantities", store=True)
    qty_undecorated = fields.Float(
        string="Undecorated", digits="Product Unit of Measure",
        compute="_compute_sukkar_quantities", store=True)
    qty_decorated = fields.Float(
        string="Decorated", digits="Product Unit of Measure",
        compute="_compute_sukkar_quantities", store=True)
    qty_reserved = fields.Float(
        string="Reserved for Tomorrow", digits="Product Unit of Measure",
        compute="_compute_sukkar_quantities", store=True)

    # The two figures the owner asked to see separately (K5).
    qty_physical = fields.Float(
        string="Physical Remaining", digits="Product Unit of Measure",
        compute="_compute_sukkar_quantities", store=True,
        help="Opening + Received − Sold − Waste/Expired. Everything still in the shop.")
    qty_available = fields.Float(
        string="Available", digits="Product Unit of Measure",
        compute="_compute_sukkar_quantities", store=True,
        help="Physical Remaining − Reserved for Tomorrow. What a new order may take.")

    move_ids = fields.One2many(
        "sukkar.cake.move", "batch_id", string="Movements")
    move_count = fields.Integer(compute="_compute_move_count")

    note = fields.Text(string="Notes")
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, required=True)

    @api.depends("qty_opening", "qty_received", "move_ids.qty",
                 "move_ids.move_type", "move_ids.state_from", "move_ids.state_to",
                 "move_ids.sale_order_id.state")
    def _compute_sukkar_quantities(self):
        for rec in self:
            arrived = rec.qty_opening + rec.qty_received
            to_fridge = sold = waste = held = 0.0
            left_back = left_fridge = left_unplaced = 0.0
            for move in rec.move_ids:
                qty, kind = move.qty, move.move_type
                if kind == "receive":
                    arrived += qty
                elif kind == "decorate":
                    to_fridge += qty
                elif kind in ("reserve", "unreserve"):
                    # A cancelled order holds nothing: the pieces never left the
                    # shop, so the promise lapses with the order — and so does
                    # the row that released it, or the two would cancel twice.
                    if move.sale_order_id and move.sale_order_id.state == "cancel":
                        continue
                    held += qty if kind == "reserve" else -qty
                elif kind == "move":
                    # Same pieces, different shelf: the count never changes.
                    if move.state_from == "decorated":
                        to_fridge -= qty
                    if move.state_to == "decorated":
                        to_fridge += qty
                elif kind == "adjust":
                    arrived += qty
                    if move.state_to == "decorated":
                        to_fridge += qty
                elif kind in ("consume", "waste"):
                    if kind == "consume":
                        sold += qty
                    else:
                        waste += qty
                    if move.state_from == "reserved":
                        held -= qty
                        left_unplaced += qty
                    elif move.state_from == "decorated":
                        left_fridge += qty
                    elif move.state_from == "undecorated":
                        left_back += qty
                    else:
                        left_unplaced += qty
            # Pieces taken without saying where they sat come out of the display
            # fridge first, then out of the back.
            in_fridge = to_fridge - left_fridge
            from_fridge = min(max(in_fridge, 0.0), left_unplaced)
            rec.qty_sold = sold
            rec.qty_waste = waste
            rec.qty_decorated = in_fridge - from_fridge
            rec.qty_undecorated = (
                arrived - to_fridge - left_back - (left_unplaced - from_fridge))
            rec.qty_physical = arrived - sold - waste
            rec.qty_reserved = held
            rec.qty_available = rec.qty_physical - held

    @api.depends("move_ids")
    def _compute_move_count(self):
        for rec in self:
            rec.move_count = len(rec.move_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "sukkar.cake.batch") or "New"
        return super().create(vals_list)

    # ------------------------------------------------------------- the screen
    def action_sukkar_open(self):
        """Count the delivery in: from here its pieces can be sold."""
        self.write({"state": "open"})

    def action_sukkar_close(self):
        """Finished with — nothing more is taken from this batch."""
        self.write({"state": "closed"})

    def _sukkar_move_action(self, name, defaults):
        self.ensure_one()
        context = dict(self.env.context, default_batch_id=self.id)
        context.update(defaults)
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": "sukkar.cake.move",
            "view_mode": "form",
            "views": [(self.env.ref("sukkar.sukkar_cake_move_view_form").id, "form")],
            "target": "new",
            "context": context,
        }

    def action_sukkar_decorate(self):
        return self._sukkar_move_action(_("Move to Display Fridge"), {
            "default_move_type": "decorate",
            "default_state_from": "undecorated",
            "default_state_to": "decorated",
            "default_qty": self.qty_undecorated,
        })

    def action_sukkar_waste(self):
        return self._sukkar_move_action(_("Write Off Waste"), {
            "default_move_type": "waste",
            "default_state_from": "available",
            "default_state_to": "waste",
        })

    def action_sukkar_move_pieces(self):
        return self._sukkar_move_action(_("Move Pieces"), {
            "default_move_type": "move",
            "default_state_from": "decorated",
            "default_state_to": "undecorated",
        })

    def action_view_sukkar_moves(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Cake Movements"),
            "res_model": "sukkar.cake.move",
            "view_mode": "list,form",
            "domain": [("batch_id", "=", self.id)],
            "context": {"default_batch_id": self.id},
        }

    # === The cake-piece service =============================================
    # Everything that needs to know how many pieces the shop has — the order
    # confirmation gate, the kitchen, the register — asks here, so there is one
    # set of rules and one ledger.

    @api.model
    def _sukkar_piece_product(self, company=None):
        """The item that counts as one cake piece (Settings → Sukkar)."""
        return (company or self.env.company).sukkar_cake_piece_product_id

    @api.model
    def _sukkar_order_pieces(self, order):
        """How many cake pieces an order needs, read off the recipes (K3)."""
        company = order.company_id or self.env.company
        if not self._sukkar_piece_product(company):
            return 0.0
        total = 0.0
        for line in order.order_line:
            if line.display_type or not line.product_id:
                continue
            pieces = line.product_id.with_company(company).sukkar_piece_count
            total += pieces * line.product_uom_qty
        return total

    @api.model
    def _sukkar_sellable_batches(self, company=None):
        """Every batch a piece may still be taken from, oldest first (K1).

        Past its Use By date a batch is out of the running: those pieces are
        expired, and are written off rather than sold.
        """
        company = company or self.env.company
        today = fields.Date.context_today(self)
        return self.search(
            [("company_id", "=", company.id),
             ("state", "=", "open"),
             "|", ("expiry_date", "=", False), ("expiry_date", ">=", today)],
            order="receipt_date asc, id asc")

    @api.model
    def _sukkar_available_pieces(self, company=None):
        """Pieces a new order may take: physically here and not already promised.

        A batch that has gone past its Use By date stops being sellable, but the
        orders promised against it are still promised. Those pieces have to come
        out of what is fresh, or the same cake is sold twice.
        """
        company = company or self.env.company
        sellable = self._sukkar_sellable_batches(company)
        stranded = self.search([
            ("company_id", "=", company.id),
            ("id", "not in", sellable.ids),
            ("qty_reserved", ">", 0),
        ])
        return (sum(sellable.mapped("qty_available"))
                - sum(stranded.mapped("qty_reserved")))

    @api.model
    def _sukkar_in_reserve_window(self, order):
        """Is this order near enough to hold today's pieces for?

        Cake pieces do not survive long, so an order for a far-off date neither
        holds today's stock nor is judged against it. How far ahead counts is
        Settings → Sukkar → Reserve Pieces Ahead.
        """
        company = order.company_id or self.env.company
        due = order.sukkar_delivery_date
        if not due:
            return True
        horizon = max(company.sukkar_reserve_horizon_days, 0)
        return due <= fields.Date.context_today(self) + timedelta(days=horizon)

    @api.model
    def _sukkar_held_by_batch(self, order):
        """Pieces this order is still holding, batch by batch."""
        held = {}
        moves = self.env["sukkar.cake.move"].search(
            [("sale_order_id", "=", order.id)], order="id asc")
        for move in moves:
            batch = move.batch_id
            if move.move_type == "reserve":
                held[batch] = held.get(batch, 0.0) + move.qty
            elif move.move_type == "unreserve":
                held[batch] = held.get(batch, 0.0) - move.qty
            elif move.move_type in ("consume", "waste") and move.state_from == "reserved":
                held[batch] = held.get(batch, 0.0) - move.qty
        return held

    @api.model
    def _sukkar_used_by_order(self, order):
        """Pieces this order has already taken out of stock."""
        return sum(self.env["sukkar.cake.move"].search(
            [("sale_order_id", "=", order.id),
             ("move_type", "=", "consume")]).mapped("qty"))

    @api.model
    def _sukkar_check_pieces(self, orders):
        """Refuse to confirm an order the shop cannot cover (K5).

        Whether it refuses at all is Settings → Sukkar → Block Orders Beyond
        Available Pieces; a manager who approves the order through the override
        wizard sets sukkar_oversell_approved and is let straight through.
        """
        if self.env.context.get("sukkar_sale_already_taken"):
            # A sale the register has already been paid for. Refusing it here
            # would throw away the record of something that has happened, at the
            # counter, with the money in the drawer. The shortfall is still
            # written down: the reservation below drives Available negative,
            # which is the shop's own signal to count and reorder.
            return
        for order in orders:
            company = order.company_id or self.env.company
            if not company.sukkar_block_oversell or order.sukkar_oversell_approved:
                continue
            needed = self._sukkar_order_pieces(order)
            if needed <= 0 or not self._sukkar_in_reserve_window(order):
                continue
            available = self._sukkar_available_pieces(company)
            if float_compare(needed, available, precision_digits=2) <= 0:
                continue
            raise UserError(_(
                "Insufficient cake stock. Only %(available)s pieces are available "
                "and this order needs %(needed)s.\n\n"
                "A manager can still approve it: Actions ▸ Cake Stock Override.",
                available="%g" % available, needed="%g" % needed))

    @api.model
    def _sukkar_reserve_for_orders(self, orders):
        """Hold a confirmed order's pieces against the oldest batches (K4).

        Reserving does not take anything out of the shop — it only stops the
        same pieces being promised twice. They leave stock later, when the
        kitchen produces the order.
        """
        return self._sukkar_sync_reservations(orders)

    @api.model
    def _sukkar_sync_reservations(self, orders):
        """Make what each order holds match what it still needs.

        Called at confirmation and again whenever the order changes, because an
        order that is edited after it is confirmed is the normal case, not the
        exception: a bouquet is dropped, a second one is added, the customer
        cancels. A hold that is never given back is cake the shop can never sell
        again, and a hold that never grows is cake promised twice.
        """
        Move = self.env["sukkar.cake.move"]
        for order in orders:
            in_window = self._sukkar_in_reserve_window(order)
            wanted = self._sukkar_order_pieces(order) if in_window else 0.0
            if order.state == "cancel":
                wanted = 0.0
            wanted = max(0.0, wanted - self._sukkar_used_by_order(order))
            held = {b: q for b, q in self._sukkar_held_by_batch(order).items()
                    if float_compare(q, 0.0, precision_digits=2) > 0}
            sellable = self._sukkar_sellable_batches(order.company_id)

            # 1 · give back what is no longer needed, and anything held on a
            #     batch that has gone past its Use By date — those pieces are
            #     written off, so the promise has to move to fresh cake.
            surplus = sum(held.values()) - wanted
            order_batches = sorted(
                held, key=lambda b: (b in sellable, -b.id))
            for batch in order_batches:
                give_back = held[batch] if batch not in sellable else min(
                    held[batch], max(surplus, 0.0))
                if float_compare(give_back, 0.0, precision_digits=2) <= 0:
                    continue
                Move.create(self._sukkar_move_vals(
                    batch, give_back, "unreserve", "reserved", "available", order))
                held[batch] -= give_back
                surplus -= give_back

            # 2 · take what is still short, oldest batch first.
            needed = wanted - sum(held.values())
            if float_compare(needed, 0.0, precision_digits=2) <= 0 or not sellable:
                continue
            for batch in sellable:
                if float_compare(needed, 0.0, precision_digits=2) <= 0:
                    break
                take = min(batch.qty_available, needed)
                if take <= 0:
                    continue
                Move.create(self._sukkar_move_vals(
                    batch, take, "reserve", "available", "reserved", order))
                needed -= take
            if float_compare(needed, 0.0, precision_digits=2) > 0:
                # Approved past what is there: the promise is still written down,
                # on the newest batch, so Available shows the shop is short.
                Move.create(self._sukkar_move_vals(
                    sellable[-1], needed, "reserve", "available", "reserved", order))

    @api.model
    def _sukkar_consume_for_order(self, order, production=None):
        """Take an order's pieces out of stock, once (K4).

        The pieces were held at confirmation; producing the order turns that
        hold into pieces used. Anything the order did not manage to hold comes
        out of the oldest batch still open, so the count can never be doubled.
        """
        Move = self.env["sukkar.cake.move"]
        needed = self._sukkar_order_pieces(order)
        if needed <= 0:
            return
        # The order may have been edited, or its batch may have expired since it
        # was confirmed. Settle the holds first, so what is made is taken out of
        # cake that still exists.
        self._sukkar_sync_reservations(order)
        needed -= self._sukkar_used_by_order(order)
        if float_compare(needed, 0.0, precision_digits=2) <= 0:
            return
        for batch, qty in self._sukkar_held_by_batch(order).items():
            if float_compare(needed, 0.0, precision_digits=2) <= 0:
                break
            take = min(qty, needed)
            if take <= 0:
                continue
            Move.create(self._sukkar_move_vals(
                batch, take, "consume", "reserved", False, order, production))
            needed -= take
        batches = self._sukkar_sellable_batches(order.company_id)
        for batch in batches:
            if float_compare(needed, 0.0, precision_digits=2) <= 0:
                break
            take = min(batch.qty_physical, needed)
            if take <= 0:
                continue
            Move.create(self._sukkar_move_vals(
                batch, take, "consume", "available", False, order, production))
            needed -= take
        if float_compare(needed, 0.0, precision_digits=2) > 0 and batches:
            # More went out than the shop had on paper. It is recorded anyway:
            # a Physical Remaining below zero is the shop's own signal to count.
            Move.create(self._sukkar_move_vals(
                batches[-1], needed, "consume", "available", False, order, production))

    @api.model
    def _sukkar_move_vals(self, batch, qty, move_type, state_from, state_to,
                          order=None, production=None):
        return {
            "batch_id": batch.id,
            "qty": qty,
            "move_type": move_type,
            "state_from": state_from,
            "state_to": state_to,
            "sale_order_id": order.id if order else False,
            "production_id": production.id if production else False,
            "company_id": batch.company_id.id,
        }
