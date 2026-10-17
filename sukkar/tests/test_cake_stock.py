from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarCakeStock(TransactionCase):
    """Daily cake batches, the five piece states, reservation, and the block on
    confirming beyond what is available.

    The numbers here are the ones that would cost the shop money if they drifted:
    decorating must never change how much cake exists, wasting must, and the same
    pieces must never be promised to two orders.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env["res.partner"].create({"name": "Cake Stock Customer"})
        cls.piece = cls.env["product.product"].create({
            "name": "Test Cupcake", "type": "consu", "is_storable": True})
        cls.bouquet = cls.env["product.product"].create({
            "name": "Test 7-piece Bouquet", "list_price": 20.0, "sale_ok": True})
        cls.env["mrp.bom"].create({
            "product_tmpl_id": cls.bouquet.product_tmpl_id.id,
            "product_id": cls.bouquet.id,
            "product_qty": 1.0,
            "bom_line_ids": [(0, 0, {"product_id": cls.piece.id, "product_qty": 7.0})],
        })
        cls.company.write({
            "sukkar_cake_piece_product_id": cls.piece.id,
            "sukkar_block_oversell": True,
            "sukkar_reserve_horizon_days": 1,
        })
        cls.Batch = cls.env["sukkar.cake.batch"]
        cls.Move = cls.env["sukkar.cake.move"]

    # ------------------------------------------------------------------ helpers
    def _batch(self, received=50.0, **vals):
        base = {"qty_received": received, "state": "open",
                "receipt_date": fields.Date.today()}
        base.update(vals)
        return self.Batch.create(base)

    def _order(self, qty=1, **vals):
        base = {"partner_id": self.partner.id,
                "order_line": [(0, 0, {"product_id": self.bouquet.id,
                                       "product_uom_qty": qty})]}
        base.update(vals)
        return self.env["sale.order"].create(base)

    # ------------------------------------------------------------ K1 / K3 basics
    def test_piece_count_comes_from_the_recipe(self):
        self.assertEqual(self.bouquet.sukkar_piece_count, 7.0)
        self.assertEqual(self.piece.sukkar_piece_count, 1.0)

    def test_order_pieces_adds_the_lines_up(self):
        order = self._order(qty=3)
        self.assertEqual(self.Batch._sukkar_order_pieces(order), 21.0)

    def test_batch_is_numbered_and_counts_what_arrived(self):
        batch = self._batch(received=50.0)
        self.assertTrue(batch.name.startswith("CB"))
        self.assertEqual(batch.qty_physical, 50.0)
        self.assertEqual(batch.qty_available, 50.0)
        self.assertEqual(batch.qty_undecorated, 50.0)

    # ------------------------------------------------------------------- K2 states
    def test_decorating_does_not_change_physical_stock(self):
        batch = self._batch(received=50.0)
        batch.action_sukkar_decorate()
        self.Move.create({"batch_id": batch.id, "qty": 20.0, "move_type": "decorate",
                          "state_from": "undecorated", "state_to": "decorated"})
        self.assertEqual(batch.qty_decorated, 20.0)
        self.assertEqual(batch.qty_undecorated, 30.0)
        self.assertEqual(batch.qty_physical, 50.0, "the same cake in another place")

    def test_waste_does_reduce_physical_stock(self):
        batch = self._batch(received=50.0)
        self.Move.create({"batch_id": batch.id, "qty": 5.0, "move_type": "waste",
                          "state_from": "available", "state_to": "waste",
                          "reason": "Dropped"})
        self.assertEqual(batch.qty_waste, 5.0)
        self.assertEqual(batch.qty_physical, 45.0)
        self.assertEqual(batch.qty_available, 45.0)

    def test_the_two_locations_always_add_up_to_physical(self):
        batch = self._batch(received=50.0)
        self.Move.create({"batch_id": batch.id, "qty": 30.0, "move_type": "decorate",
                          "state_from": "undecorated", "state_to": "decorated"})
        self.Move.create({"batch_id": batch.id, "qty": 8.0, "move_type": "waste",
                          "state_from": "available", "state_to": "waste"})
        self.assertEqual(batch.qty_undecorated + batch.qty_decorated,
                         batch.qty_physical)

    # -------------------------------------------------------- K4 reserve at confirm
    def test_confirming_holds_the_pieces_without_taking_them_out(self):
        batch = self._batch(received=50.0)
        order = self._order(qty=2)
        order.action_confirm()
        self.assertEqual(batch.qty_reserved, 14.0)
        self.assertEqual(batch.qty_physical, 50.0, "reserved cake is still here")
        self.assertEqual(batch.qty_available, 36.0, "but it cannot be sold again")

    def test_pieces_are_taken_from_the_oldest_batch_first(self):
        old = self._batch(received=10.0,
                          receipt_date=fields.Date.today() - timedelta(days=2))
        new = self._batch(received=50.0)
        self._order(qty=2).action_confirm()
        self.assertEqual(old.qty_reserved, 10.0)
        self.assertEqual(new.qty_reserved, 4.0)

    def test_confirming_twice_does_not_hold_twice(self):
        batch = self._batch(received=50.0)
        order = self._order(qty=2)
        order.action_confirm()
        self.Batch._sukkar_reserve_for_orders(order)
        self.assertEqual(batch.qty_reserved, 14.0)

    def test_production_uses_what_was_held_and_nothing_twice(self):
        batch = self._batch(received=50.0)
        order = self._order(qty=2)
        order.action_confirm()
        self.Batch._sukkar_consume_for_order(order)
        self.Batch._sukkar_consume_for_order(order)
        self.assertEqual(batch.qty_sold, 14.0)
        self.assertEqual(batch.qty_reserved, 0.0)
        self.assertEqual(batch.qty_physical, 36.0)

    def test_finishing_the_kitchen_ticket_uses_the_pieces_up(self):
        batch = self._batch(received=50.0)
        order = self._order(qty=2)
        order.action_confirm()
        self.assertEqual(batch.qty_reserved, 14.0, "held, not yet taken")
        ticket = order.sukkar_production_ids
        done = self.env["sukkar.production.stage"].search(
            [("is_done", "=", True)], order="sequence", limit=1)
        ticket.stage_id = done.id
        self.assertEqual(batch.qty_sold, 14.0, "made, so the pieces are gone")
        self.assertEqual(batch.qty_reserved, 0.0, "and the hold is spent")
        self.assertEqual(batch.qty_physical, 36.0)

    def test_the_order_shows_how_many_pieces_it_takes(self):
        order = self._order(qty=3)
        self.assertEqual(order.order_line.sukkar_piece_qty, 21.0)
        self.assertEqual(order.sukkar_piece_total, 21.0)

    def test_a_cancelled_order_gives_its_pieces_back(self):
        batch = self._batch(received=50.0)
        order = self._order(qty=2)
        order.action_confirm()
        order._action_cancel()
        self.assertEqual(batch.qty_reserved, 0.0)
        self.assertEqual(batch.qty_available, 50.0)

    def test_an_order_for_next_month_does_not_hold_today_stock(self):
        batch = self._batch(received=50.0)
        slot = self.env["sukkar.delivery.slot"].search([], limit=1)
        order = self._order(
            qty=2, sukkar_delivery_date=fields.Date.today() + timedelta(days=30),
            sukkar_delivery_slot_id=slot.id)
        order.action_confirm()
        self.assertEqual(batch.qty_reserved, 0.0)

    # ------------------------------------------------------- K5 block overselling
    def test_confirming_beyond_available_is_refused_with_both_numbers(self):
        self._batch(received=20.0)
        order = self._order(qty=4)  # 28 pieces
        with self.assertRaises(UserError) as caught:
            order.action_confirm()
        message = str(caught.exception)
        self.assertIn("20", message)
        self.assertIn("28", message)
        # English only since 10 Oct 2026 (owner: no Arabic in the back office).
        self.assertIn("Insufficient cake stock", message)

    def test_the_block_is_a_setting(self):
        self.company.sukkar_block_oversell = False
        self._batch(received=20.0)
        order = self._order(qty=4)
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_no_cake_piece_product_means_no_blocking(self):
        self.company.sukkar_cake_piece_product_id = False
        order = self._order(qty=4)
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_expired_pieces_are_not_counted_as_available(self):
        self._batch(received=50.0,
                    expiry_date=fields.Date.today() - timedelta(days=1))
        self.assertEqual(self.Batch._sukkar_available_pieces(self.company), 0.0)
        with self.assertRaises(UserError):
            self._order(qty=1).action_confirm()

    # ------------------------------------------------------------- K5 the override
    def test_manager_override_records_who_when_and_why_then_confirms(self):
        batch = self._batch(received=20.0)
        order = self._order(qty=4)
        wizard = self.env["sukkar.oversell.wizard"].with_context(
            active_model="sale.order", active_id=order.id).create({
                "reason": "Supplier delivery arrives at 4pm"})
        self.assertEqual(wizard.qty_needed, 28.0)
        self.assertEqual(wizard.qty_available, 20.0)
        wizard.action_confirm()
        override = order.sukkar_override_ids
        self.assertEqual(len(override), 1)
        self.assertTrue(override.name.startswith("OVR"))
        self.assertEqual(override.manager_id, self.env.user)
        self.assertTrue(override.override_datetime)
        self.assertEqual(override.reason, "Supplier delivery arrives at 4pm")
        self.assertEqual(override.qty_needed, 28.0)
        self.assertEqual(override.qty_available, 20.0)
        self.assertTrue(order.sukkar_oversell_approved)
        self.assertEqual(order.state, "sale")
        self.assertEqual(batch.qty_reserved, 28.0,
                         "the promise is recorded even though the shop is short")
        self.assertEqual(batch.qty_available, -8.0)

    def test_an_override_without_an_order_says_so(self):
        wizard = self.env["sukkar.oversell.wizard"].create({"reason": "No order"})
        with self.assertRaises(UserError):
            wizard.action_confirm()

    # ------------------------------------------------------------- K6 the figures
    def test_the_two_figures_are_what_the_owner_asked_for(self):
        batch = self._batch(received=40.0, qty_opening=10.0)
        self.Move.create({"batch_id": batch.id, "qty": 5.0, "move_type": "waste",
                          "state_from": "available", "state_to": "waste"})
        self._order(qty=2).action_confirm()
        # Opening 10 + Received 40 − Sold 0 − Waste 5 = 45 physically here
        self.assertEqual(batch.qty_physical, 45.0)
        # 45 − 14 promised to tomorrow's order = 31 a new order may take
        self.assertEqual(batch.qty_available, 31.0)

    # -------------------------------------------- the hold follows the order
    def test_shrinking_a_confirmed_order_gives_the_pieces_back(self):
        """An order edited after it is confirmed is the normal case. The hold
        never came back, so the shop slowly stopped being able to sell cake that
        was sitting in the fridge."""
        batch = self._batch(received=50.0)
        order = self._order(qty=2)
        order.action_confirm()
        self.assertEqual(batch.qty_reserved, 14.0)
        order.order_line.product_uom_qty = 1
        self.assertEqual(batch.qty_reserved, 7.0, "seven pieces given back")
        self.assertEqual(batch.qty_available, 43.0)

    def test_dropping_the_line_altogether_releases_everything(self):
        batch = self._batch(received=50.0)
        order = self._order(qty=2)
        order.action_confirm()
        order.order_line.product_uom_qty = 0
        self.assertEqual(batch.qty_reserved, 0.0)
        self.assertEqual(batch.qty_available, 50.0)

    def test_growing_a_confirmed_order_takes_the_extra_pieces(self):
        batch = self._batch(received=50.0)
        order = self._order(qty=1)
        order.action_confirm()
        self.assertEqual(batch.qty_reserved, 7.0)
        order.order_line.product_uom_qty = 5
        self.assertEqual(batch.qty_reserved, 35.0,
                         "the gate reads Available, so a grown order must hold more")
        self.assertEqual(batch.qty_available, 15.0)

    def test_cancelling_and_then_deleting_the_order_keeps_the_pieces_free(self):
        """Tidying up cancelled orders is an obvious thing for office staff to
        do; it used to take the cake off the shelf on paper, permanently."""
        batch = self._batch(received=50.0)
        order = self._order(qty=2)
        order.action_confirm()
        order._action_cancel()
        self.assertEqual(batch.qty_available, 50.0)
        order.unlink()
        batch.invalidate_recordset()
        self.assertEqual(batch.qty_available, 50.0,
                         "deleting the order must not bring the hold back to life")

    def test_an_expired_batch_does_not_hide_what_is_still_promised(self):
        """Overnight the batch passes its Use By date. The cake is written off;
        the order promised against it is not, so it has to come out of the
        fresh batch instead of quietly out of nothing."""
        today = fields.Date.today()
        old = self._batch(received=10.0, receipt_date=today - timedelta(days=1),
                          expiry_date=today + timedelta(days=1))
        order = self._order(qty=1)
        order.action_confirm()
        self.assertEqual(old.qty_reserved, 7.0)
        # Morning: yesterday's batch is past its Use By, a fresh one arrives.
        old.expiry_date = today - timedelta(days=1)
        fresh = self._batch(received=10.0)
        self.assertEqual(self.Batch._sukkar_available_pieces(self.company), 3.0,
                         "the seven pieces are still promised to that order")
        # A second order for the same day must be refused, not silently taken.
        with self.assertRaises(UserError):
            self._order(qty=1).action_confirm()
        self.assertEqual(fresh.qty_physical, 10.0)

    def test_moving_pieces_between_states_never_changes_the_count(self):
        """The button is called Move Pieces. It used to ADD them: every tidy-up
        raised the count the order gate reads."""
        batch = self._batch(received=100.0)
        self.Move.create({"batch_id": batch.id, "qty": 40.0, "move_type": "decorate",
                          "state_from": "undecorated", "state_to": "decorated"})
        action = batch.action_sukkar_move_pieces()
        self.assertEqual(action["context"]["default_move_type"], "move")
        self.Move.create({"batch_id": batch.id, "qty": 10.0, "move_type": "move",
                          "state_from": "decorated", "state_to": "undecorated"})
        self.assertEqual(batch.qty_physical, 100.0, "the same cake, another shelf")
        self.assertEqual(batch.qty_available, 100.0)
        self.assertEqual(batch.qty_decorated, 30.0)
        self.assertEqual(batch.qty_undecorated, 70.0)

    def test_a_move_without_both_ends_is_refused(self):
        batch = self._batch(received=20.0)
        with self.assertRaises(Exception):
            self.Move.create({"batch_id": batch.id, "qty": 5.0,
                              "move_type": "move", "state_to": "undecorated"})
