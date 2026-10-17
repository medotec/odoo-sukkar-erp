from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SukkarExpenseCategory(models.Model):
    _name = "sukkar.expense.category"
    _description = "Expense Category"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    is_salary = fields.Boolean(
        string="Salary / Overtime",
        help="Expenses in this category show the overtime calculator.")
    active = fields.Boolean(default=True)
    color = fields.Integer()
    account_id = fields.Many2one(
        "account.account", string="Expense Account",
        domain="[('account_type', 'in', ['expense', 'expense_direct_cost', "
               "'expense_depreciation'])]",
        help="Where this kind of cost lands in the accounts. Confirming an "
             "expense in this category writes it here, so it appears in the "
             "Profit & Loss.")


class SukkarExpense(models.Model):
    _name = "sukkar.expense"
    _description = "Expense"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    reference = fields.Char(default="New", copy=False, readonly=True, index=True)
    name = fields.Char(string="Description", required=True, tracking=True)
    date = fields.Date(default=fields.Date.context_today, required=True, tracking=True)
    category_id = fields.Many2one(
        "sukkar.expense.category", string="Category", required=True, tracking=True)
    is_salary = fields.Boolean(related="category_id.is_salary")

    partner_id = fields.Many2one("res.partner", string="Payee / Vendor")
    employee_name = fields.Char(string="Employee", help="For salary expenses.")

    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id, required=True)

    # --- Amount & overtime calculator --------------------------------------
    basic_amount = fields.Monetary(
        string="Amount / Basic Pay", currency_field="currency_id",
        help="The expense amount; for salaries, the basic pay before overtime.")
    ot_hours = fields.Float(string="Overtime Hours")
    ot_hourly_rate = fields.Monetary(
        string="Hourly Rate", currency_field="currency_id",
        help="Normal hourly wage. Tip: monthly salary ÷ ~208 hours (26 days × 8h).")
    ot_multiplier = fields.Float(
        string="OT Multiplier", default=1.25,
        help="Kuwait: 1.25× on normal days, 1.5× on weekly rest days / holidays.")
    ot_amount = fields.Monetary(
        string="Overtime Pay", currency_field="currency_id",
        compute="_compute_ot_amount", store=True)
    amount = fields.Monetary(
        string="Total", currency_field="currency_id",
        compute="_compute_amount", store=True, tracking=True)

    payment_method = fields.Selection(
        [("cash", "Cash"), ("knet", "KNET"), ("bank_transfer", "Bank Transfer"),
         ("link", "Payment Link"), ("other", "Other")],
        string="Payment Method", default="bank_transfer")
    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed"), ("paid", "Paid")],
        default="draft", tracking=True)

    is_recurring = fields.Boolean(string="Recurring")
    recurrence = fields.Selection(
        [("monthly", "Monthly"), ("quarterly", "Quarterly"), ("yearly", "Yearly")],
        string="Every", default="monthly")

    receipt = fields.Binary(string="Receipt", attachment=True)
    receipt_filename = fields.Char()
    note = fields.Text()

    # --- The bridge into the accounts ---------------------------------------
    source_id = fields.Many2one(
        "sukkar.expense", string="From Recurring Cost", copy=False, index=True,
        help="The monthly fixed cost this expense was created from.")
    account_id = fields.Many2one(
        "account.account", string="Expense Account",
        compute="_compute_account_id", store=True, readonly=False,
        domain="[('account_type', 'in', ['expense', 'expense_direct_cost', "
               "'expense_depreciation'])]")
    move_id = fields.Many2one(
        "account.move", string="Journal Entry", copy=False, readonly=True)
    move_state = fields.Selection(related="move_id.state", string="Entry Status")
    posts_to_books = fields.Boolean(compute="_compute_posts_to_books")

    @api.depends("category_id", "category_id.account_id")
    def _compute_account_id(self):
        # Depending on the category alone left every expense typed before the
        # accounting layer existed with a blank account, and the monthly copies
        # of the fixed costs inherited the blank: twelve costs a year that
        # refuse to confirm, pointing at a screen already filled in.
        for exp in self:
            exp.account_id = exp.category_id.account_id or exp.account_id

    @api.depends("is_recurring", "category_id.is_salary", "company_id")
    def _compute_posts_to_books(self):
        for exp in self:
            # A recurring row is a monthly plan, not money that moved — the
            # cron copies it into a real expense each month and THAT one posts.
            # Salaries have one engine, Payroll, which posts its own entries;
            # a wage typed here would be the same money counted twice.
            exp.posts_to_books = bool(
                exp.company_id.sukkar_post_expenses
                and not exp.is_recurring
                and not exp.category_id.is_salary)

    def _money_account(self):
        self.ensure_one()
        company = self.company_id
        if self.payment_method == "cash":
            return company.sukkar_expense_cash_account_id
        if self.payment_method in ("knet", "bank_transfer", "link"):
            return company.sukkar_expense_bank_account_id
        return company.sukkar_expense_unpaid_account_id

    def _create_move(self):
        for exp in self:
            if exp.move_id or not exp.posts_to_books:
                continue
            journal = exp.company_id.sukkar_expense_journal_id
            credit = exp._money_account()
            if not journal or not exp.account_id or not credit:
                raise UserError(_(
                    "This expense cannot reach the books yet.\n\n"
                    "Missing: %(missing)s.\n\n"
                    "An administrator sets these in Settings → Sukkar → Books, "
                    "and the account per category in Expenses → Configuration → "
                    "Categories.",
                    missing=", ".join(filter(None, [
                        not journal and _("the expense journal") or "",
                        not exp.account_id and _("the account for this category") or "",
                        not credit and _("the money-out account") or "",
                    ]))))
            move = self.env["account.move"].create({
                "move_type": "entry",
                "journal_id": journal.id,
                "date": exp.date,
                "ref": "%s — %s" % (exp.reference, exp.name),
                "company_id": exp.company_id.id,
                "line_ids": [
                    (0, 0, {"account_id": exp.account_id.id,
                            "partner_id": exp.partner_id.id or False,
                            "name": exp.name,
                            "debit": exp.amount, "credit": 0.0}),
                    (0, 0, {"account_id": credit.id,
                            "partner_id": exp.partner_id.id or False,
                            "name": exp.name,
                            "debit": 0.0, "credit": exp.amount}),
                ],
            })
            move.action_post()
            exp.move_id = move

    @api.depends("ot_hours", "ot_hourly_rate", "ot_multiplier")
    def _compute_ot_amount(self):
        for exp in self:
            exp.ot_amount = round(
                (exp.ot_hours or 0.0) * (exp.ot_hourly_rate or 0.0)
                * (exp.ot_multiplier or 0.0), 3)

    @api.depends("basic_amount", "ot_amount")
    def _compute_amount(self):
        for exp in self:
            exp.amount = (exp.basic_amount or 0.0) + (exp.ot_amount or 0.0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("reference", "New") == "New":
                vals["reference"] = self.env["ir.sequence"].next_by_code(
                    "sukkar.expense") or "New"
        return super().create(vals_list)

    def action_confirm(self):
        self.write({"state": "confirmed"})
        self._create_move()

    def action_mark_paid(self):
        self.write({"state": "paid"})

    def action_set_draft(self):
        for exp in self.filtered("move_id"):
            move = exp.move_id
            exp.move_id = False
            if move.state == "posted":
                move.button_draft()
            move.unlink()
        self.write({"state": "draft"})

    def action_open_move(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
        }

    @api.model
    def _cron_generate_recurring(self):
        """Copy each monthly fixed cost into this month's DRAFT expense.

        Draft on purpose: a cost that reaches the books unattended is a cost
        nobody checked. Somebody opens the list, confirms what really happened,
        and only then does it hit the accounts.
        """
        today = fields.Date.context_today(self)
        for company in self.env["res.company"].search([]):
            if not company.sukkar_generate_recurring:
                continue
            # On or after the day, not ON it. Odoo runs in Docker on a Mac: if
            # the machine is asleep on the 1st the catch-up run lands on the 2nd,
            # and a test for the exact day skipped the whole month's fixed costs
            # silently. The duplicate guard below is what keeps this to once a
            # month. Clamped to 28 so February is never skipped either.
            day = min(max(company.sukkar_generate_recurring_day or 1, 1), 28)
            if today.day < day:
                continue
            self.with_company(company)._generate_recurring_for(company, today)

    @api.model
    def _generate_recurring_for(self, company, today):
        month_start = today.replace(day=1)
        templates = self.search([
            ("is_recurring", "=", True), ("company_id", "=", company.id),
            ("state", "in", ["confirmed", "paid"]),
        ])
        created = self.browse()
        for template in templates:
            months = {"monthly": 1, "quarterly": 3, "yearly": 12}.get(
                template.recurrence, 1)
            if months > 1:
                since = relativedelta(months=months)
                anchor = template.date
                while anchor + since <= month_start:
                    anchor += since
                if anchor.replace(day=1) != month_start:
                    continue
            if self.search_count([
                    ("source_id", "=", template.id),
                    ("date", ">=", month_start),
                    ("date", "<=", today + relativedelta(day=31))]):
                continue
            created |= template.copy({
                "date": month_start,
                "is_recurring": False,
                "recurrence": False,
                "source_id": template.id,
                "state": "draft",
                "move_id": False,
            })
        return created
