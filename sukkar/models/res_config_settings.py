from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """Surfaces the Sukkar operating settings in Settings → Sukkar."""
    _inherit = "res.config.settings"

    sukkar_finish_before_min = fields.Integer(
        related="company_id.sukkar_finish_before_min",
        readonly=False,
        string="Finish Before Delivery (minutes)",
    )
    sukkar_tz = fields.Selection(
        related="company_id.sukkar_tz",
        readonly=False,
        string="Operating Timezone",
    )
    sukkar_payroll_divisor_days = fields.Integer(
        related="company_id.sukkar_payroll_divisor_days", readonly=False,
        string="Salary Divisor — Days")
    sukkar_payroll_divisor_hours = fields.Float(
        related="company_id.sukkar_payroll_divisor_hours", readonly=False,
        string="Salary Divisor — Hours")
    sukkar_ot_rate_normal = fields.Float(
        related="company_id.sukkar_ot_rate_normal", readonly=False,
        string="Overtime Rate — Normal Day")
    sukkar_ot_rate_rest = fields.Float(
        related="company_id.sukkar_ot_rate_rest", readonly=False,
        string="Overtime Rate — Rest Day")
    sukkar_ot_rate_holiday = fields.Float(
        related="company_id.sukkar_ot_rate_holiday", readonly=False,
        string="Overtime Rate — Public Holiday")
    sukkar_ot_cap_default_hours = fields.Float(
        related="company_id.sukkar_ot_cap_default_hours", readonly=False)
    sukkar_ot_min_block_hours = fields.Float(
        related="company_id.sukkar_ot_min_block_hours", readonly=False,
        string="Minimum Overtime Block (hours)")
    sukkar_late_grace_min = fields.Integer(
        related="company_id.sukkar_late_grace_min", readonly=False,
        string="Lateness Grace (minutes)")
    sukkar_prorate_basic = fields.Boolean(
        related="company_id.sukkar_prorate_basic", readonly=False,
        string="Prorate Basic Pay for Part Months")
    sukkar_prorate_basis_days = fields.Integer(
        related="company_id.sukkar_prorate_basis_days", readonly=False,
        string="Part-Month Basis (days)")

    # === September 2026 batch ===============================================
    sukkar_card_message_max = fields.Integer(
        related="company_id.sukkar_card_message_max", readonly=False,
        string="Card Message Limit (characters)")
    sukkar_default_flavour_id = fields.Many2one(
        related="company_id.sukkar_default_flavour_id", readonly=False,
        string="Default Cake Flavour")
    sukkar_pos_fee_amounts = fields.Char(
        related="company_id.sukkar_pos_fee_amounts", readonly=False,
        string="Register Delivery Fee — Amounts (KWD)")
    sukkar_pos_fee_product_id = fields.Many2one(
        related="company_id.sukkar_pos_fee_product_id", readonly=False,
        string="Register Delivery Fee Product")
    sukkar_cod_overdue_days = fields.Integer(
        related="company_id.sukkar_cod_overdue_days", readonly=False,
        string="COD Overdue After (days)")
    sukkar_cake_piece_product_id = fields.Many2one(
        related="company_id.sukkar_cake_piece_product_id", readonly=False,
        string="Cake Piece Product")
    sukkar_stock_plan_hour = fields.Integer(
        related="company_id.sukkar_stock_plan_hour", readonly=False,
        string="Write Tomorrow's Stock Plan At")
    sukkar_block_oversell = fields.Boolean(
        related="company_id.sukkar_block_oversell", readonly=False,
        string="Block Orders Beyond Available Pieces")
    sukkar_reserve_horizon_days = fields.Integer(
        related="company_id.sukkar_reserve_horizon_days", readonly=False,
        string="Reserve Pieces This Many Days Ahead")

    # === Books (September 2026) ============================================
    sukkar_post_expenses = fields.Boolean(
        related="company_id.sukkar_post_expenses", readonly=False,
        string="Post Expenses to the Books")
    sukkar_expense_journal_id = fields.Many2one(
        related="company_id.sukkar_expense_journal_id", readonly=False,
        string="Expense Journal")
    sukkar_expense_cash_account_id = fields.Many2one(
        related="company_id.sukkar_expense_cash_account_id", readonly=False,
        string="Money Out — Cash")
    sukkar_expense_bank_account_id = fields.Many2one(
        related="company_id.sukkar_expense_bank_account_id", readonly=False,
        string="Money Out — Bank / KNET / Link")
    sukkar_expense_unpaid_account_id = fields.Many2one(
        related="company_id.sukkar_expense_unpaid_account_id", readonly=False,
        string="Money Out — Not Paid Yet")
    sukkar_generate_recurring = fields.Boolean(
        related="company_id.sukkar_generate_recurring", readonly=False,
        string="Create This Month's Fixed Costs Automatically")
    sukkar_generate_recurring_day = fields.Integer(
        related="company_id.sukkar_generate_recurring_day", readonly=False,
        string="Create Them On Day")
