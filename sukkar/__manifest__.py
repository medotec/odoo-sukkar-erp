{
    "name": "Sukkar Operations",
    "version": "18.0.1.0.0",
    "summary": "Gifting dessert operations: orders, gifting fields, kitchen workflow, delivery",
    "description": "Custom operations layer for Sukkar (gifting dessert business): "
                   "gifting fields on orders, gift invoice + delivery sheet, "
                   "kitchen production workflow, delivery and payroll.",
    "author": "Ahmed S.",
    "website": "https://www.upwork.com/freelancers/~01437ca49ef98616c4",
    "category": "Sales/Sales",
    "license": "LGPL-3",
    "depends": [
        "sale_management",
        "hr_contract",   # employees + contracts (the wage overtime is calculated on)
        "hr_attendance",  # models/hr_attendance.py inherits it; without it a new database cannot install
        "payroll",       # OCA payroll — Odoo Community ships none
        "payroll_account",  # payslips post to accounting (one OT system, one ledger)
        "account",
        # --- the books layer (all free, all OCA) ---------------------------
        "account_asset_management",     # depreciation — Enterprise-only in Odoo
        "account_fiscal_year",          # the short first year, 1 Oct–31 Dec 2026
        "account_lock_date_update",     # the accountant locks a month, without admin rights
        "account_spread_cost_revenue",  # a year of rent spread over twelve months
        "mis_builder",
        "mis_template_financial_report",  # the P&L and Balance Sheet Community lacks
        "stock",
        "mrp",
        "mail",
        "delivery",   # delivery carriers + fees on orders
        "loyalty",    # Sukkar Rewards + gift cards
        "point_of_sale",  # register: delivery popup, card notes, receipt, size photos
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/sukkar_production_stage_data.xml",
        "data/sukkar_delivery_slot_data.xml",
        "data/sukkar_pos_note_data.xml",
        "data/ir_cron_data.xml",
        "data/sukkar_expense_category_data.xml",
        "data/sukkar_payroll_data.xml",
        "data/sukkar_accounting_data.xml",
        "data/sukkar_attendance_cron.xml",
        "data/sukkar_cake_sequence_data.xml",
        "data/sukkar_area_load.xml",
        "views/delivery_carrier_views.xml",
        "views/sukkar_delivery_slot_views.xml",
        "views/sukkar_cake_flavour_views.xml",
        "views/sukkar_cake_batch_views.xml",
        "views/sukkar_cake_move_views.xml",
        "views/sukkar_cake_override_views.xml",
        "views/sukkar_oversell_wizard_views.xml",
        # The flavour records need the model's views loaded first (the Settings
        # page links to the flavour action).
        "data/sukkar_cake_flavour_data.xml",
        "data/sukkar_cake_flavour_default.xml",
        "views/sale_order_views.xml",
        "views/sukkar_mark_paid_wizard_views.xml",
        "views/sukkar_production_views.xml",
        "views/sukkar_production_stage_views.xml",
        "views/sukkar_production_stage_log_views.xml",
        "views/sukkar_orders_views.xml",
        "views/sukkar_dispatch_views.xml",
        "views/sukkar_drivers_views.xml",
        "views/sukkar_driver_receipt_wizard_views.xml",
        "views/sukkar_delivery_views.xml",
        "views/sukkar_display_production_views.xml",
        "views/sukkar_stock_plan_views.xml",
        "views/sukkar_expense_views.xml",
        "views/sukkar_monthly_close_views.xml",
        "views/hr_payroll_views.xml",
        "views/sukkar_attendance_views.xml",
        "views/sukkar_missing_punch_views.xml",
        "views/sukkar_kitchen_screen_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_users_views.xml",
        "views/sukkar_menus.xml",
        "views/sukkar_driver_cashup_views.xml",
        "views/sukkar_area_views.xml",
        # Last: the setup pass reads the expense categories loaded above and
        # needs the models' views in place before it links the reports to menus.
        "data/sukkar_books_data.xml",
        "reports/report_invoice.xml",
        "reports/report_invoice_ar.xml",
        "reports/report_pos_receipt_ar.xml",
        "reports/report_delivery_sheet.xml",
        "reports/report_driver_receipt.xml",
        "reports/report_kitchen_ticket.xml",
        "reports/report_order_receipt.xml",
        "reports/report_quotation_ar.xml",
    ],
    "assets": {
        # prepend so our brand variables are set BEFORE Odoo's primary_variables.scss,
        # making every derived colour ($o-brand-primary, $o-brand-odoo, button maps,
        # navbar, links, mail composer, …) follow the brand instead of the old purple.
        "web._assets_primary_variables": [
            ("prepend", "sukkar/static/src/scss/sukkar_variables.scss"),
        ],
        "web.assets_backend": [
            "sukkar/static/src/scss/sukkar_backend.scss",
            "sukkar/static/src/scss/sukkar_kitchen_screen.scss",
            "sukkar/static/src/js/sukkar_kitchen_screen.js",
            "sukkar/static/src/scss/sukkar_drivers.scss",
            "sukkar/static/src/js/sukkar_drivers_kanban.js",
            "sukkar/static/src/xml/sukkar_drivers_kanban.xml",
        ],
        "point_of_sale._assets_pos": [
            "sukkar/static/src/scss/sukkar_pos.scss",
            "sukkar/static/src/scss/sukkar_pos_boutique.scss",
            "sukkar/static/src/scss/sukkar_pos_layout.scss",
            "sukkar/static/src/xml/pos_receipt.xml",
            "sukkar/static/src/xml/pos_variant_image.xml",
            "sukkar/static/src/pos/sukkar_customer_popup.js",
            "sukkar/static/src/pos/sukkar_customer_popup.xml",
            "sukkar/static/src/pos/sukkar_partner_create.js",
            "sukkar/static/src/pos/sukkar_fee.js",
            "sukkar/static/src/pos/sukkar_delivery_slip.js",
            "sukkar/static/src/pos/sukkar_delivery_slip.xml",
            "sukkar/static/src/pos/sukkar_receipt_preview.js",
            "sukkar/static/src/pos/sukkar_receipt_preview.xml",
            "sukkar/static/src/pos/sukkar_recipient_popup.js",
            "sukkar/static/src/pos/sukkar_recipient_popup.xml",
            "sukkar/static/src/pos/sukkar_pos_patches.js",
            "sukkar/static/src/pos/sukkar_note_popup.js",
            "sukkar/static/src/pos/sukkar_note_popup.xml",
            "sukkar/static/src/pos/sukkar_payment_screen.js",
            "sukkar/static/src/pos/sukkar_control_buttons.js",
            "sukkar/static/src/pos/sukkar_control_buttons.xml",
            "sukkar/static/src/pos/sukkar_register_layout.js",
            "sukkar/static/src/pos/sukkar_register_layout.xml",
            "sukkar/static/src/pos/sukkar_order_summary.js",
            "sukkar/static/src/pos/sukkar_order_summary.xml",
            "sukkar/static/src/pos/sukkar_order_type_popup.js",
            "sukkar/static/src/pos/sukkar_order_type_popup.xml",
            "sukkar/static/src/pos/sukkar_flavour_popup.js",
            "sukkar/static/src/pos/sukkar_flavour_popup.xml",
            "sukkar/static/src/pos/sukkar_fee_popup.js",
            "sukkar/static/src/pos/sukkar_fee_popup.xml",
        ],
    },
    "application": True,
    "installable": True,
}
