from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SukkarDriverCashup(models.Model):
    """Taking the day's cash off a driver, order by order.

    A driver who delivers a cash order comes back holding the shop's money.
    Until it is handed in, the shop cannot tell what he is carrying from what
    a customer has not paid — both look the same on the order.

    This is the hand-in: pick the driver, and every cash order he is still
    holding money for is listed with its amount. Count what he puts on the
    counter, type it in, press Collect. Every order on the list is marked paid
    in cash, and the hand-in stays as the record of who handed over how much,
    when, and to whom.

    A short count is not refused — it is recorded, because the shop has to be
    able to say afterwards exactly which day was short and by how much — but
    it cannot be saved without a reason written down.
    """
    _name = "sukkar.driver.cashup"
    _description = "Driver Cash Hand-in"
    _order = "date desc, id desc"
    _rec_name = "display_name"

    driver_id = fields.Many2one(
        "res.users", string="Driver", required=True,
        domain="[('sukkar_is_driver', '=', True)]")
    date = fields.Date(string="Day", required=True, default=fields.Date.context_today)
    order_ids = fields.Many2many("sale.order", string="Orders he collected for")
    expected_amount = fields.Monetary(
        string="Should hand in", compute="_compute_amounts", store=True,
        currency_field="currency_id")
    counted_amount = fields.Monetary(
        string="Counted on the counter", currency_field="currency_id")
    difference = fields.Monetary(
        string="Difference", compute="_compute_amounts", store=True,
        currency_field="currency_id",
        help="Counted minus what the orders add up to. Negative is short.")
    note = fields.Text(string="Note")
    state = fields.Selection(
        [("draft", "Counting"), ("done", "Collected")],
        default="draft", required=True, index=True)
    collected_by = fields.Many2one("res.users", string="Taken in by", readonly=True)
    # The cash booked into the books at the hand-in, one payment per order
    # (owner, 10 Oct 2026: a cash order is not paid until the driver hands in).
    payment_ids = fields.Many2many(
        "account.payment", string="Cash Booked", readonly=True, copy=False)
    collected_at = fields.Datetime(string="Taken in at", readonly=True)
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, required=True)

    @api.depends("order_ids.sukkar_cash_due", "counted_amount", "state")
    def _compute_amounts(self):
        for rec in self:
            # Once collected the orders are paid and their cash due drops to
            # zero, so the figure is frozen at that moment instead.
            if rec.state == "done":
                rec.expected_amount = rec.expected_amount
            else:
                rec.expected_amount = sum(rec.order_ids.mapped("sukkar_cash_due"))
            rec.difference = rec.counted_amount - rec.expected_amount

    @api.depends("driver_id", "date")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "%s — %s" % (
                rec.driver_id.name or _("Driver"), rec.date or "")

    @api.onchange("driver_id", "date")
    def _onchange_driver_id(self):
        """Fill the list with everything that driver is still holding."""
        self.order_ids = self.driver_id and self._sukkar_outstanding(self.driver_id)

    @api.model
    def _sukkar_outstanding(self, driver):
        """The cash orders this driver has not handed the money in for.

        The amount itself is worked out on the fly and cannot be searched, so
        the search asks the two questions that can be — his orders, paid in
        cash, still unpaid — and the amount is checked afterwards.
        """
        orders = self.env["sale.order"].search([
            ("sukkar_driver_id", "=", driver.id),
            ("sukkar_payment_method", "in", ("cod", "cash")),
            ("state", "not in", ("draft", "cancel")),
        ])
        return orders.filtered(lambda o: o.sukkar_cash_due > 0)

    def action_collect(self):
        """Take the money in: every order on the list becomes paid, in cash."""
        for rec in self:
            if rec.state == "done":
                raise UserError(_("This hand-in has already been taken in."))
            if not rec.order_ids:
                raise UserError(_(
                    "There is nothing to collect: this driver is not holding "
                    "money for any order."))
            if rec.difference and not (rec.note or "").strip():
                raise UserError(_(
                    "The count is off by %(diff)s. Write what happened in the "
                    "note before taking it in — a short day has to be "
                    "explainable afterwards.",
                    diff=rec.currency_id.format(rec.difference)))
            dues = {order: order.sukkar_cash_due for order in rec.order_ids}
            expected = sum(dues.values())
            rec.order_ids.sudo().write({
                "sukkar_paid": True,
                "sukkar_payment_method": "cash",
            })
            payments = rec._sukkar_book_cash(dues)
            rec.write({
                "state": "done",
                "expected_amount": expected,
                "collected_by": self.env.user.id,
                "collected_at": fields.Datetime.now(),
                "payment_ids": [(6, 0, payments.ids)],
            })
        return True

    def _sukkar_cash_journal(self):
        """The till's cash journal — where counted cash lives."""
        self.ensure_one()
        method = self.env["pos.payment.method"].sudo().search([
            ("is_cash_count", "=", True),
            ("company_id", "=", self.company_id.id)], limit=1)
        return method.journal_id or self.env["account.journal"].sudo().search([
            ("type", "=", "cash"), ("company_id", "=", self.company_id.id)], limit=1)

    def _sukkar_book_cash(self, dues):
        """One customer payment per order, in cash, matched against what that
        customer owes. The customer paid the driver in full, so each order is
        booked at its amount; a short count stays on the hand-in with its
        written reason for the accountant."""
        self.ensure_one()
        journal = self._sukkar_cash_journal()
        Payment = self.env["account.payment"].sudo()
        payments = Payment
        if not journal:
            return payments
        for order, amount in dues.items():
            if amount <= 0:
                continue
            partner = order.partner_id.commercial_partner_id
            payment = Payment.create({
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": partner.id,
                "amount": amount,
                "currency_id": order.currency_id.id,
                "journal_id": journal.id,
                "date": self.date or fields.Date.context_today(self),
                "memo": _("Cash from driver %(driver)s · %(order)s",
                          driver=self.driver_id.name, order=order.name),
                "company_id": self.company_id.id,
            })
            payment.action_post()
            self._sukkar_match(payment, partner)
            payments |= payment
        return payments

    def _sukkar_match(self, payment, partner):
        """Settle what the customer owes, oldest first — the till's Cash on
        Delivery leaves it on the customer's account once the till is closed.
        Anything not yet there is matched later in the books."""
        lines = payment.move_id.line_ids.filtered(
            lambda l: l.account_id.account_type == "asset_receivable"
            and not l.reconciled)
        if not lines:
            return
        open_lines = self.env["account.move.line"].sudo().search([
            ("partner_id", "=", partner.id),
            ("account_id", "in", lines.account_id.ids),
            ("parent_state", "=", "posted"),
            ("reconciled", "=", False),
            ("balance", ">", 0),
        ], order="date, id")
        if open_lines:
            (lines | open_lines).reconcile()

    def action_reload_orders(self):
        self.ensure_one()
        self.order_ids = self._sukkar_outstanding(self.driver_id)
        return True


class ResUsersDriverCash(models.Model):
    _inherit = "res.users"

    sukkar_cash_held = fields.Monetary(
        string="Cash Held", compute="_compute_sukkar_cash_held",
        currency_field="sukkar_currency_id",
        help="What this driver has collected from customers and not yet "
             "handed in.")
    sukkar_cash_orders = fields.Integer(
        string="Orders", compute="_compute_sukkar_cash_held")
    sukkar_currency_id = fields.Many2one(
        "res.currency", compute="_compute_sukkar_cash_held")

    def _compute_sukkar_cash_held(self):
        Cashup = self.env["sukkar.driver.cashup"]
        for user in self:
            user.sukkar_currency_id = user.company_id.currency_id \
                or self.env.company.currency_id
            if not user.sukkar_is_driver:
                user.sukkar_cash_held = 0.0
                user.sukkar_cash_orders = 0
                continue
            orders = Cashup._sukkar_outstanding(user)
            user.sukkar_cash_held = sum(orders.mapped("sukkar_cash_due"))
            user.sukkar_cash_orders = len(orders)

    def action_sukkar_cashup(self):
        """Start the hand-in for this driver, already filled in."""
        self.ensure_one()
        orders = self.env["sukkar.driver.cashup"]._sukkar_outstanding(self)
        return {
            "type": "ir.actions.act_window",
            "name": _("Cash hand-in — %s", self.name),
            "res_model": "sukkar.driver.cashup",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_driver_id": self.id,
                "default_order_ids": [(6, 0, orders.ids)],
            },
        }
