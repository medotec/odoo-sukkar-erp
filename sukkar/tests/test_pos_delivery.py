from datetime import date, timedelta

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarPosDelivery(TransactionCase):
    """A register sale flagged as a delivery must open the normal pipeline:
    confirmed sale order → kitchen ticket → driver — with the money already
    marked collected, and without ever double-creating."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["pos.config"].search([], limit=1)
        cls.session = cls.env["pos.session"].create({
            "config_id": cls.config.id, "user_id": cls.env.uid})
        cls.partner = cls.env["res.partner"].create({"name": "POS Buyer"})
        cls.product = cls.env["product.product"].create({
            "name": "POS Delivery Cake", "type": "consu", "list_price": 12.0,
            "is_storable": True})
        Slot = cls.env["sukkar.delivery.slot"]
        cls.slot = Slot.search([], limit=1) or Slot.create(
            {"start_hour": 12.0, "end_hour": 14.0})
        cls.driver = cls.env["res.users"].create({
            "name": "POS Driver", "login": "pos.delivery.driver",
            "sukkar_is_driver": True})

    def _pos_order(self, **extra):
        return self.env["pos.order"].create({
            "session_id": self.session.id,
            "partner_id": self.partner.id,
            "amount_tax": 0.0, "amount_total": 12.0,
            "amount_paid": 12.0, "amount_return": 0.0,
            "lines": [(0, 0, {
                "product_id": self.product.id, "qty": 1,
                "price_unit": 12.0, "price_subtotal": 12.0,
                "price_subtotal_incl": 12.0,
                "customer_note": "Happy Birthday Noura",
            })],
            **extra,
        })

    def test_delivery_pos_order_opens_the_pipeline(self):
        pos_order = self._pos_order(
            sukkar_delivery_date=date.today() + timedelta(days=1),
            sukkar_delivery_slot_id=self.slot.id,
            sukkar_receiver_name="Noura",
            sukkar_receiver_phone="99887766",
            sukkar_driver_id=self.driver.id,
        )
        pos_order._sukkar_create_delivery_order()
        sale = pos_order.sukkar_sale_order_id
        self.assertTrue(sale, "a delivery POS order must create a sale order")
        self.assertEqual(sale.state, "sale", "…confirmed, so the kitchen ticket exists")
        self.assertEqual(sale.sukkar_delivery_slot_id, self.slot)
        self.assertEqual(sale.sukkar_receiver_name, "Noura")
        self.assertTrue(sale.sukkar_paid, "the register already collected the money")
        self.assertIn("Happy Birthday", sale.sukkar_special_requests,
                      "card text must reach the kitchen")
        ticket = sale.sukkar_production_ids
        self.assertTrue(ticket, "kitchen ticket created")
        self.assertEqual(ticket.driver_id, self.driver, "driver assigned from the register")

        before = self.env["sale.order"].search_count([])
        pos_order._sukkar_create_delivery_order()
        self.assertEqual(self.env["sale.order"].search_count([]), before,
                         "running twice must never create a second order")

    def test_an_address_without_a_governorate_still_reaches_the_driver(self):
        """The governorate dropdown is optional at the register. Reading it as
        'is there an address at all' sent the receiver's area, block and street
        nowhere: the driver receipt printed the walk-in customer's name."""
        pos_order = self._pos_order(
            sukkar_delivery_date=date.today() + timedelta(days=1),
            sukkar_delivery_slot_id=self.slot.id,
            sukkar_order_type="delivery",
            sukkar_receiver_name="Noura",
            sukkar_receiver_phone="99887766",
            sukkar_addr_area="Salmiya",
            sukkar_addr_block="10",
            sukkar_addr_street="Amman",
            sukkar_addr_building="7",
        )
        pos_order._sukkar_create_delivery_order()
        sale = pos_order.sukkar_sale_order_id
        self.assertEqual(sale.sukkar_deliver_to, "other")
        shipping = sale.partner_shipping_id
        self.assertNotEqual(shipping, sale.partner_id,
                            "the gift does not go to the payer's own house")
        self.assertEqual(shipping.city, "Salmiya")
        self.assertIn("Block 10", shipping.street or "")
        self.assertIn("Building 7", shipping.street2 or "")

    def test_a_pickup_never_becomes_a_delivery_address(self):
        pos_order = self._pos_order(
            sukkar_delivery_date=date.today() + timedelta(days=1),
            sukkar_delivery_slot_id=self.slot.id,
            sukkar_order_type="pickup",
            sukkar_addr_area="Salmiya",
        )
        pos_order._sukkar_create_delivery_order()
        sale = pos_order.sukkar_sale_order_id
        self.assertEqual(sale.sukkar_deliver_to, "sender")
        self.assertEqual(sale.partner_shipping_id, sale.partner_id)

    def test_a_register_sale_does_not_raise_a_second_delivery_order(self):
        """The register ships the goods at the counter. The mirror order used to
        raise a WH/OUT transfer for the same cakes that nobody ever validated."""
        pos_order = self._pos_order(
            sukkar_delivery_date=date.today() + timedelta(days=1),
            sukkar_delivery_slot_id=self.slot.id,
            sukkar_order_type="delivery",
            sukkar_receiver_name="Noura", sukkar_receiver_phone="99887766",
        )
        pos_order._sukkar_create_delivery_order()
        sale = pos_order.sukkar_sale_order_id
        self.assertEqual(sale.sukkar_pos_order_id, pos_order)
        if "picking_ids" in sale._fields:
            self.assertFalse(
                sale.picking_ids,
                "the goods already left with the customer, once")

    def test_a_register_sale_awards_its_loyalty_points_once(self):
        """The register awards them on validation; the mirror order is the same
        sale written down again for the kitchen, and used to pay them twice —
        roughly 5% of till turnover handed back as discounts."""
        if "coupon_point_ids" not in self.env["sale.order"]._fields:
            self.skipTest("sale_loyalty is not installed")
        programme = self.env["loyalty.program"].create({
            "name": "Grill Rewards", "program_type": "loyalty",
            "trigger": "auto", "applies_on": "both",
            "rule_ids": [(0, 0, {"reward_point_mode": "money",
                                 "reward_point_amount": 1.0})],
            "reward_ids": [(0, 0, {"reward_type": "discount",
                                   "discount": 5.0, "required_points": 100.0})],
        })
        card = self.env["loyalty.card"].create({
            "program_id": programme.id, "partner_id": self.partner.id,
            "points": 100.0})
        pos_order = self._pos_order(
            sukkar_delivery_date=date.today() + timedelta(days=1),
            sukkar_delivery_slot_id=self.slot.id,
            sukkar_order_type="delivery",
            sukkar_receiver_name="Noura", sukkar_receiver_phone="99887766",
        )
        pos_order._sukkar_create_delivery_order()
        sale = pos_order.sukkar_sale_order_id
        self.assertEqual(sale.state, "sale")
        self.assertFalse(sale.coupon_point_ids,
                         "no second helping of points for one sale")
        card.invalidate_recordset()
        self.assertAlmostEqual(card.points, 100.0, places=2,
                               msg="the register already gave the points")
        # Control: an ordinary back-office order for the same customer still
        # earns its points, so the programme really is live in this test.
        office = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {"product_id": self.product.id,
                                   "product_uom_qty": 1, "price_unit": 12.0})]})
        office.action_confirm()
        self.assertTrue(office.coupon_point_ids,
                        "a normal sale order must still earn loyalty points")

    def test_the_card_text_goes_on_the_card_not_into_the_kitchen_alarm(self):
        card_category = self.env["pos.category"].create(
            {"name": "Cards & Toppers"})
        card = self.env["product.product"].create({
            "name": "Greeting Card", "type": "consu", "list_price": 0.25,
            "pos_categ_ids": [(6, 0, [card_category.id])]})
        pos_order = self._pos_order(
            sukkar_delivery_date=date.today() + timedelta(days=1),
            sukkar_delivery_slot_id=self.slot.id,
            sukkar_order_type="delivery",
            sukkar_receiver_name="Noura", sukkar_receiver_phone="99887766",
        )
        self.env["pos.order.line"].create({
            "order_id": pos_order.id, "product_id": card.id, "qty": 1,
            "price_unit": 0.25, "price_subtotal": 0.25,
            "price_subtotal_incl": 0.25,
            "customer_note": "Happy Birthday Noura, love Dana"})
        pos_order._sukkar_create_delivery_order()
        sale = pos_order.sukkar_sale_order_id
        self.assertEqual(sale.sukkar_gift_message,
                         "Happy Birthday Noura, love Dana")
        self.assertNotIn("love Dana", sale.sukkar_special_requests or "",
                         "the private message is not a kitchen instruction")
        self.assertIn("Happy Birthday Noura", sale.sukkar_special_requests or "",
                      "the cake line's own note still reaches the kitchen")

    def test_refunding_the_whole_sale_cancels_the_cake(self):
        """Money back at the register used to leave the order confirmed: the
        kitchen kept making it, the driver kept a run for it and its cake pieces
        stayed held."""
        pos_order = self._pos_order(
            sukkar_delivery_date=date.today() + timedelta(days=1),
            sukkar_delivery_slot_id=self.slot.id,
            sukkar_order_type="delivery",
            sukkar_receiver_name="Noura", sukkar_receiver_phone="99887766",
        )
        pos_order._sukkar_create_delivery_order()
        sale = pos_order.sukkar_sale_order_id
        self.assertEqual(sale.state, "sale")
        refund = self.env["pos.order"].create({
            "session_id": self.session.id,
            "partner_id": self.partner.id,
            "amount_tax": 0.0, "amount_total": -12.0,
            "amount_paid": -12.0, "amount_return": 0.0,
            "lines": [(0, 0, {
                "product_id": self.product.id, "qty": -1,
                "price_unit": 12.0, "price_subtotal": -12.0,
                "price_subtotal_incl": -12.0,
                "refunded_orderline_id": pos_order.lines[0].id,
            })],
        })
        refund._sukkar_cancel_refunded_delivery()
        self.assertEqual(sale.state, "cancel",
                         "a refunded sale is not made and not delivered")

    def test_a_partial_refund_leaves_the_order_alone(self):
        """Half the order handed back is a change to the order, not a
        cancellation — that call belongs to the office."""
        pos_order = self._pos_order(
            sukkar_delivery_date=date.today() + timedelta(days=1),
            sukkar_delivery_slot_id=self.slot.id,
            sukkar_order_type="delivery",
            sukkar_receiver_name="Noura", sukkar_receiver_phone="99887766",
        )
        pos_order.lines[0].qty = 2
        pos_order._sukkar_create_delivery_order()
        sale = pos_order.sukkar_sale_order_id
        refund = self.env["pos.order"].create({
            "session_id": self.session.id,
            "partner_id": self.partner.id,
            "amount_tax": 0.0, "amount_total": -12.0,
            "amount_paid": -12.0, "amount_return": 0.0,
            "lines": [(0, 0, {
                "product_id": self.product.id, "qty": -1,
                "price_unit": 12.0, "price_subtotal": -12.0,
                "price_subtotal_incl": -12.0,
                "refunded_orderline_id": pos_order.lines[0].id,
            })],
        })
        refund._sukkar_cancel_refunded_delivery()
        self.assertEqual(sale.state, "sale")

    def test_counter_sale_stays_a_counter_sale(self):
        pos_order = self._pos_order()
        pos_order._sukkar_create_delivery_order()
        self.assertFalse(pos_order.sukkar_sale_order_id,
                         "no delivery details → no sale order, no ticket")


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarKitchenIngredients(TransactionCase):
    """The wall tile's ingredients list: one level of recipe, aggregated."""

    def test_components_summary_lists_recipe(self):
        partner = self.env["res.partner"].create({"name": "BoM Buyer"})
        component = self.env["product.product"].create(
            {"name": "Test Cupcake", "type": "consu"})
        finished = self.env["product.product"].create(
            {"name": "Test Mini Bouquet", "type": "consu", "list_price": 5.0})
        self.env["mrp.bom"].create({
            "product_tmpl_id": finished.product_tmpl_id.id,
            "product_qty": 1,
            "bom_line_ids": [(0, 0, {"product_id": component.id, "product_qty": 7})],
        })
        order = self.env["sale.order"].create({
            "partner_id": partner.id,
            "order_line": [(0, 0, {"product_id": finished.id, "product_uom_qty": 2})],
        })
        order.action_confirm()
        summary = order.sukkar_production_ids.components_summary
        self.assertIn("Test Cupcake", summary)
        self.assertIn("14", summary, "7 per unit × qty 2 = 14, aggregated")

    def test_order_preview_shows_photos_and_card_text(self):
        partner = self.env["res.partner"].create({"name": "Preview Buyer"})
        cake = self.env["product.product"].create(
            {"name": "Preview Cake", "type": "consu", "list_price": 9.0})
        order = self.env["sale.order"].create({
            "partner_id": partner.id,
            "sukkar_gift_message": "To Noura with love",
            "order_line": [(0, 0, {"product_id": cake.id, "product_uom_qty": 1})],
        })
        order.action_confirm()
        html = order.sukkar_production_ids.order_preview_html
        self.assertIn("/web/image/product.product/%s" % cake.id, html,
                      "each line must carry its product photo")
        self.assertIn("To Noura with love", html,
                      "the card statement must be on the preview")


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarPosLoading(TransactionCase):
    """What the register is sent at startup.

    The till once hung on its loading screen because an order reached it
    stripped of its payments. Odoo sends an order's fields as an empty list,
    which means "send them all"; naming any field turns that into a short list
    and everything unnamed is dropped. These tests keep that from happening
    again, quietly, on a Saturday.
    """

    def test_order_fields_are_not_restricted(self):
        config = self.env["pos.config"].search([], limit=1)
        fields = self.env["pos.order"]._load_pos_data_fields(config.id)
        self.assertEqual(
            fields, [],
            "The register must receive every field of an order. Listing fields "
            "here drops the rest — payments included — and the till will not "
            "finish loading.")

    def test_models_with_a_real_list_still_carry_ours(self):
        config = self.env["pos.config"].search([], limit=1)
        line_fields = self.env["pos.order.line"]._load_pos_data_fields(config.id)
        company_fields = self.env["res.company"]._load_pos_data_fields(config.id)
        # These two DO publish a list of their own, so ours must be added to it.
        for field in ("sukkar_flavour_json", "sukkar_flavour_summary"):
            self.assertIn(field, line_fields)
        for field in ("sukkar_card_message_max", "sukkar_default_flavour_id"):
            self.assertIn(field, company_fields)
        self.assertIn("payment_ids", self.env["pos.order"]._fields,
                      "sanity: the field the register was missing exists")

    def test_register_gets_the_arabic_names(self):
        """The register leads with Arabic (layout 2, 29 Sep 2026): products and
        categories bring their Arabic name, read from the translation."""
        config = self.env["pos.config"].search([], limit=1)
        product_fields = self.env["product.product"]._load_pos_data_fields(config.id)
        self.assertIn("sukkar_name_ar", product_fields)
        self.assertIn("sukkar_variant_ar", product_fields)
        self.assertIn("sukkar_name_ar",
                      self.env["pos.category"]._load_pos_data_fields(config.id))

        arabic = self.env["res.lang"]._sukkar_arabic_code()
        if not arabic:
            self.skipTest("Arabic is not installed in this database")
        english = self.env(context=dict(self.env.context, lang="en_US"))

        product = english["product.product"].create({"name": "Test Rose Box"})
        product.with_context(lang=arabic).name = "بوكس ورد تجريبي"
        self.assertEqual(product.sukkar_name_ar, "بوكس ورد تجريبي")

        # Nothing written in Arabic yet: nothing, rather than the English twice.
        plain = english["product.product"].create({"name": "Untranslated Thing"})
        self.assertFalse(plain.sukkar_name_ar)

        category = english["pos.category"].create({"name": "Test Shelf"})
        category.with_context(lang=arabic).name = "رف تجريبي"
        self.assertEqual(category.sukkar_name_ar, "رف تجريبي")
