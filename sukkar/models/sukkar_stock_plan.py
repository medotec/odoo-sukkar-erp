from datetime import timedelta

from odoo import _, api, fields, models


class SukkarStockPlan(models.Model):
    """"What do I have to make and buy for a given day?"

    Takes every confirmed order due on the chosen day, explodes the recipes down
    to raw materials and packaging, and compares the total against what is on the
    shelf. Anything short is flagged so it can be bought before the day starts.
    """
    _name = "sukkar.stock.plan"
    _description = "Daily Stock Plan"
    _order = "date desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    date = fields.Date(
        string="Planning For", required=True,
        default=lambda self: fields.Date.context_today(self) + timedelta(days=1),
        help="Defaults to tomorrow — the question is always what has to be ready "
             "before the next trading day starts.")
    line_ids = fields.One2many("sukkar.stock.plan.line", "plan_id", string="Requirements")
    order_ids = fields.Many2many("sale.order", string="Orders Covered", copy=False)
    order_count = fields.Integer(compute="_compute_counts", string="Orders")
    shortage_count = fields.Integer(compute="_compute_counts", string="Short Items")
    computed_on = fields.Datetime(string="Last Calculated", readonly=True, copy=False)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, required=True)

    @api.depends("date")
    def _compute_name(self):
        for rec in self:
            rec.name = _("Stock plan for %s", rec.date) if rec.date else _("Stock plan")

    @api.depends("order_ids", "line_ids.shortfall")
    def _compute_counts(self):
        for rec in self:
            rec.order_count = len(rec.order_ids)
            rec.shortage_count = len(rec.line_ids.filtered(lambda l: l.shortfall > 0))

    def action_compute(self):
        """Recalculate from the confirmed orders due that day."""
        Line = self.env["sukkar.stock.plan.line"]
        for plan in self:
            plan.line_ids.unlink()
            start = fields.Datetime.to_string(
                fields.Datetime.from_string(f"{plan.date} 00:00:00"))
            end = fields.Datetime.to_string(
                fields.Datetime.from_string(f"{plan.date} 23:59:59"))
            orders = self.env["sale.order"].search([
                ("state", "=", "sale"),
                ("sukkar_delivery_datetime", ">=", start),
                ("sukkar_delivery_datetime", "<=", end),
            ])
            plan.order_ids = [(6, 0, orders.ids)]

            needed = {}   # product -> qty
            for order in orders:
                for line in order.order_line:
                    product = line.product_id
                    if line.display_type or not product or line.is_delivery:
                        continue
                    qty = line.product_uom_qty
                    bom = self.env["mrp.bom"].sudo()._bom_find(product).get(product)
                    if bom:
                        # Explode the recipe (and any sub-recipes) to real components.
                        factor = qty / (bom.product_qty or 1.0)
                        _boms, lines = bom.explode(product, factor)
                        for bom_line, data in lines:
                            needed[bom_line.product_id] = needed.get(
                                bom_line.product_id, 0.0) + data["qty"]
                    else:
                        needed[product] = needed.get(product, 0.0) + qty

            for product, qty in sorted(needed.items(), key=lambda kv: kv[0].display_name):
                Line.create({
                    "plan_id": plan.id,
                    "product_id": product.id,
                    "required_qty": qty,
                })
            plan.computed_on = fields.Datetime.now()
        return True

    @api.model
    def _cron_build_tomorrow(self):
        """Tonight, work out everything the kitchen needs for tomorrow.

        Runs every evening so the list is already waiting in the morning. Safe to
        run twice: the same day's plan is recalculated, never duplicated.
        """
        tomorrow = fields.Date.context_today(self) + timedelta(days=1)
        plan = self.search([("date", "=", tomorrow)], limit=1)
        if not plan:
            plan = self.create({"date": tomorrow})
        plan.action_compute()
        return plan

    def action_view_orders(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Orders for %s", self.date),
            "res_model": "sale.order",
            "view_mode": "list,form",
            "domain": [("id", "in", self.order_ids.ids)],
        }


class SukkarStockPlanLine(models.Model):
    _name = "sukkar.stock.plan.line"
    _description = "Daily Stock Plan Line"
    _order = "shortfall desc, product_id"

    plan_id = fields.Many2one(
        "sukkar.stock.plan", required=True, ondelete="cascade", index=True)
    product_id = fields.Many2one("product.product", string="Item", required=True)
    categ_id = fields.Many2one(related="product_id.categ_id", string="Category", store=True)
    uom_name = fields.Char(related="product_id.uom_id.name", string="Unit")
    required_qty = fields.Float(string="Needed", digits="Product Unit of Measure")
    on_hand_qty = fields.Float(
        related="product_id.qty_available", string="In Stock",
        digits="Product Unit of Measure")
    shortfall = fields.Float(
        compute="_compute_shortfall", store=True, string="Short By",
        digits="Product Unit of Measure")
    is_short = fields.Boolean(compute="_compute_shortfall", store=True, string="Must Buy")

    @api.depends("required_qty", "on_hand_qty")
    def _compute_shortfall(self):
        for line in self:
            gap = line.required_qty - line.on_hand_qty
            line.shortfall = gap if gap > 0 else 0.0
            line.is_short = gap > 0
