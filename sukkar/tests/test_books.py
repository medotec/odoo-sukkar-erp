from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarBooks(TransactionCase):
    """Costs the accountant can actually see.

    Until September 2026 the Expenses screen kept its own list and never touched
    the ledger, so rent, advertising and packaging were invisible to the books
    and any profit figure taken from the accounts was far too high.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.rent = cls.env["sukkar.expense.category"].search(
            [("name", "=", "Rent")], limit=1)
        cls.salary_cat = cls.env["sukkar.expense.category"].search(
            [("is_salary", "=", True)], limit=1)

    def _expense(self, **vals):
        base = {
            "name": "Shop rent",
            "category_id": self.rent.id,
            "date": date(2026, 10, 5),
            "basic_amount": 440.0,
            "payment_method": "bank_transfer",
        }
        base.update(vals)
        return self.env["sukkar.expense"].create(base)

    def test_setup_filled_the_books_settings(self):
        """A fresh database must arrive configured, not merely installed."""
        self.assertTrue(self.company.sukkar_expense_journal_id)
        self.assertTrue(self.company.sukkar_expense_cash_account_id)
        self.assertTrue(self.company.sukkar_expense_bank_account_id)
        self.assertTrue(self.company.sukkar_expense_unpaid_account_id)
        self.assertTrue(self.rent.account_id,
                        "every expense category needs an account behind it")

    def test_confirming_an_expense_posts_a_balanced_entry(self):
        expense = self._expense()
        expense.action_confirm()
        move = expense.move_id
        self.assertTrue(move, "a confirmed expense must reach the books")
        self.assertEqual(move.state, "posted")
        self.assertAlmostEqual(sum(move.line_ids.mapped("debit")),
                               sum(move.line_ids.mapped("credit")), places=3)
        debit = move.line_ids.filtered(lambda l: l.debit)
        credit = move.line_ids.filtered(lambda l: l.credit)
        self.assertEqual(debit.account_id, self.rent.account_id)
        self.assertEqual(credit.account_id,
                         self.company.sukkar_expense_bank_account_id)
        self.assertAlmostEqual(debit.debit, 440.0, places=3)

    def test_cash_and_unpaid_credit_different_accounts(self):
        cash = self._expense(payment_method="cash")
        cash.action_confirm()
        self.assertEqual(
            cash.move_id.line_ids.filtered(lambda l: l.credit).account_id,
            self.company.sukkar_expense_cash_account_id)
        unpaid = self._expense(payment_method="other")
        unpaid.action_confirm()
        self.assertEqual(
            unpaid.move_id.line_ids.filtered(lambda l: l.credit).account_id,
            self.company.sukkar_expense_unpaid_account_id,
            "a cost recorded before it is paid must show as owed")

    def test_a_recurring_cost_is_a_plan_not_a_payment(self):
        template = self._expense(is_recurring=True, recurrence="monthly")
        template.action_confirm()
        self.assertFalse(template.move_id,
                         "a monthly fixed cost is a plan — posting it would "
                         "charge one month's rent to the whole year")

    def test_salaries_are_never_written_from_the_expense_screen(self):
        if not self.salary_cat:
            self.skipTest("no salary category in this database")
        salary = self._expense(category_id=self.salary_cat.id, name="Wages")
        salary.action_confirm()
        self.assertFalse(salary.move_id,
                         "payroll posts wages — two systems writing the same "
                         "wages is how people get paid twice on paper")

    def test_resetting_to_draft_removes_the_entry(self):
        expense = self._expense()
        expense.action_confirm()
        expense.action_set_draft()
        self.assertFalse(expense.move_id,
                         "an expense pulled back to draft must leave nothing "
                         "behind in the accounts")

    def test_monthly_costs_are_copied_into_this_month_as_drafts(self):
        template = self._expense(is_recurring=True, recurrence="monthly",
                                 name="Monthly rent")
        template.action_confirm()
        created = self.env["sukkar.expense"]._generate_recurring_for(
            self.company, date(2026, 11, 1))
        created = created.filtered(lambda e: e.source_id == template)
        self.assertEqual(len(created), 1)
        self.assertEqual(created.state, "draft",
                         "nothing reaches the books unattended")
        self.assertFalse(created.is_recurring)
        again = self.env["sukkar.expense"]._generate_recurring_for(
            self.company, date(2026, 11, 1))
        self.assertFalse(again.filtered(lambda e: e.source_id == template),
                         "running twice in a month must not duplicate the cost")

    def test_monthly_pl_reads_the_posted_accounts(self):
        expense = self._expense(date=date(2026, 10, 5))
        expense.action_confirm()
        close = self.env["sukkar.monthly.close"].create({
            "date_from": date(2026, 10, 1), "date_to": date(2026, 10, 31)})
        close.action_compute()
        self.assertTrue(close.from_books)
        self.assertGreaterEqual(close.expenses_total, 440.0,
                                "the rent just posted must be in the figure")

    def test_every_expense_category_can_reach_the_books(self):
        """A category with no account throws the user out when he confirms.

        The Insurance category was added by hand and was mapped by its Arabic
        screen name, which matches nothing in English — so it silently kept no
        account and every insurance bill was refused.
        """
        blank = self.env["sukkar.expense.category"].search(
            [("account_id", "=", False)])
        self.assertFalse(
            blank, "these categories have no account behind them, so "
                   "confirming an expense in them fails: %s"
                   % ", ".join(blank.mapped("name")))

    def test_an_insurance_expense_reaches_the_books(self):
        category = self.env["sukkar.expense.category"].search(
            [("name", "=", "Insurance")], limit=1)
        if not category:
            self.skipTest("no Insurance category in this database")
        expense = self._expense(category_id=category.id, name="Shop insurance")
        expense.action_confirm()
        self.assertTrue(expense.move_id)
        self.assertEqual(
            expense.move_id.line_ids.filtered(lambda l: l.debit).account_id,
            category.account_id)

    def test_the_balance_sheet_balances(self):
        """Assets must equal liabilities plus equity, or it is not a balance
        sheet. Drawn for a single month it never does: the assets are running
        totals while the earnings line is only that month's profit."""
        instance = self.env["mis.report.instance"].search(
            [("name", "=", "Balance Sheet — today")], limit=1)
        self.assertTrue(instance, "the accountant's Balance Sheet is missing")
        rows = {}
        for row in instance.compute()["body"]:
            cells = row.get("cells") or []
            rows[row.get("label")] = cells[0].get("val") if cells else None
        assets = rows.get("Assets") or 0.0
        claims = rows.get("Liability + Equity") or 0.0
        self.assertAlmostEqual(
            assets, claims, places=2,
            msg="assets %s do not equal liabilities + equity %s — the "
                "accumulated profit or loss is missing from the statement"
                % (assets, claims))

    def test_the_accountant_can_see_the_accounting_menus(self):
        """Odoo Community hides the chart of accounts, journal entries,
        reconciliation, statement import and the lock dates behind one group."""
        manager = self.env.ref("account.group_account_manager")
        for user in manager.users:
            self.assertTrue(
                user.has_group("account.group_account_user"),
                "%s administers the accounts but cannot see the Accounting "
                "menu" % user.login)

    def test_closing_a_month_locks_it(self):
        company = self.company
        before = company.fiscalyear_lock_date
        # A month that has finished: locking one that has not would stop the
        # till closing for the rest of it.
        first_of_this_month = fields.Date.context_today(self.env.user).replace(day=1)
        last_month_end = first_of_this_month - timedelta(days=1)
        close = self.env["sukkar.monthly.close"].create({
            "date_from": last_month_end.replace(day=1), "date_to": last_month_end})
        close.action_close()
        self.assertEqual(company.fiscalyear_lock_date, last_month_end,
                         "a closed month must stop being editable")
        company.sudo().fiscalyear_lock_date = before

    def test_a_month_that_has_not_finished_cannot_be_locked(self):
        """One click on a screen called 'Monthly P&L', on any day before month
        end, used to stop the shop closing the till and issuing any invoice for
        the rest of the month."""
        company = self.company
        before = company.fiscalyear_lock_date
        today = fields.Date.context_today(self.env.user)
        close = self.env["sukkar.monthly.close"].create({
            "date_from": today.replace(day=1),
            "date_to": today + timedelta(days=5)})
        with self.assertRaises(UserError):
            close.action_close()
        self.assertEqual(close.state, "draft", "nothing may be closed either")
        self.assertEqual(company.fiscalyear_lock_date, before,
                         "the company lock date must not have moved")

    def test_only_an_accountant_may_lock_a_month(self):
        """Setting a company lock date is the accountant's decision, not a
        button anyone who can open the screen may press."""
        company = self.company
        before = company.fiscalyear_lock_date
        clerk = self.env["res.users"].create({
            "name": "Counter Clerk", "login": "sukkar.counter.clerk",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        first_of_this_month = fields.Date.context_today(self.env.user).replace(day=1)
        last_month_end = first_of_this_month - timedelta(days=1)
        close = self.env["sukkar.monthly.close"].with_user(clerk).create({
            "date_from": last_month_end.replace(day=1), "date_to": last_month_end})
        with self.assertRaises(UserError):
            close.action_close()
        self.assertEqual(company.fiscalyear_lock_date, before)


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarFixedCosts(TransactionCase):
    """The monthly fixed costs: how they are created, and how they are counted.

    Counting a fixed cost twice — once as the run-rate of the template, once as
    the real copy the cron made — overstated running costs by the shop's whole
    fixed-cost base, every month.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Expense = cls.env["sukkar.expense"]
        cls.category = cls.env["sukkar.expense.category"].search(
            [("name", "=", "Rent")], limit=1)

    def _template(self, amount=450.0, start=date(2026, 9, 1)):
        template = self.Expense.create({
            "name": "Monthly rent (test)", "category_id": self.category.id,
            "date": start, "basic_amount": amount, "is_recurring": True,
            "recurrence": "monthly", "payment_method": "bank_transfer"})
        template.state = "confirmed"
        return template

    def _close(self, start, end):
        close = self.env["sukkar.monthly.close"].create({
            "date_from": start, "date_to": end})
        close.action_compute()
        return close

    def test_a_fixed_cost_is_counted_once_a_month(self):
        october = (date(2026, 10, 1), date(2026, 10, 31))
        baseline = self._close(*october).ops_expenses_total
        template = self._template(amount=450.0)
        with_template = self._close(*october).ops_expenses_total
        self.assertAlmostEqual(with_template - baseline, 450.0, places=2,
                               msg="the run-rate stands in until the real copy exists")
        # The cron makes October's copy and somebody confirms it.
        copy = self.Expense.create({
            "name": template.name, "category_id": self.category.id,
            "date": date(2026, 10, 1), "basic_amount": 450.0,
            "is_recurring": False, "source_id": template.id,
            "payment_method": "bank_transfer"})
        copy.state = "confirmed"
        with_copy = self._close(*october).ops_expenses_total
        self.assertAlmostEqual(with_copy - baseline, 450.0, places=2,
                               msg="one rent a month, not the template plus its copy")

    def test_a_month_before_the_cost_existed_is_not_charged_for_it(self):
        self._template(amount=450.0, start=date(2026, 9, 1))
        close = self._close(date(2020, 1, 1), date(2020, 1, 31))
        self.assertAlmostEqual(close.ops_expenses_total, 0.0, places=2,
                               msg="the shop did not exist in 2020")

    def test_a_draft_copy_does_not_yet_replace_the_run_rate(self):
        """The cron creates DRAFTS. Until somebody confirms one, the fixed cost
        is still expected, so the run-rate has to stand."""
        october = (date(2026, 10, 1), date(2026, 10, 31))
        baseline = self._close(*october).ops_expenses_total
        template = self._template(amount=450.0)
        self.Expense.create({
            "name": template.name, "category_id": self.category.id,
            "date": date(2026, 10, 1), "basic_amount": 450.0,
            "is_recurring": False, "source_id": template.id,
            "payment_method": "bank_transfer"})
        close = self._close(*october)
        self.assertAlmostEqual(close.ops_expenses_total - baseline, 450.0, places=2)

    def test_a_recurring_template_carries_its_category_account(self):
        """The copies inherit it. A blank here was twelve costs a year that
        refused to confirm, pointing at a screen already filled in."""
        template = self._template()
        self.assertEqual(template.account_id, self.category.account_id)
        generated = template.copy({"is_recurring": False, "source_id": template.id})
        self.assertEqual(generated.account_id, self.category.account_id)

    def test_a_blank_account_is_filled_in_when_the_category_gets_one(self):
        category = self.env["sukkar.expense.category"].create(
            {"name": "Grill Test Category"})
        expense = self.Expense.create({
            "name": "Typed before the accounts existed",
            "category_id": category.id, "date": date(2026, 10, 1),
            "basic_amount": 10.0, "payment_method": "cash"})
        self.assertFalse(expense.account_id)
        category.account_id = self.env["sukkar.expense.category"].search(
            [("name", "=", "Rent")], limit=1).account_id
        self.assertEqual(expense.account_id, category.account_id,
                         "the account follows the category, whenever it is set")

    def test_the_cron_still_catches_up_if_the_server_was_off(self):
        """Odoo runs in Docker on a Mac. If the machine is asleep on the 1st the
        catch-up run lands on the 2nd, and a whole month of fixed costs used to
        be skipped silently."""
        template = self._template(amount=450.0)
        created = self.Expense._generate_recurring_for(
            self.company, date(2026, 11, 2))
        self.assertIn(template, created.mapped("source_id"))
        self.assertEqual(created.filtered(lambda e: e.source_id == template).date,
                         date(2026, 11, 1))
        again = self.Expense._generate_recurring_for(
            self.company, date(2026, 11, 3))
        self.assertNotIn(template, again.mapped("source_id"),
                         "and still only once a month")
