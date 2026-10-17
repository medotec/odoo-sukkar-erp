from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarNightlyStockPlan(TransactionCase):
    """The list the kitchen finds waiting every morning.

    The shop takes every order it is given; what it has to buy or make shows up
    here instead of stopping the sale. So this list has to be right, and it has
    to appear without anybody remembering to ask for it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env["res.partner"].create({"name": "Plan Customer"})
        cls.flour = cls.env["product.product"].create({
            "name": "Plan Flour", "type": "consu", "is_storable": True})
        cls.bouquet = cls.env["product.product"].create({
            "name": "Plan Bouquet", "list_price": 25.0, "sale_ok": True})
        cls.env["mrp.bom"].create({
            "product_tmpl_id": cls.bouquet.product_tmpl_id.id,
            "product_id": cls.bouquet.id,
            "product_qty": 1.0,
            "bom_line_ids": [(0, 0, {"product_id": cls.flour.id,
                                     "product_qty": 3.0})],
        })
        Slot = cls.env["sukkar.delivery.slot"]
        cls.slot = Slot.search([], limit=1) or Slot.create(
            {"start_hour": 16.0, "end_hour": 18.0})
        cls.Plan = cls.env["sukkar.stock.plan"]

    def _tomorrow(self):
        return fields.Date.context_today(self.Plan) + timedelta(days=1)

    def _order_for_tomorrow(self, qty=2):
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "sukkar_delivery_date": self._tomorrow(),
            "sukkar_delivery_slot_id": self.slot.id,
            "order_line": [(0, 0, {"product_id": self.bouquet.id,
                                   "product_uom_qty": qty})],
        })
        order.action_confirm()
        return order

    def test_the_nightly_run_writes_tomorrows_list(self):
        self._order_for_tomorrow(qty=2)
        plan = self.Plan._cron_build_tomorrow()
        self.assertEqual(plan.date, self._tomorrow())
        line = plan.line_ids.filtered(lambda l: l.product_id == self.flour)
        self.assertTrue(line, "the recipe was exploded to its ingredients")
        self.assertEqual(line.required_qty, 6.0,
                         "two bouquets at three each")

    def test_it_says_what_must_be_bought(self):
        self._order_for_tomorrow(qty=2)
        plan = self.Plan._cron_build_tomorrow()
        line = plan.line_ids.filtered(lambda l: l.product_id == self.flour)
        self.assertTrue(line.is_short, "nothing on the shelf, so it must be bought")
        self.assertEqual(line.shortfall, 6.0)
        self.assertEqual(plan.shortage_count, 1)

    def test_running_twice_does_not_make_two_lists(self):
        self._order_for_tomorrow()
        first = self.Plan._cron_build_tomorrow()
        second = self.Plan._cron_build_tomorrow()
        self.assertEqual(first, second, "the same day is recalculated, not duplicated")
        self.assertEqual(
            len(self.Plan.search([("date", "=", self._tomorrow())])), 1)

    def test_a_shortage_never_stops_an_order(self):
        # The owner reversed the refusal on 20 Sep: take the order, buy the cake.
        self.assertFalse(self.company.sukkar_block_oversell)
        order = self._order_for_tomorrow(qty=50)
        self.assertEqual(order.state, "sale", "a big order is still taken")
        plan = self.Plan._cron_build_tomorrow()
        line = plan.line_ids.filtered(lambda l: l.product_id == self.flour)
        self.assertEqual(line.required_qty, 150.0)
        self.assertTrue(line.is_short)

    def test_the_hour_is_the_owners_to_set(self):
        cron = self.env.ref("sukkar.cron_daily_stock_plan")
        self.company.sukkar_stock_plan_hour = 21
        moved = fields.Datetime.context_timestamp(self.company, cron.nextcall)
        self.assertEqual(moved.hour, 21,
                         "the job runs at the hour set in Settings, shop time")
