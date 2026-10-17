from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SukkarDisplayProduction(models.Model):
    """What the shop makes each morning for the display counter.

    Separate from order-driven production: these are cupcakes and boxes made
    speculatively to fill the counter, not against a customer order. Producing
    consumes the ingredients from stock the same way a kitchen ticket does.
    """
    _name = "sukkar.display.production"
    _description = "Daily Display Production"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    name = fields.Char(default="New", copy=False, readonly=True, index=True)
    date = fields.Date(
        string="Production Day", required=True, default=fields.Date.context_today,
        tracking=True)
    state = fields.Selection(
        [("draft", "To Make"), ("done", "Made")],
        default="draft", tracking=True, string="Status")
    line_ids = fields.One2many(
        "sukkar.display.production.line", "plan_id", string="Items")
    note = fields.Text(string="Notes")
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related="company_id.currency_id")

    mrp_production_ids = fields.Many2many(
        "mrp.production", string="Manufacturing Orders", copy=False)
    mrp_production_count = fields.Integer(compute="_compute_mrp_count")
    total_qty = fields.Float(compute="_compute_totals", string="Total Items")
    total_cost = fields.Monetary(
        compute="_compute_totals", string="Ingredient Cost", currency_field="currency_id")

    # --- What the batch actually sold (requirement 12.4) --------------------
    # HONEST LIMIT, stated here because the number looks more precise than it is:
    # nothing in the system traces an individual cupcake from a batch to the till.
    # Doing that properly needs lot or serial tracking on every display item, which
    # means the counter scanning a lot on every sale — not a reasonable ask for a
    # cupcake counter.
    #
    # So this attributes by TIME WINDOW: sales of a product from the moment the
    # batch was made until the next batch of that same product replaces it. That
    # is exactly how the shop reasons about it out loud ("we made 24 this morning,
    # we sold 19"), and it is right whenever a product is made once and sold down
    # before the next batch — which is the normal case here. It will over- or
    # under-attribute if two batches of the same item overlap in the counter.
    sold_qty = fields.Float(
        compute="_compute_sales", string="Sold", store=False,
        help="Units of this batch sold before the next batch replaced it — across "
             "the POS and any customer invoices.")
    sold_revenue = fields.Monetary(
        compute="_compute_sales", string="Revenue", currency_field="currency_id")
    unsold_qty = fields.Float(
        compute="_compute_sales", string="Unsold",
        help="Made but not sold in the window. Waste, or still on the counter.")
    margin = fields.Monetary(
        compute="_compute_sales", string="Margin", currency_field="currency_id",
        help="Revenue from this batch less the ingredient cost of making it.")

    def _sukkar_window_end(self, product):
        """The date the next batch of `product` takes over. None = still current."""
        self.ensure_one()
        later = self.search([
            ("state", "=", "done"),
            ("date", ">", self.date),
            ("line_ids.product_id", "=", product.id),
        ], order="date asc", limit=1)
        return later.date or None

    def _sukkar_window_peers(self, product):
        """Every done batch of `product` sharing this batch's window.

        Batches made on the same day compete for the same sales. Timestamps cannot
        settle it — two batches entered in one sitting carry the same create_date —
        so the window's sales are SHARED IN PROPORTION to how much each batch made.
        That makes double counting arithmetically impossible: the shares sum to one.
        With the normal one-batch-a-day pattern this reduces to the whole window.
        """
        self.ensure_one()
        return self.search([
            ("state", "=", "done"),
            ("date", "=", self.date),
            ("line_ids.product_id", "=", product.id),
        ])

    def _sukkar_sales_for(self, product):
        """(qty, revenue) of `product` attributable to THIS batch."""
        self.ensure_one()
        Slot = self.env["sukkar.delivery.slot"]
        start = Slot._tz_midnight_utc(self.date)
        end_date = self._sukkar_window_end(product)
        end = Slot._tz_midnight_utc(end_date) if end_date else fields.Datetime.now()

        qty = revenue = 0.0
        # The counter: POS is how this shop actually sells display stock. Refunds
        # come through as negative lines, so summing handles them.
        pos_lines = self.env["pos.order.line"].sudo().search([
            ("product_id", "=", product.id),
            ("order_id.date_order", ">=", start),
            ("order_id.date_order", "<", end),
            ("order_id.state", "in", ("paid", "done", "invoiced")),
        ])
        qty += sum(pos_lines.mapped("qty"))
        revenue += sum(pos_lines.mapped("price_subtotal_incl"))

        # Customer invoices, for anything sold off the counter on an invoice.
        # invoice_date is a DATE, so compare it against the batch's own dates —
        # never against start.date(). Kuwait is UTC+3, so midnight local is 21:00
        # UTC the previous day, and deriving the date from that UTC datetime would
        # silently pull in the day before the batch was even made.
        # A till sale the cashier ticked "Invoice" is one sale with two records:
        # the POS line above and the invoice the register generated for it.
        # Counting both told the owner he had sold twice what he made.
        inv_domain = [
            ("product_id", "=", product.id),
            ("move_id.state", "=", "posted"),
            ("move_id.move_type", "in", ("out_invoice", "out_refund")),
            ("move_id.invoice_date", ">=", self.date),
            ("move_id.pos_order_ids", "=", False),
        ]
        if end_date:
            inv_domain.append(("move_id.invoice_date", "<", end_date))
        for line in self.env["account.move.line"].sudo().search(inv_domain):
            sign = -1.0 if line.move_id.move_type == "out_refund" else 1.0
            qty += sign * line.quantity
            revenue += sign * line.price_total

        # Split the window between the batches that share it.
        peers = self._sukkar_window_peers(product)
        made_here = sum(l.qty for l in self.line_ids if l.product_id == product)
        made_total = sum(
            l.qty for b in peers for l in b.line_ids if l.product_id == product)
        share = (made_here / made_total) if made_total else 0.0
        qty *= share
        revenue *= share

        # Never credit a batch with more than it made — the surplus was already on
        # the counter before this batch existed.
        if qty > made_here:
            revenue *= (made_here / qty) if qty else 0.0
            qty = made_here
        return max(0.0, qty), max(0.0, revenue)

    @api.depends("line_ids.product_id", "line_ids.qty", "date", "state", "total_cost")
    def _compute_sales(self):
        for rec in self:
            if rec.state != "done":
                rec.sold_qty = rec.sold_revenue = rec.unsold_qty = rec.margin = 0.0
                continue
            qty = revenue = 0.0
            for line in rec.line_ids:
                if not line.product_id:
                    continue
                q, r = rec._sukkar_sales_for(line.product_id)
                qty += q
                revenue += r
            rec.sold_qty = qty
            rec.sold_revenue = revenue
            rec.unsold_qty = max(0.0, rec.total_qty - qty)
            rec.margin = revenue - rec.total_cost

    def action_view_sales(self):
        """The POS orders that sold what this batch made."""
        self.ensure_one()
        Slot = self.env["sukkar.delivery.slot"]
        start = Slot._tz_midnight_utc(self.date)
        products = self.line_ids.mapped("product_id")
        ends = [self._sukkar_window_end(p) for p in products]
        real_ends = [e for e in ends if e]
        end = (max(real_ends)
               if len(real_ends) == len(products) and real_ends
               else fields.Datetime.now())
        lines = self.env["pos.order.line"].sudo().search([
            ("product_id", "in", products.ids),
            ("order_id.date_order", ">=", start),
            ("order_id.date_order", "<", end),
        ])
        return {
            "type": "ir.actions.act_window",
            "name": _("Sales from %s", self.name),
            "res_model": "pos.order",
            "view_mode": "list,form",
            "domain": [("id", "in", lines.mapped("order_id").ids)],
        }

    @api.depends("mrp_production_ids")
    def _compute_mrp_count(self):
        for rec in self:
            rec.mrp_production_count = len(rec.mrp_production_ids)

    @api.depends("line_ids.qty", "line_ids.product_id")
    def _compute_totals(self):
        for rec in self:
            rec.total_qty = sum(rec.line_ids.mapped("qty"))
            rec.total_cost = sum(
                l.qty * l.product_id.standard_price for l in rec.line_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "sukkar.display.production") or "New"
        return super().create(vals_list)

    def action_produce(self):
        """Build everything on the list, consuming ingredients from stock."""
        Production = self.env["mrp.production"].sudo()
        BoM = self.env["mrp.bom"].sudo()
        for plan in self:
            if plan.state == "done":
                raise UserError(_("%s has already been made.", plan.name))
            if not plan.line_ids:
                raise UserError(_("Add at least one item before producing."))
            made = Production
            for line in plan.line_ids:
                if line.qty <= 0:
                    continue
                bom = BoM._bom_find(line.product_id).get(line.product_id)
                if not bom:
                    raise UserError(_(
                        "%s has no recipe, so it cannot be produced.",
                        line.product_id.display_name))
                mo = Production.create({
                    "product_id": line.product_id.id,
                    "product_qty": line.qty,
                    "bom_id": bom.id,
                    "origin": f"{plan.name} (display)",
                })
                mo.action_confirm()
                mo.action_assign()
                mo.qty_producing = mo.product_qty
                mo._set_qty_producing()
                result = mo.button_mark_done()
                if isinstance(result, dict) and result.get("res_model"):
                    wiz = self.env[result["res_model"]].with_context(
                        result.get("context") or {}).create({})
                    if hasattr(wiz, "action_confirm"):
                        wiz.action_confirm()
                    elif hasattr(wiz, "action_backorder"):
                        wiz.action_backorder()
                made |= mo
            plan.mrp_production_ids = [(6, 0, made.ids)]
            plan.state = "done"
        return True

    def action_view_mrp(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Manufacturing Orders"),
            "res_model": "mrp.production",
            "view_mode": "list,form",
            "domain": [("id", "in", self.mrp_production_ids.ids)],
        }

    def action_copy_yesterday(self):
        """Yesterday's list is usually today's starting point."""
        self.ensure_one()
        previous = self.search(
            [("date", "<", self.date), ("state", "=", "done")], limit=1, order="date desc")
        if not previous:
            raise UserError(_("There is no earlier display production to copy."))
        self.line_ids.unlink()
        self.line_ids = [(0, 0, {"product_id": l.product_id.id, "qty": l.qty})
                         for l in previous.line_ids]
        return True


class SukkarDisplayProductionLine(models.Model):
    _name = "sukkar.display.production.line"
    _description = "Daily Display Production Line"

    plan_id = fields.Many2one(
        "sukkar.display.production", required=True, ondelete="cascade", index=True)
    product_id = fields.Many2one(
        "product.product", string="Item", required=True,
        domain="[('bom_ids', '!=', False)]",
        help="Only items with a recipe can be produced.")
    qty = fields.Float(string="Quantity", default=1.0, required=True)
    uom_name = fields.Char(related="product_id.uom_id.name", string="Unit")
    on_hand = fields.Float(related="product_id.qty_available", string="In Stock")
    currency_id = fields.Many2one(related="plan_id.currency_id")
    cost = fields.Monetary(
        compute="_compute_cost", string="Cost", currency_field="currency_id")

    sold_qty = fields.Float(compute="_compute_line_sales", string="Sold")
    unsold_qty = fields.Float(compute="_compute_line_sales", string="Unsold")
    sold_revenue = fields.Monetary(
        compute="_compute_line_sales", string="Revenue", currency_field="currency_id")

    @api.depends("qty", "product_id", "plan_id.date", "plan_id.state")
    def _compute_line_sales(self):
        for line in self:
            if line.plan_id.state != "done" or not line.product_id:
                line.sold_qty = line.unsold_qty = line.sold_revenue = 0.0
                continue
            qty, revenue = line.plan_id._sukkar_sales_for(line.product_id)
            line.sold_qty = qty
            line.unsold_qty = max(0.0, line.qty - qty)
            line.sold_revenue = revenue

    @api.depends("qty", "product_id")
    def _compute_cost(self):
        for line in self:
            line.cost = line.qty * line.product_id.standard_price
