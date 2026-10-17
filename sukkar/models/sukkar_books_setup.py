from odoo import api, models

# (code, name, account_type, reconcile)
NEW_ACCOUNTS = [
    ("152000", "Accumulated Depreciation — Equipment", "asset_fixed", False),
    ("153000", "Intangible Assets — Website & Software", "asset_fixed", False),
    ("154000", "Accumulated Amortisation — Intangibles", "asset_fixed", False),
    ("235000", "End-of-Service Indemnity Provision", "liability_non_current", False),
    ("303000", "Owner Drawings", "equity", False),
    ("309000", "Opening Balance Suspense — TO BE CLASSIFIED", "equity", False),
    ("613000", "Packaging & Supplies", "expense", False),
    ("614000", "Delivery & Logistics", "expense", False),
    ("615000", "Advertising & Marketing", "expense", False),
    ("616000", "Utilities", "expense", False),
    ("617000", "Government, Visa & Licence Fees", "expense", False),
    ("618000", "Software & Subscriptions", "expense", False),
    ("619000", "Professional Services", "expense", False),
    ("621000", "Repairs & Maintenance", "expense", False),
    ("622000", "Insurance", "expense", False),
    ("623000", "Staff Travel & Tickets", "expense", False),
    ("624000", "Depreciation & Amortisation", "expense", False),
    ("625000", "End-of-Service Indemnity Expense", "expense", False),
]

# Expense category (by the name shipped in sukkar_expense_category_data.xml)
# -> the account its spending lands in.
CATEGORY_ACCOUNTS = {
    "Salaries": "6300",
    "Insurance": "622000",
    "Licenses & Permits": "617000",
    "Advertising & Ad Spend": "615000",
    "Rent": "612000",
    "Utilities": "616000",
    "Packaging & Supplies": "613000",
    "Delivery & Logistics": "614000",
    "Software & Subscriptions": "618000",
    "Bank & Payment Fees": "620000",
    "Equipment & Maintenance": "621000",
    "Government & Visa Fees": "617000",
    "End-of-Service Indemnity": "625000",
    "Influencer & Gifting Marketing": "615000",
    "Professional Services": "619000",
    "Miscellaneous": "600000",
}

# Category names are translatable, so a category is looked up in English no
# matter which language the pass runs in — the Arabic screen name would
# otherwise match nothing and the category would silently keep no account.
CATEGORY_LOOKUP_LANG = "en_US"

# name, years, asset account, accumulated account
ASSET_PROFILES = [
    ("Equipment — 5 years", 5, "151000", "152000"),
    ("Small Equipment & Electronics — 3 years", 3, "151000", "152000"),
    ("Website & Software — 3 years", 3, "153000", "154000"),
]

# The Profit & Loss the shop actually reads: sales, what the cakes cost, then
# the running costs one line at a time. sequence, code, label, expression, bold.
#
# No square bracket may appear INSIDE a balp[...] selector: mis_builder matches
# the selector with a non-greedy regex, so ('code', 'in', ['a', 'b']) is cut at
# the first closing bracket and the expression stops parsing. Write one balp per
# account and add them up instead.
PL_KPIS = [
    (10, "sales", "Sales", "-balp[('account_type', '=', 'income')]", True),
    (20, "cogs", "Cost of Goods Sold",
     "balp[('account_type', '=', 'expense_direct_cost')]", False),
    (30, "gross_profit", "Gross Profit", "sales - cogs", True),
    (40, "exp_salaries", "Salaries & Overtime",
     "balp[('code', '=', '6300')] + balp[('code', '=', '630000')]", False),
    (50, "exp_rent", "Rent", "balp[('code', '=', '612000')]", False),
    (60, "exp_marketing", "Advertising & Marketing",
     "balp[('code', '=', '615000')] + balp[('code', '=', '962000')]", False),
    (70, "exp_packaging", "Packaging & Supplies",
     "balp[('code', '=', '613000')]", False),
    (80, "exp_delivery", "Delivery & Logistics",
     "balp[('code', '=', '614000')]", False),
    (90, "exp_utilities", "Utilities", "balp[('code', '=', '616000')]", False),
    (100, "exp_gov", "Government, Visa & Licence Fees",
     "balp[('code', '=', '617000')]", False),
    (110, "exp_software", "Software & Subscriptions",
     "balp[('code', '=', '618000')]", False),
    (120, "exp_prof", "Professional Services",
     "balp[('code', '=', '619000')]", False),
    (130, "exp_bank", "Bank & Card Fees", "balp[('code', '=', '620000')]", False),
    (140, "exp_maint", "Repairs & Maintenance",
     "balp[('code', '=', '621000')]", False),
    (150, "exp_insurance", "Insurance", "balp[('code', '=', '622000')]", False),
    (160, "exp_travel", "Staff Travel & Tickets",
     "balp[('code', '=', '623000')]", False),
    (170, "exp_depreciation", "Depreciation & Amortisation",
     "balp[('code', '=', '624000')]", False),
    (180, "exp_indemnity", "End-of-Service Indemnity",
     "balp[('code', '=', '625000')]", False),
    (190, "exp_other", "Other Running Costs",
     "opex - exp_salaries - exp_rent - exp_marketing - exp_packaging "
     "- exp_delivery - exp_utilities - exp_gov - exp_software - exp_prof "
     "- exp_bank - exp_maint - exp_insurance - exp_travel - exp_depreciation "
     "- exp_indemnity", False),
    (200, "opex", "Total Running Costs",
     "balp[('account_type', '=', 'expense')]", True),
    (210, "other_income", "Other Income",
     "-balp[('account_type', '=', 'income_other')]", False),
    (220, "net_profit", "NET PROFIT", "gross_profit - opex + other_income", True),
]


class SukkarBooksSetup(models.AbstractModel):
    """Everything the accountant expects to find already switched on.

    Runs on every install and upgrade of the module and only ever fills a blank,
    so an accountant who renames an account, changes a depreciation life or
    points a category somewhere else keeps his change. Nothing here is a
    business figure — the figures live in the Odoo screens and in
    docs/CONFIGURATION.md.
    """
    _name = "sukkar.books.setup"
    _description = "Sukkar — books configuration"

    # ------------------------------------------------------------------ utils
    def _account(self, company, code):
        return self.env["account.account"].search(
            [("code", "=", code), ("company_ids", "in", company.id)], limit=1)

    # ---------------------------------------------------------------- the pass
    @api.model
    def _sukkar_setup_books(self):
        """Configure every company that already has a chart of accounts.

        On a brand-new database Odoo installs the chart of accounts AFTER every
        module's data has loaded, and that install DELETES the journals and
        accounts that were there before it. Anything built here first would be
        wiped — and the delete fails outright once an asset profile points at
        one of those journals, which takes the whole install down. So a company
        with no chart yet is left alone and finished later by the cron below.
        """
        pending = False
        for company in self.env["res.company"].search([]):
            if not company.chart_template:
                pending = True
                continue
            self_c = self.with_company(company)
            self_c._setup_country(company)
            self_c._setup_accounts(company)
            self_c._setup_expense_settings(company)
            self_c._setup_category_accounts(company)
            self_c._setup_accountant_access(company)
            self_c._setup_asset_profiles(company)
            self_c._setup_fiscal_years(company)
            self_c._setup_knet(company)
        self._retire_oversell_block()
        if pending:
            return False
        self._setup_pl_report()
        return True

    @api.model
    def _cron_setup_books(self):
        """Finish the setup on a database that had no chart of accounts yet.

        Idempotent, like the pass itself: it only ever fills a blank. Once every
        company is configured the cron switches itself off.
        """
        payroll = self.env["hr.payslip"]._sukkar_setup_accounting()
        if self._sukkar_setup_books() and payroll:
            cron = self.env.ref("sukkar.cron_setup_books",
                                raise_if_not_found=False)
            if cron:
                cron.sudo().active = False
        return True

    def _retire_oversell_block(self):
        """Switch the shortage refusal off once, on databases that had it on.

        The owner reversed the decision on 20 Sep: an order is always taken, and
        the shortage shows up on that night's stock plan instead. Guarded by a
        one-shot parameter so a shop that deliberately turns the block back on
        keeps it through every later upgrade.
        """
        Param = self.env["ir.config_parameter"].sudo()
        if Param.get_param("sukkar.oversell_block_retired"):
            return
        self.env["res.company"].sudo().search(
            [("sukkar_block_oversell", "=", True)]).sukkar_block_oversell = False
        Param.set_param("sukkar.oversell_block_retired", "1")

    def _setup_country(self, company):
        """The generic chart left the tax country at United States.

        The taxes it created carry that country too, and Odoo refuses to post an
        entry whose tax belongs to a different country from the company — so the
        company and the taxes have to move together or nothing posts at all.
        """
        kuwait = self.env.ref("base.kw", raise_if_not_found=False)
        if not kuwait:
            return
        groups = self.env["account.tax.group"].search(
            [("country_id", "!=", kuwait.id)])
        groups.write({"country_id": kuwait.id})
        taxes = self.env["account.tax"].with_context(active_test=False).search(
            [("country_id", "!=", kuwait.id), ("company_id", "=", company.id)])
        taxes.write({"country_id": kuwait.id})
        if company.account_fiscal_country_id != kuwait:
            company.write({"account_fiscal_country_id": kuwait.id})

    def _setup_accounts(self, company):
        Account = self.env["account.account"]
        for code, name, atype, reconcile in NEW_ACCOUNTS:
            if self._account(company, code):
                continue
            Account.create({
                "code": code, "name": name, "account_type": atype,
                "reconcile": reconcile, "company_ids": [(4, company.id)],
            })

    def _setup_expense_settings(self, company):
        vals = {}
        if not company.sukkar_expense_journal_id:
            journal = self.env["account.journal"].search(
                [("type", "=", "general"), ("company_id", "=", company.id)],
                order="id", limit=1)
            if journal:
                vals["sukkar_expense_journal_id"] = journal.id
        pairs = [
            ("sukkar_expense_cash_account_id", "101501"),
            ("sukkar_expense_bank_account_id", "101401"),
            ("sukkar_expense_unpaid_account_id", "211000"),
        ]
        for field, code in pairs:
            if not company[field]:
                account = self._account(company, code)
                if account:
                    vals[field] = account.id
        if vals:
            company.write(vals)

    def _setup_category_accounts(self, company):
        Category = self.env["sukkar.expense.category"].with_context(
            active_test=False, lang=CATEGORY_LOOKUP_LANG)
        for name, code in CATEGORY_ACCOUNTS.items():
            category = Category.search([("name", "=", name)], limit=1)
            if not category or category.account_id:
                continue
            account = self._account(company, code)
            if account:
                category.account_id = account
        # Expenses typed before the categories had accounts kept their blank.
        blank = self.env["sukkar.expense"].search([
            ("company_id", "=", company.id),
            ("account_id", "=", False),
            ("category_id.account_id", "!=", False)])
        for expense in blank:
            expense.account_id = expense.category_id.account_id

    def _setup_accountant_access(self, company):
        """Give the accounting menus to the people who already hold the rights.

        Odoo Community hides Chart of Accounts, Journal Entries, Reconcile,
        Import Statement, Lock Dates and the OCA statements behind "Show Full
        Accounting Features". Without it an accounting administrator signs in
        and finds an Invoicing app with no accounting in it.
        """
        manager = self.env.ref("account.group_account_manager",
                               raise_if_not_found=False)
        full = self.env.ref("account.group_account_user",
                            raise_if_not_found=False)
        if not manager or not full:
            return
        missing = manager.users.filtered(
            lambda u: not u.has_group("account.group_account_user")
            and not u.share and not u.sukkar_is_driver)
        if missing:
            full.sudo().write({"users": [(4, user.id) for user in missing]})

    def _setup_asset_profiles(self, company):
        if "account.asset.profile" not in self.env:
            return
        Profile = self.env["account.asset.profile"]
        journal = self.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", company.id)],
            order="id", limit=1)
        expense = self._account(company, "624000")
        if not journal or not expense:
            return
        for name, years, asset_code, accum_code in ASSET_PROFILES:
            if Profile.search_count(
                    [("name", "=", name), ("company_id", "=", company.id)]):
                continue
            asset = self._account(company, asset_code)
            accum = self._account(company, accum_code)
            if not asset or not accum:
                continue
            Profile.create({
                "name": name, "company_id": company.id, "journal_id": journal.id,
                "account_asset_id": asset.id,
                "account_depreciation_id": accum.id,
                "account_expense_depreciation_id": expense.id,
                "method": "linear", "method_time": "year",
                "method_number": years, "method_period": "month",
                "prorata": True,
            })

    def _setup_fiscal_years(self, company):
        if "account.fiscal.year" not in self.env:
            return
        FY = self.env["account.fiscal.year"]
        # The books open 1 Oct 2026, so the first year is a short three-month
        # period; every year after it is the calendar year the accountant uses.
        wanted = [
            ("2026 — 1 Oct to 31 Dec (first short period)",
             "2026-10-01", "2026-12-31"),
            ("2027", "2027-01-01", "2027-12-31"),
        ]
        for name, date_from, date_to in wanted:
            if FY.search_count([("date_from", "=", date_from),
                                ("company_id", "=", company.id)]):
                continue
            FY.create({"name": name, "date_from": date_from,
                       "date_to": date_to, "company_id": company.id})

    def _setup_knet(self, company):
        """Card money lands in the bank about a day after the sale.

        Pointing the KNET method at Outstanding Receipts is what lets the bank
        line be matched to the day's takings instead of the two never agreeing.
        """
        outstanding = self._account(company, "101403")
        fees = self._account(company, "620000")
        if outstanding and "pos.payment.method" in self.env:
            # Odoo refuses to touch a payment method while a register session is
            # open, and a shop upgrades mid-trading. Those methods are left for
            # the next run rather than taking the whole upgrade down.
            open_journals = self.env["pos.session"].search([
                ("state", "!=", "closed"), ("company_id", "=", company.id),
            ]).config_id.payment_method_ids
            methods = self.env["pos.payment.method"].search(
                [("company_id", "=", company.id)]) - open_journals
            for method in methods:
                if method.journal_id.type == "bank" and \
                        not method.outstanding_account_id:
                    method.outstanding_account_id = outstanding
        if not fees:
            return
        Model = self.env["account.reconcile.model"]
        name = "KNET settlement — card fee"
        if Model.search_count([("name", "=", name),
                               ("company_id", "=", company.id)]):
            return
        Model.create({
            "name": name, "company_id": company.id,
            "rule_type": "writeoff_button",
            "line_ids": [(0, 0, {
                "account_id": fees.id,
                "label": "KNET fee",
                "amount_type": "percentage",
                "amount_string": "100",
            })],
        })

    def _setup_pl_report(self):
        """The shop's own Profit & Loss, line by line.

        mis_template_financial_report ships a three-line version (income, expense,
        the difference). That answers 'did we make money' but not 'where did it
        go', which is the question the owner actually asks.
        """
        if "mis.report" not in self.env:
            return
        Report = self.env["mis.report"]
        name = "Sukkar — Profit & Loss"
        report = Report.search([("name", "=", name)], limit=1)
        if not report:
            style = self.env.ref(
                "mis_template_financial_report.style_default",
                raise_if_not_found=False)
            report = Report.create(
                {"name": name, "style_id": style.id if style else False})
        header = self.env.ref("mis_template_financial_report.style_header",
                              raise_if_not_found=False)
        for sequence, code, label, expression, bold in PL_KPIS:
            if report.kpi_ids.filtered(lambda k, c=code: k.name == c):
                continue
            self.env["mis.report.kpi"].create({
                "report_id": report.id, "name": code, "description": label,
                "expression": expression, "sequence": sequence, "type": "num",
                "compare_method": "diff", "accumulation_method": "sum",
                "style_id": header.id if (bold and header) else False,
            })
        self._setup_pl_instances(report)

    def _repair_balance_sheet_period(self, instance, balance_sheet):
        """Put right a Balance Sheet that was first built with a month column.

        Only the untouched original is corrected — one relative monthly column,
        exactly as it was created. An accountant who has set his own periods
        keeps them.
        """
        if not balance_sheet or instance.report_id != balance_sheet:
            return
        period = instance.period_ids
        if len(period) != 1 or period.mode != "relative" or period.type != "m":
            return
        period.write({"type": "y", "offset": 0, "duration": 1,
                      "name": "This year"})

    def _setup_pl_instances(self, report):
        Instance = self.env["mis.report.instance"]
        balance_sheet = self.env.ref(
            "mis_template_financial_report.report_bs", raise_if_not_found=False)
        wanted = [
            ("Profit & Loss — this month vs last month", report, [
                ("This month", "m", 0), ("Last month", "m", -1)]),
            ("Profit & Loss — this year", report, [("This year", "y", 0)]),
        ]
        # A Balance Sheet column MUST cover the whole financial year. Its
        # "Unallocated Earnings" line is the profit of the column period, while
        # the asset and liability lines are running totals — so a one-month
        # column shows every asset the shop owns against one month of profit and
        # the statement does not balance.
        if balance_sheet:
            wanted.append(("Balance Sheet — today", balance_sheet,
                           [("This year", "y", 0)]))
        # An accountant opening this system looks for "Profit & Loss" and
        # "Balance Sheet" on the Reporting menu — not for a list called MIS
        # Reports with the statements buried inside it.
        menus = [
            ("Profit & Loss", "Profit & Loss — this month vs last month", 5),
            ("Balance Sheet", "Balance Sheet — today", 6),
        ]
        for name, target, periods in wanted:
            if not target:
                continue
            existing = Instance.search([("name", "=", name)], limit=1)
            if existing:
                self._repair_balance_sheet_period(existing, balance_sheet)
                continue
            Instance.create({
                "name": name, "report_id": target.id, "target_move": "posted",
                "period_ids": [(0, 0, {
                    "name": label, "mode": "relative", "type": ptype,
                    "offset": offset, "duration": 1, "source": "actuals",
                    "sequence": (index + 1) * 10,
                }) for index, (label, ptype, offset) in enumerate(periods)],
            })
        parent = self.env.ref("account.menu_finance_reports",
                              raise_if_not_found=False)
        if not parent:
            return

        for label, instance_name, sequence in menus:
            instance = Instance.search([("name", "=", instance_name)], limit=1)
            if not instance or self.env["ir.ui.menu"].search_count(
                    [("name", "=", label), ("parent_id", "=", parent.id)]):
                continue
            action = self.env["ir.actions.act_window"].create({
                "name": label,
                "res_model": "mis.report.instance",
                "res_id": instance.id,
                "view_mode": "form",
                "target": "current",
            })
            self.env["ir.ui.menu"].create({
                "name": label, "parent_id": parent.id, "sequence": sequence,
                "action": "ir.actions.act_window,%d" % action.id,
            })
