from odoo import fields, models


class ResCompany(models.Model):
    """Where an expense goes when it is confirmed.

    Until September 2026 the Expenses screen kept its own list of costs and
    never touched the ledger, so rent, advertising and packaging did not exist
    as far as the accounts were concerned. These four settings are the bridge:
    they say which journal the entry is written in and which money account is
    credited. Every one of them is edited in Settings → Sukkar → Books and is
    listed in docs/CONFIGURATION.md.
    """
    _inherit = "res.company"

    sukkar_post_expenses = fields.Boolean(
        string="Post Expenses to the Books", default=True,
        help="On: confirming an expense writes it into the accounts, so the "
             "Profit & Loss includes it. Off: the Expenses screen is a private "
             "list again and the accountant will not see these costs.")
    sukkar_expense_journal_id = fields.Many2one(
        "account.journal", string="Expense Journal",
        domain="[('type', '=', 'general')]",
        help="The book the expense entries are written in.")
    sukkar_expense_cash_account_id = fields.Many2one(
        "account.account", string="Money Out — Cash",
        help="Credited when an expense is paid from the till.")
    sukkar_expense_bank_account_id = fields.Many2one(
        "account.account", string="Money Out — Bank / KNET / Link",
        help="Credited when an expense is paid by transfer, card or link.")
    sukkar_expense_unpaid_account_id = fields.Many2one(
        "account.account", string="Money Out — Not Paid Yet",
        help="Credited when an expense is recorded before it is paid, so it "
             "shows as owed until it is settled.")

    sukkar_generate_recurring = fields.Boolean(
        string="Create This Month's Fixed Costs Automatically", default=True,
        help="On the chosen day each month, every recurring cost is copied into "
             "a DRAFT expense for that month. Nothing reaches the books until "
             "somebody confirms it.")
    sukkar_generate_recurring_day = fields.Integer(
        string="Create Them On Day", default=1,
        help="Day of the month the drafts are created. 1 = the first. Every "
             "month has 28 days, so anything above 28 is treated as 28.")
