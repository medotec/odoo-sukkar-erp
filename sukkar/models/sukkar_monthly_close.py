import calendar
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SukkarMonthlyClose(models.Model):
    """Month-end actuals: Revenue − COGS − Operating Expenses = Net Profit.
    Figures are pulled with the Compute button, then editable for adjustments."""
    _name = "sukkar.monthly.close"
    _description = "Monthly Profitability (month-end actuals)"
    _order = "date_from desc"

    def _default_from(self):
        t = fields.Date.context_today(self)
        return t.replace(day=1)

    def _default_to(self):
        t = fields.Date.context_today(self)
        return t.replace(day=calendar.monthrange(t.year, t.month)[1])

    name = fields.Char(default="New", copy=False)
    date_from = fields.Date(required=True, default=_default_from)
    date_to = fields.Date(required=True, default=_default_to)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    state = fields.Selection([("draft", "Draft"), ("closed", "Closed")], default="draft")

    # pulled by Compute, then editable
    revenue = fields.Monetary(currency_field="currency_id")
    cogs = fields.Monetary(string="Cost of Goods Sold", currency_field="currency_id")
    expenses_total = fields.Monetary(string="Operating Expenses", currency_field="currency_id")
    other_income = fields.Monetary(currency_field="currency_id")
    other_costs = fields.Monetary(currency_field="currency_id")

    # derived live
    gross_profit = fields.Monetary(compute="_compute_pl", store=True, currency_field="currency_id")
    gross_margin = fields.Float(string="Gross Margin %", compute="_compute_pl", store=True)
    net_profit = fields.Monetary(compute="_compute_pl", store=True, currency_field="currency_id")
    net_margin = fields.Float(string="Net Margin %", compute="_compute_pl", store=True)

    # ratio reference (from the BOM Fixed Cost tab)
    var_raw_pct = fields.Float(string="Raw Materials % of revenue", default=33.0)
    var_ads_pct = fields.Float(string="Ads % of revenue", default=20.0)
    est_variable_cost = fields.Monetary(
        string="Est. Variable Cost (ratio)", compute="_compute_pl",
        store=True, currency_field="currency_id")

    note = fields.Text()

    # --- Books vs the shop floor --------------------------------------------
    # The headline figures above come from the posted accounts. These three are
    # the same month counted from orders, the till and the expense list. They
    # are not a second profit figure — they are the check that the books have
    # not missed anything, and a gap between them is a question to answer.
    from_books = fields.Boolean(string="Figures From the Books", readonly=True)
    lock_on_close = fields.Boolean(
        string="Lock the Month on Close", default=True,
        help="Closing the month moves the company lock date to its last day, so "
             "nobody can change an entry in it afterwards.")
    ops_revenue = fields.Monetary(
        string="Revenue — from orders & till", currency_field="currency_id",
        readonly=True)
    ops_cogs = fields.Monetary(
        string="Cost of Goods Sold — from recipes", currency_field="currency_id",
        readonly=True)
    ops_expenses_total = fields.Monetary(
        string="Running Costs — from the expense list",
        currency_field="currency_id", readonly=True)
    variance = fields.Monetary(
        string="Gap to Explain", compute="_compute_variance",
        currency_field="currency_id",
        help="Profit per the books minus profit per the shop floor. Anything "
             "other than zero means something was recorded in one place and "
             "not the other.")

    @api.depends("revenue", "cogs", "expenses_total", "from_books",
                 "ops_revenue", "ops_cogs", "ops_expenses_total")
    def _compute_variance(self):
        for r in self:
            books = r.revenue - r.cogs - r.expenses_total
            floor = r.ops_revenue - r.ops_cogs - r.ops_expenses_total
            r.variance = (books - floor) if r.from_books else 0.0

    @api.depends("revenue", "cogs", "expenses_total", "other_income", "other_costs",
                 "var_raw_pct", "var_ads_pct")
    def _compute_pl(self):
        for r in self:
            r.gross_profit = r.revenue - r.cogs
            r.net_profit = r.gross_profit + r.other_income - r.expenses_total - r.other_costs
            r.gross_margin = (r.gross_profit / r.revenue * 100) if r.revenue else 0.0
            r.net_margin = (r.net_profit / r.revenue * 100) if r.revenue else 0.0
            r.est_variable_cost = r.revenue * (r.var_raw_pct + r.var_ads_pct) / 100.0

    def _ledger_totals(self):
        """What the posted accounts say for the period — the official figures."""
        self.ensure_one()
        Line = self.env["account.move.line"]
        base = [("parent_state", "=", "posted"),
                ("company_id", "=", self.company_id.id),
                ("date", ">=", self.date_from), ("date", "<=", self.date_to)]

        def total(account_types):
            groups = Line._read_group(
                base + [("account_id.account_type", "in", account_types)],
                aggregates=["balance:sum"])
            return groups[0][0] if groups else 0.0

        return {
            "revenue": -total(["income"]),
            "other_income": -total(["income_other"]),
            "cogs": total(["expense_direct_cost"]),
            "expenses_total": total(["expense", "expense_depreciation"]),
        }

    def action_compute(self):
        """Pull actuals from posted invoices + POS sessions + expenses (+ payroll)."""
        Move = self.env["account.move"]
        Exp = self.env["sukkar.expense"]
        for rec in self:
            moves = Move.search([
                ("move_type", "in", ["out_invoice", "out_refund"]),
                ("state", "=", "posted"),
                ("invoice_date", ">=", rec.date_from),
                ("invoice_date", "<=", rec.date_to)])
            revenue = cogs = 0.0
            for mv in moves:
                sign = 1.0 if mv.move_type == "out_invoice" else -1.0
                revenue += sign * mv.amount_untaxed
                for ln in mv.invoice_line_ids.filtered(lambda l: l.product_id):
                    cogs += sign * (ln.product_id.standard_price * ln.quantity)
            # Register sales. The shop is a counter business, so a P&L that only
            # reads invoices misses most of the till. Invoiced POS orders are
            # skipped here — their invoice is already in the loop above.
            # Period boundaries are KUWAIT days, date_order is stored UTC:
            # convert the boundaries, never .date() the datetimes (the timezone
            # bug this project has already paid for twice).
            if "pos.order" in self.env:
                Slot = self.env["sukkar.delivery.slot"]
                start_utc = Slot._tz_midnight_utc(rec.date_from)
                end_utc = Slot._tz_midnight_utc(rec.date_to + timedelta(days=1))
                pos_orders = self.env["pos.order"].search([
                    ("state", "in", ["paid", "done"]),
                    ("date_order", ">=", start_utc),
                    ("date_order", "<", end_utc)])
                for po in pos_orders:
                    revenue += po.amount_total - po.amount_tax
                    for ln in po.lines.filtered(lambda l: l.product_id):
                        cogs += ln.product_id.standard_price * ln.qty
            # Salaries come from ONE system: Payroll. When the period has
            # completed payslips, their GROSS is the salary cost and every
            # salary-category expense is EXCLUDED — otherwise the same wages
            # would count twice (once typed as an expense, once posted by the
            # payslip). Months before payroll went live have no payslips, so
            # the old expense-based salary figure still stands there.
            slips = self.env["hr.payslip"].search([
                ("state", "=", "done"),
                ("date_from", ">=", rec.date_from),
                ("date_from", "<=", rec.date_to)])
            payroll_cost = sum(
                line.total for line in slips.mapped("line_ids")
                if line.code == "GROSS")
            salary_excl = ([("category_id.is_salary", "=", False)]
                           if slips else [])
            # One-off expenses in the period. This includes the copies the cron
            # makes of each fixed cost on the 1st: those are real, dated money.
            company_only = [("company_id", "=", rec.company_id.id)]
            oneoff = sum(Exp.search([
                ("is_recurring", "=", False), ("state", "in", ["confirmed", "paid"]),
                ("date", ">=", rec.date_from), ("date", "<=", rec.date_to)]
                + company_only + salary_excl).mapped("amount"))
            # The recurring run-rate stands in for a fixed cost only until the
            # month's real copy is confirmed. Counting both charged the shop its
            # whole fixed-cost base twice, and counting it with no date at all
            # charged a month the shop did not yet exist.
            templates = Exp.search(
                [("is_recurring", "=", True),
                 ("state", "in", ["confirmed", "paid"])] + company_only + salary_excl)
            already_real = set(Exp.search([
                ("source_id", "in", templates.ids),
                ("state", "in", ["confirmed", "paid"]),
                ("date", ">=", rec.date_from),
                ("date", "<=", rec.date_to)]).mapped("source_id").ids)
            run = 0.0
            for e in templates:
                if e.id in already_real or (e.date and e.date > rec.date_to):
                    continue
                run += {"monthly": e.amount, "quarterly": e.amount / 3.0,
                        "yearly": e.amount / 12.0}.get(e.recurrence, e.amount)
            rec.ops_revenue = revenue
            rec.ops_cogs = cogs
            rec.ops_expenses_total = oneoff + run + payroll_cost
            # The books win when there are books. Before 1 Oct 2026 nothing was
            # posted, so those months keep the operational estimate — otherwise
            # every historical month would read zero.
            ledger = rec._ledger_totals()
            if any(abs(v) > 0.0005 for v in ledger.values()):
                rec.from_books = True
                rec.revenue = ledger["revenue"]
                rec.cogs = ledger["cogs"]
                rec.expenses_total = ledger["expenses_total"]
                rec.other_income = ledger["other_income"]
            else:
                rec.from_books = False
                rec.revenue = revenue
                rec.cogs = cogs
                rec.expenses_total = oneoff + run + payroll_cost
            if rec.name in (False, "New"):
                rec.name = rec.date_from.strftime("%B %Y")

    def action_close(self):
        """Close the month and lock it, so the figures cannot move afterwards.

        A month that anyone can still edit is a month that is never final: the
        accountant signs off a profit figure and the reports quietly change
        underneath him. Closing here pushes the company's lock date to the last
        day of the month — nothing before it can be changed again.

        Two things it may not do. It may not lock a day that has not happened
        yet: on the 19th the month still has eleven trading days in it, and a
        lock dated the 30th stops the till closing and every invoice posting
        until somebody finds a hidden accounting setting. And it may not be done
        by anyone who happens to open the screen: a company lock date is the
        accountant's decision, not a button on a profit report.
        """
        today = fields.Date.context_today(self)
        locking = self.filtered("lock_on_close")
        if locking and not self.env.user.has_group("account.group_account_manager"):
            raise UserError(_(
                "Locking a month is the accountant's decision. Ask whoever keeps "
                "the books to close it, or untick \"Lock the Month on Close\"."))
        for rec in locking:
            if rec.date_to >= today:
                raise UserError(_(
                    "%(period)s has not finished yet. Locking it now would stop "
                    "the till closing and every invoice posting for the rest of "
                    "the month. Close it on or after %(day)s, or untick \"Lock "
                    "the Month on Close\".",
                    period=rec.name if rec.name not in (False, "New")
                    else rec.date_from.strftime("%B %Y"),
                    day=(rec.date_to + timedelta(days=1)).strftime("%d %B %Y")))
        self.write({"state": "closed"})
        for rec in locking:
            company = rec.company_id.sudo()
            current = company.fiscalyear_lock_date
            if not current or current < rec.date_to:
                company.write({"fiscalyear_lock_date": rec.date_to})
        return True

    def action_draft(self):
        """Reopening does NOT unlock the month — that is the accountant's call
        (Accounting → Configuration → Lock Dates), on purpose."""
        self.write({"state": "draft"})
