from odoo import api, fields, models


class ResCompany(models.Model):
    """The Sukkar operating settings added with the September 2026 batch.

    Kept in their own file so the settings the six builders share have one
    owner and one place. Every number here is edited in Settings → Sukkar and
    documented in docs/CONFIGURATION.md — none of them is ever hard-coded.
    """
    _inherit = "res.company"

    # --- Register ------------------------------------------------------------
    sukkar_card_message_max = fields.Integer(
        string="Card Message Limit (characters)", default=150,
        help="Longest greeting-card message the register accepts. The card is a "
             "physical card: past this many characters the text stops fitting on it.")

    # --- Cake flavours -------------------------------------------------------
    sukkar_default_flavour_id = fields.Many2one(
        "sukkar.cake.flavour", string="Default Cake Flavour",
        help="Used when nobody picks flavours by hand — normally "
             "'Mixed Cake Flavours'.")

    # --- Delivery fee at the register ---------------------------------------
    sukkar_pos_fee_amounts = fields.Char(
        string="Register Delivery Fee — Amounts (KWD)",
        default="1, 1.5, 2, 2.25, 2.5, 4, 5, 7",
        help="The amounts the cashier may pick by hand when overriding the "
             "area's own fee, separated by commas. These are the shop's real "
             "delivery prices, so the list is short and every button is one "
             "the shop actually charges.")
    sukkar_pos_fee_product_id = fields.Many2one(
        "product.product", string="Register Delivery Fee Product",
        help="The product the cashier's delivery fee is charged on, so the fee "
             "is its own line on the receipt and the invoice.")

    def _sukkar_pos_fee_amounts(self):
        """The fee buttons, read from the setting. One bad entry is skipped
        rather than taking the register down with it."""
        self.ensure_one()
        amounts = []
        for part in (self.sukkar_pos_fee_amounts or "").split(","):
            try:
                amounts.append(float(part.strip()))
            except ValueError:
                continue
        return sorted(set(amounts))

    # --- Cash on delivery ----------------------------------------------------
    sukkar_cod_overdue_days = fields.Integer(
        string="COD Overdue After (days)", default=2,
        help="How many days an unpaid cash-on-delivery order may age before it "
             "is flagged overdue so the driver can be chased.")

    # --- Cake pieces: stock and reservation ---------------------------------
    sukkar_cake_piece_product_id = fields.Many2one(
        "product.product", string="Cake Piece Product",
        help="The recipe component that counts as one cake piece. The piece "
             "count of every bouquet is read from its recipe against this item.")
    sukkar_block_oversell = fields.Boolean(
        string="Block Orders Beyond Available Pieces", default=False,
        help="Off (the shop's normal way): an order is always taken, and what "
             "has to be bought or made appears on that night's stock plan. "
             "On: confirming an order short of cake pieces is refused, and only "
             "a manager override lets it through.")
    sukkar_stock_plan_hour = fields.Integer(
        string="Write Tomorrow's Stock Plan At", default=23,
        help="The hour, shop time, at which the system works out everything the "
             "kitchen needs for the next day and leaves the list waiting.")
    sukkar_reserve_horizon_days = fields.Integer(
        string="Reserve Pieces This Many Days Ahead", default=1,
        help="A confirmed order due within this many days holds its pieces as "
             "'Reserved for Tomorrow'. 1 = tomorrow's orders.")

    @api.model
    def _sukkar_seed_default_flavour(self):
        """Point companies with no default flavour at 'Mixed Cake Flavours'.

        Only fills a blank, so a company that picked a different default — or
        renamed the flavour — is left exactly as it is.
        """
        flavour = self.env.ref(
            "sukkar.sukkar_flavour_mixed", raise_if_not_found=False)
        if flavour:
            self.search([("sukkar_default_flavour_id", "=", False)]).write(
                {"sukkar_default_flavour_id": flavour.id})
        return True

    @api.model
    def _sukkar_seed_fee_product(self):
        """Give every company an item to charge the register's delivery fee on.

        Without it the cashier's fee popup has nothing to put the amount on, so
        the first delivery of a brand-new shop could not be charged for. Like
        the flavour above it only fills a blank: a company already pointing at
        its own item keeps it.
        """
        for company in self.search([("sukkar_pos_fee_product_id", "=", False)]):
            self.env["pos.order"].with_company(company)._sukkar_fee_product()
        return True
