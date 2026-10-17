from datetime import date, timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarOrderType(TransactionCase):
    """Delivery or pickup, when, where, and what the register charges for it.

    Every one of these is a promise the shop has to keep, so the system refuses
    a half-booked order rather than discovering the gap on the day.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.sukkar_block_oversell = False
        cls.partner = cls.env["res.partner"].create({"name": "Order Type Buyer"})
        cls.cake = cls.env["product.product"].create({
            "name": "Order Type Cake", "type": "consu", "list_price": 30.0})
        Slot = cls.env["sukkar.delivery.slot"]
        cls.slot = Slot.search([], limit=1) or Slot.create(
            {"start_hour": 16.0, "end_hour": 18.0})
        cls.tomorrow = date.today() + timedelta(days=1)

    def _order(self, **vals):
        base = {
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {"product_id": self.cake.id,
                                   "product_uom_qty": 1})],
        }
        base.update(vals)
        return self.env["sale.order"].create(base)

    # ------------------------------------------------- no order half-booked
    def test_a_delivery_without_a_date_cannot_be_confirmed(self):
        order = self._order(sukkar_receiver_name="Noura",
                            sukkar_receiver_phone="99887766")
        with self.assertRaises(UserError) as caught:
            order.action_confirm()
        self.assertIn("time window", str(caught.exception))

    def test_a_delivery_with_a_date_but_no_window_cannot_be_confirmed(self):
        order = self._order(sukkar_receiver_name="Noura",
                            sukkar_delivery_date=self.tomorrow)
        with self.assertRaises(UserError):
            order.action_confirm()

    def test_a_pickup_needs_its_collection_appointment_too(self):
        order = self._order(sukkar_order_type="pickup")
        with self.assertRaises(UserError):
            order.action_confirm()

    def test_a_fully_booked_order_confirms(self):
        order = self._order(sukkar_order_type="pickup",
                            sukkar_delivery_date=self.tomorrow,
                            sukkar_delivery_slot_id=self.slot.id)
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_a_bare_counter_sale_is_not_asked_to_schedule(self):
        order = self._order()
        order.action_confirm()
        self.assertEqual(order.state, "sale",
                         "products and nothing else: handed over on the spot")

    def test_the_register_refuses_a_half_booked_order_as_well(self):
        config = self.env["pos.config"].search([], limit=1)
        session = self.env["pos.session"].create(
            {"config_id": config.id, "user_id": self.env.uid})
        with self.assertRaises(ValidationError):
            self.env["pos.order"].create({
                "session_id": session.id,
                "partner_id": self.partner.id,
                "amount_tax": 0.0, "amount_total": 30.0,
                "amount_paid": 30.0, "amount_return": 0.0,
                "sukkar_order_type": "delivery",
                "lines": [(0, 0, {
                    "product_id": self.cake.id, "qty": 1, "price_unit": 30.0,
                    "price_subtotal": 30.0, "price_subtotal_incl": 30.0})],
            })

    # ------------------------------------------------- a pickup is not driven
    def test_a_pickup_is_not_asked_for_a_driver(self):
        order = self._order(sukkar_order_type="pickup",
                            sukkar_delivery_date=self.tomorrow,
                            sukkar_delivery_slot_id=self.slot.id)
        order.action_confirm()
        ticket = order.sukkar_production_ids
        ticket.action_picked_up()
        self.assertEqual(ticket.status, "in_delivery",
                         "the customer collects it — there is no driver to name")

    def test_a_delivery_still_is(self):
        order = self._order(sukkar_delivery_date=self.tomorrow,
                            sukkar_delivery_slot_id=self.slot.id,
                            sukkar_receiver_name="Noura")
        order.action_confirm()
        with self.assertRaises(UserError):
            order.sukkar_production_ids.action_picked_up()

    # --------------------------------------------- the whole address survives
    def test_all_eight_address_parts_travel_from_the_register(self):
        config = self.env["pos.config"].search([], limit=1)
        session = self.env["pos.session"].create(
            {"config_id": config.id, "user_id": self.env.uid})
        governorate = self.env["res.country.state"].search(
            [("country_id.code", "=", "KW")], limit=1)
        pos_order = self.env["pos.order"].create({
            "session_id": session.id,
            "partner_id": self.partner.id,
            "amount_tax": 0.0, "amount_total": 30.0,
            "amount_paid": 30.0, "amount_return": 0.0,
            "sukkar_order_type": "delivery",
            "sukkar_delivery_date": self.tomorrow,
            "sukkar_delivery_slot_id": self.slot.id,
            "sukkar_receiver_name": "Dana",
            "sukkar_receiver_phone": "99112233",
            "sukkar_addr_governorate_id": governorate.id,
            "sukkar_addr_area": "Salmiya",
            "sukkar_addr_block": "12",
            "sukkar_addr_street": "Amman",
            "sukkar_addr_avenue": "4",
            "sukkar_addr_building": "House 7",
            "sukkar_addr_floor": "3",
            "sukkar_addr_apartment": "5",
            "sukkar_addr_directions": "Blue gate next to the pharmacy",
            "lines": [(0, 0, {
                "product_id": self.cake.id, "qty": 1, "price_unit": 30.0,
                "price_subtotal": 30.0, "price_subtotal_incl": 30.0})],
        })
        pos_order._sukkar_create_delivery_order()
        sale = pos_order.sukkar_sale_order_id
        self.assertEqual(sale.sukkar_addr_governorate_id, governorate)
        self.assertEqual(sale.sukkar_addr_area, "Salmiya")
        self.assertEqual(sale.sukkar_addr_block, "12")
        self.assertEqual(sale.sukkar_addr_street, "Amman")
        self.assertEqual(sale.sukkar_addr_avenue, "4")
        self.assertEqual(sale.sukkar_addr_building, "House 7")
        self.assertEqual(sale.sukkar_addr_floor, "3")
        self.assertEqual(sale.sukkar_addr_apartment, "5")
        self.assertEqual(sale.sukkar_addr_directions,
                         "Blue gate next to the pharmacy")
        # The one line every document prints, assembled once.
        summary = sale.sukkar_address_summary
        for part in ("Salmiya", "Block 12", "Street Amman", "Avenue 4",
                     "Building House 7", "Floor 3", "Apt 5",
                     "Blue gate next to the pharmacy"):
            self.assertIn(part, summary)
        self.assertEqual(sale.sukkar_production_ids.address_summary, summary,
                         "and the kitchen ticket finally has the address")

    def test_a_pickup_prints_no_address(self):
        order = self._order(sukkar_order_type="pickup",
                            sukkar_addr_area="Salmiya",
                            sukkar_delivery_date=self.tomorrow,
                            sukkar_delivery_slot_id=self.slot.id)
        self.assertFalse(order.sukkar_address_summary,
                         "the customer comes to the branch")

    # ------------------------------------------------ the register's own fee
    def test_a_register_delivery_fee_is_its_own_line_and_in_the_total(self):
        config = self.env["pos.config"].search([], limit=1)
        session = self.env["pos.session"].create(
            {"config_id": config.id, "user_id": self.env.uid})
        fee_product = self.env["pos.order"]._sukkar_fee_product()
        pos_order = self.env["pos.order"].create({
            "session_id": session.id,
            "partner_id": self.partner.id,
            "amount_tax": 0.0, "amount_total": 33.0,
            "amount_paid": 33.0, "amount_return": 0.0,
            "sukkar_order_type": "delivery",
            "sukkar_delivery_date": self.tomorrow,
            "sukkar_delivery_slot_id": self.slot.id,
            "sukkar_receiver_name": "Dana",
            "sukkar_receiver_phone": "99112233",
            "sukkar_delivery_fee": 3.0,
            "lines": [
                (0, 0, {"product_id": self.cake.id, "qty": 1, "price_unit": 30.0,
                        "price_subtotal": 30.0, "price_subtotal_incl": 30.0}),
                (0, 0, {"product_id": fee_product.id, "qty": 1, "price_unit": 3.0,
                        "price_subtotal": 3.0, "price_subtotal_incl": 3.0}),
            ],
        })
        pos_order._sukkar_create_delivery_order()
        sale = pos_order.sukkar_sale_order_id
        fee_lines = sale.order_line.filtered("is_delivery")
        self.assertEqual(len(fee_lines), 1, "the fee is its own line, not a markup")
        self.assertEqual(fee_lines.product_id, fee_product)
        self.assertEqual(sale.sukkar_delivery_fee, 3.0)
        self.assertEqual(sale.amount_total, 33.0, "and it is in the total")
        self.assertNotIn(fee_product.display_name,
                         sale.sukkar_production_ids.line_summary,
                         "money is not something the kitchen makes")

    def test_the_fee_stays_inside_the_range_the_owner_set(self):
        config = self.env["pos.config"].search([], limit=1)
        session = self.env["pos.session"].create(
            {"config_id": config.id, "user_id": self.env.uid})
        with self.assertRaises(ValidationError):
            self.env["pos.order"].create({
                "session_id": session.id,
                "partner_id": self.partner.id,
                "amount_tax": 0.0, "amount_total": 30.0,
                "amount_paid": 30.0, "amount_return": 0.0,
                "sukkar_delivery_fee": self.company.sukkar_pos_fee_max + 1,
                "lines": [(0, 0, {
                    "product_id": self.cake.id, "qty": 1, "price_unit": 30.0,
                    "price_subtotal": 30.0, "price_subtotal_incl": 30.0})],
            })

    # ------------------------------------------------------- the card message
    def test_a_card_message_longer_than_the_card_is_refused(self):
        self.company.sukkar_card_message_max = 150
        with self.assertRaises(ValidationError) as caught:
            self._order(sukkar_gift_message="x" * 151)
        self.assertIn("150", str(caught.exception))
        self.assertIn("card message", str(caught.exception))

    def test_the_card_length_is_a_setting_not_a_number_in_the_code(self):
        self.company.sukkar_card_message_max = 200
        order = self._order(sukkar_gift_message="x" * 151)
        self.assertEqual(len(order.sukkar_gift_message), 151,
                         "raising the limit in Settings is all it takes")


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarOrderEndToEnd(TransactionCase):
    """One order, all the way through, because the breaks are at the joins.

    Register → delivery order → cake pieces held → kitchen ticket → driver →
    made → invoice. Six pieces of work built this; this walks the whole road
    once and checks nothing is dropped between them.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env["res.partner"].create({"name": "End To End Buyer"})
        cls.piece = cls.env["product.product"].create({
            "name": "E2E Cupcake", "type": "consu", "is_storable": True})
        cls.bouquet = cls.env["product.product"].create({
            "name": "E2E Bouquet", "type": "consu", "list_price": 30.0,
            "invoice_policy": "order"})
        cls.env["mrp.bom"].create({
            "product_tmpl_id": cls.bouquet.product_tmpl_id.id,
            "product_id": cls.bouquet.id,
            "product_qty": 1.0,
            "bom_line_ids": [(0, 0, {"product_id": cls.piece.id,
                                     "product_qty": 7.0})],
        })
        cls.company.write({
            "sukkar_cake_piece_product_id": cls.piece.id,
            "sukkar_block_oversell": True,
        })
        cls.red = cls.env["sukkar.cake.flavour"].create(
            {"name": "E2E Red Velvet", "name_ar": "ريد فيلفيت", "code": "ERV"})
        cls.choc = cls.env["sukkar.cake.flavour"].create(
            {"name": "E2E Chocolate", "name_ar": "شوكولاتة", "code": "ECH"})
        Slot = cls.env["sukkar.delivery.slot"]
        cls.slot = Slot.search([], limit=1) or Slot.create(
            {"start_hour": 16.0, "end_hour": 18.0})
        cls.driver = cls.env["res.users"].create({
            "name": "E2E Driver", "login": "e2e.driver", "sukkar_is_driver": True})
        cls.governorate = cls.env["res.country.state"].search(
            [("country_id.code", "=", "KW")], limit=1)

    def test_one_order_from_the_register_to_the_invoice(self):
        batch = self.env["sukkar.cake.batch"].create({
            "qty_received": 50.0, "state": "open",
            "receipt_date": date.today()})
        config = self.env["pos.config"].search([], limit=1)
        session = self.env["pos.session"].create(
            {"config_id": config.id, "user_id": self.env.uid})
        fee_product = self.env["pos.order"]._sukkar_fee_product()
        pos_order = self.env["pos.order"].create({
            "session_id": session.id,
            "partner_id": self.partner.id,
            "amount_tax": 0.0, "amount_total": 32.0,
            "amount_paid": 32.0, "amount_return": 0.0,
            "sukkar_order_type": "delivery",
            "sukkar_delivery_date": date.today() + timedelta(days=1),
            "sukkar_delivery_slot_id": self.slot.id,
            "sukkar_receiver_name": "Noura",
            "sukkar_receiver_phone": "99887766",
            "sukkar_sender_name": "Dana",
            "sukkar_sender_phone": "99112233",
            "sukkar_addr_governorate_id": self.governorate.id,
            "sukkar_addr_area": "Salmiya",
            "sukkar_addr_block": "12",
            "sukkar_addr_street": "Amman",
            "sukkar_addr_avenue": "4",
            "sukkar_addr_building": "House 7",
            "sukkar_addr_floor": "3",
            "sukkar_addr_apartment": "5",
            "sukkar_addr_directions": "Blue gate",
            "sukkar_delivery_fee": 2.0,
            "lines": [
                (0, 0, {"product_id": self.bouquet.id, "qty": 1,
                        "price_unit": 30.0, "price_subtotal": 30.0,
                        "price_subtotal_incl": 30.0,
                        "customer_note": "Happy Birthday Noura",
                        "sukkar_flavour_json":
                            '[{"flavour_id": %s, "qty": 4}, '
                            '{"flavour_id": %s, "qty": 3}]'
                            % (self.red.id, self.choc.id)}),
                (0, 0, {"product_id": fee_product.id, "qty": 1,
                        "price_unit": 2.0, "price_subtotal": 2.0,
                        "price_subtotal_incl": 2.0}),
            ],
        })

        # 1 · the register hands the order to the back office
        pos_order._sukkar_create_delivery_order()
        sale = pos_order.sukkar_sale_order_id
        self.assertEqual(sale.state, "sale")
        self.assertEqual(sale.sukkar_order_type, "delivery")
        self.assertEqual(sale.amount_total, 32.0, "bouquet plus the fee")
        self.assertIn("Apt 5", sale.sukkar_address_summary)
        self.assertIn("4 E2E Red Velvet", sale.sukkar_flavour_summary)
        self.assertEqual(sale.sukkar_piece_total, 7.0)

        # 2 · confirming it held the cake pieces, without taking them out
        self.assertEqual(batch.qty_reserved, 7.0)
        self.assertEqual(batch.qty_physical, 50.0)
        self.assertEqual(batch.qty_available, 43.0)

        # 3 · the kitchen ticket knows what to make and where it goes
        ticket = sale.sukkar_production_ids
        self.assertIn("4 E2E Red Velvet", ticket.flavour_summary)
        self.assertIn("Salmiya", ticket.address_summary)
        self.assertIn("Happy Birthday Noura", sale.sukkar_special_requests)

        # 4 · dispatch names the driver; the order agrees, so the invoice can
        ticket.driver_id = self.driver
        self.assertEqual(sale.sukkar_driver_id, self.driver)

        # 5 · made, so the pieces are finally out of stock
        done = self.env["sukkar.production.stage"].search(
            [("is_done", "=", True)], order="sequence", limit=1)
        ticket.stage_id = done.id
        self.assertEqual(batch.qty_sold, 7.0)
        self.assertEqual(batch.qty_physical, 43.0)
        self.assertEqual(batch.qty_reserved, 0.0)

        # 6 · everything the customer's paperwork has to name
        invoice = sale._create_invoices()
        self.assertEqual(invoice.sukkar_order_type, "delivery")
        self.assertEqual(invoice.sukkar_driver_id, self.driver)
        self.assertIn("Apt 5", invoice.sukkar_address_summary)
        self.assertIn("4 E2E Red Velvet", invoice.sukkar_flavour_summary)
        bouquet_line = invoice.invoice_line_ids.filtered(
            lambda l: l.product_id == self.bouquet)
        self.assertIn("4 E2E Red Velvet", bouquet_line.sukkar_flavour_summary)

    def test_a_register_sale_is_never_lost_because_the_shop_is_short(self):
        """The money is already in the drawer by the time the back office sees
        it. Refusing the order then would lose the record of a sale that has
        happened; the shortfall is written down instead."""
        batch = self.env["sukkar.cake.batch"].create({
            "qty_received": 3.0, "state": "open", "receipt_date": date.today()})
        config = self.env["pos.config"].search([], limit=1)
        session = self.env["pos.session"].create(
            {"config_id": config.id, "user_id": self.env.uid})
        pos_order = self.env["pos.order"].create({
            "session_id": session.id,
            "partner_id": self.partner.id,
            "amount_tax": 0.0, "amount_total": 30.0,
            "amount_paid": 30.0, "amount_return": 0.0,
            "sukkar_order_type": "delivery",
            "sukkar_delivery_date": date.today() + timedelta(days=1),
            "sukkar_delivery_slot_id": self.slot.id,
            "sukkar_receiver_name": "Noura",
            "sukkar_receiver_phone": "99887766",
            "lines": [(0, 0, {
                "product_id": self.bouquet.id, "qty": 1, "price_unit": 30.0,
                "price_subtotal": 30.0, "price_subtotal_incl": 30.0})],
        })
        pos_order._sukkar_create_delivery_order()
        sale = pos_order.sukkar_sale_order_id
        self.assertTrue(sale, "the sale happened — it has to be recorded")
        self.assertEqual(sale.state, "sale")
        self.assertEqual(batch.qty_available, -4.0,
                         "and the shop can see it is four pieces short")

    def test_an_override_at_the_till_is_not_demanded_again(self):
        self.env["sukkar.cake.batch"].create({
            "qty_received": 3.0, "state": "open", "receipt_date": date.today()})
        config = self.env["pos.config"].search([], limit=1)
        session = self.env["pos.session"].create(
            {"config_id": config.id, "user_id": self.env.uid})
        pos_order = self.env["pos.order"].create({
            "session_id": session.id,
            "partner_id": self.partner.id,
            "amount_tax": 0.0, "amount_total": 30.0,
            "amount_paid": 30.0, "amount_return": 0.0,
            "sukkar_order_type": "delivery",
            "sukkar_delivery_date": date.today() + timedelta(days=1),
            "sukkar_delivery_slot_id": self.slot.id,
            "sukkar_receiver_name": "Noura",
            "sukkar_receiver_phone": "99887766",
            "sukkar_oversell_approved": True,
            "lines": [(0, 0, {
                "product_id": self.bouquet.id, "qty": 1, "price_unit": 30.0,
                "price_subtotal": 30.0, "price_subtotal_incl": 30.0})],
        })
        pos_order._sukkar_create_delivery_order()
        sale = pos_order.sukkar_sale_order_id
        self.assertTrue(sale, "the manager already approved it at the counter")
        self.assertEqual(sale.state, "sale")
        self.assertTrue(sale.sukkar_oversell_approved)
