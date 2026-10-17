from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarCodOverdue(TransactionCase):
    """Cash the driver has not brought back yet.

    A cash-on-delivery order is money owed to the shop from the day it was due
    at the customer's door. After the number of days the owner set, it stops
    being in transit and starts being chased.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.sukkar_block_oversell = False
        cls.company.sukkar_cod_overdue_days = 2
        cls.partner = cls.env["res.partner"].create({"name": "COD Buyer"})
        cls.cake = cls.env["product.product"].create({
            "name": "COD Cake", "type": "consu", "list_price": 20.0})
        Slot = cls.env["sukkar.delivery.slot"]
        cls.slot = Slot.search([], limit=1) or Slot.create(
            {"start_hour": 16.0, "end_hour": 18.0})

    def _today(self):
        # The ageing counts Kuwait calendar days, so the test must build its
        # dates in Kuwait too — a UTC date is a day behind from midnight to 3am.
        return fields.Date.context_today(self.env["sale.order"])

    def _cod_order(self, days_ago, **vals):
        base = {
            "partner_id": self.partner.id,
            "sukkar_payment_method": "cod",
            "sukkar_delivery_date": self._today() - timedelta(days=days_ago),
            "sukkar_delivery_slot_id": self.slot.id,
            "order_line": [(0, 0, {"product_id": self.cake.id,
                                   "product_uom_qty": 1})],
        }
        base.update(vals)
        order = self.env["sale.order"].create(base)
        order.action_confirm()
        return order

    def test_a_fresh_delivery_is_not_overdue(self):
        order = self._cod_order(days_ago=1)
        self.assertEqual(order.sukkar_cod_days_outstanding, 1)
        self.assertFalse(order.sukkar_cod_overdue)

    def test_on_the_last_allowed_day_it_is_still_not_overdue(self):
        order = self._cod_order(days_ago=2)
        self.assertEqual(order.sukkar_cod_days_outstanding, 2)
        self.assertFalse(order.sukkar_cod_overdue,
                         "two days is the allowance, not the breach")

    def test_past_the_allowance_it_is_overdue(self):
        order = self._cod_order(days_ago=3)
        self.assertEqual(order.sukkar_cod_days_outstanding, 3)
        self.assertTrue(order.sukkar_cod_overdue)

    def test_the_number_of_days_is_a_setting(self):
        order = self._cod_order(days_ago=3)
        self.company.sukkar_cod_overdue_days = 7
        order.invalidate_recordset(
            ["sukkar_cod_overdue", "sukkar_cod_days_outstanding"])
        self.assertFalse(order.sukkar_cod_overdue,
                         "the owner widened the allowance in Settings")

    def test_money_already_in_is_never_chased(self):
        order = self._cod_order(days_ago=10, sukkar_paid=True)
        self.assertFalse(order.sukkar_cod_overdue)
        self.assertEqual(order.sukkar_cod_days_outstanding, 0)

    def test_a_card_order_is_not_a_cash_collection(self):
        order = self._cod_order(days_ago=10, sukkar_payment_method="knet")
        self.assertFalse(order.sukkar_cod_overdue)

    def test_a_draft_order_owes_nothing_yet(self):
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "sukkar_payment_method": "cod",
            "sukkar_delivery_date": self._today() - timedelta(days=10),
            "sukkar_delivery_slot_id": self.slot.id,
            "order_line": [(0, 0, {"product_id": self.cake.id,
                                   "product_uom_qty": 1})],
        })
        self.assertFalse(order.sukkar_cod_overdue)

    # ------------------------------------------------------------ the filter
    def test_the_overdue_filter_actually_finds_them(self):
        overdue = self._cod_order(days_ago=5)
        fresh = self._cod_order(days_ago=1)
        found = self.env["sale.order"].search([("sukkar_cod_overdue", "=", True)])
        self.assertIn(overdue, found)
        self.assertNotIn(fresh, found)
        not_found = self.env["sale.order"].search(
            [("sukkar_cod_overdue", "=", False)])
        self.assertIn(fresh, not_found)
        self.assertNotIn(overdue, not_found)

    def test_the_orders_screen_shows_the_ageing(self):
        order = self._cod_order(days_ago=5)
        ticket = order.sukkar_production_ids
        self.assertTrue(ticket.cod_overdue)
        self.assertEqual(ticket.cod_days_outstanding, 5)
        found = self.env["sukkar.production"].search(
            [("sale_order_id.sukkar_cod_overdue", "=", True)])
        self.assertIn(ticket, found, "the Cash Overdue filter on Kitchen ▸ Orders")
