from datetime import date, timedelta

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarFlavours(TransactionCase):
    """Cake flavours, from wherever they were chosen to wherever they are read.

    A bouquet can be made of one flavour or split between several, and the
    kitchen has to count the pieces out. So the flavours have to survive every
    hop: the register to the delivery order, the order to the kitchen ticket,
    and the order to the invoice the customer keeps.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        # These tests are about flavours, not about cake stock: the block on
        # confirming beyond available pieces has its own tests.
        cls.company.sukkar_block_oversell = False
        cls.partner = cls.env["res.partner"].create({"name": "Flavour Buyer"})
        cls.bouquet = cls.env["product.product"].create({
            "name": "Flavour Bouquet", "type": "consu", "list_price": 25.0,
            "invoice_policy": "order"})
        Flavour = cls.env["sukkar.cake.flavour"]
        cls.red = Flavour.create({"name": "Test Red Velvet", "name_ar": "ريد فيلفيت",
                                  "code": "TRV", "sequence": 10})
        cls.choc = Flavour.create({"name": "Test Chocolate", "name_ar": "شوكولاتة",
                                   "code": "TCH", "sequence": 20})
        Slot = cls.env["sukkar.delivery.slot"]
        cls.slot = Slot.search([], limit=1) or Slot.create(
            {"start_hour": 16.0, "end_hour": 18.0})

    def _order(self, picks):
        return self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {
                "product_id": self.bouquet.id,
                "product_uom_qty": 1,
                "sukkar_flavour_line_ids": [
                    (0, 0, {"flavour_id": flavour.id, "qty": qty})
                    for flavour, qty in picks],
            })],
        })

    # ------------------------------------------------------------- the wording
    def test_a_single_flavour_is_named_without_a_count(self):
        order = self._order([(self.red, 7)])
        self.assertEqual(order.order_line.sukkar_flavour_summary,
                         "Test Red Velvet · ريد فيلفيت",
                         "a whole bouquet of one flavour just names it")

    def test_split_flavours_say_how_many_of_each(self):
        order = self._order([(self.red, 4), (self.choc, 3)])
        self.assertEqual(
            order.order_line.sukkar_flavour_summary,
            "4 Test Red Velvet · ريد فيلفيت + 3 Test Chocolate · شوكولاتة")

    def test_the_flavours_are_bilingual_everywhere(self):
        order = self._order([(self.red, 4), (self.choc, 3)])
        self.assertIn("ريد فيلفيت", order.sukkar_flavour_summary)
        self.assertIn("Red Velvet", order.sukkar_flavour_summary)

    def test_the_order_rolls_its_bouquets_up(self):
        order = self._order([(self.red, 4), (self.choc, 3)])
        self.assertEqual(order.sukkar_flavour_summary,
                         order.order_line.sukkar_flavour_summary,
                         "one bouquet: the order says exactly what the line says")

    # ------------------------------------------------ the kitchen and the invoice
    def test_split_flavours_reach_the_kitchen_ticket(self):
        order = self._order([(self.red, 4), (self.choc, 3)])
        order.action_confirm()
        ticket = order.sukkar_production_ids
        self.assertTrue(ticket)
        self.assertEqual(ticket.flavour_summary, order.sukkar_flavour_summary,
                         "the kitchen screen reads the order's flavours")
        self.assertIn("4 Test Red Velvet", ticket.order_preview_html,
                      "and the wall tile prints them on the bouquet itself")

    def test_split_flavours_reach_the_invoice(self):
        order = self._order([(self.red, 4), (self.choc, 3)])
        order.action_confirm()
        invoice = order._create_invoices()
        self.assertEqual(invoice.sukkar_flavour_summary,
                         order.sukkar_flavour_summary,
                         "the whole order's flavours on the invoice")
        line = invoice.invoice_line_ids.filtered(
            lambda l: l.product_id == self.bouquet)
        self.assertEqual(line.sukkar_flavour_summary,
                         order.order_line.sukkar_flavour_summary,
                         "and under the bouquet line they belong to")

    # ----------------------------------------------------------- the register
    def test_the_register_picks_become_real_flavour_lines(self):
        config = self.env["pos.config"].search([], limit=1)
        session = self.env["pos.session"].create(
            {"config_id": config.id, "user_id": self.env.uid})
        pos_order = self.env["pos.order"].create({
            "session_id": session.id,
            "partner_id": self.partner.id,
            "amount_tax": 0.0, "amount_total": 25.0,
            "amount_paid": 25.0, "amount_return": 0.0,
            "sukkar_order_type": "delivery",
            "sukkar_delivery_date": date.today() + timedelta(days=1),
            "sukkar_delivery_slot_id": self.slot.id,
            "sukkar_receiver_name": "Dana",
            "sukkar_receiver_phone": "99001122",
            "lines": [(0, 0, {
                "product_id": self.bouquet.id, "qty": 1,
                "price_unit": 25.0, "price_subtotal": 25.0,
                "price_subtotal_incl": 25.0,
                "sukkar_flavour_json":
                    '[{"flavour_id": %s, "qty": 4}, {"flavour_id": %s, "qty": 3}]'
                    % (self.red.id, self.choc.id),
            })],
        })
        line = pos_order.lines
        self.assertEqual(len(line.sukkar_flavour_line_ids), 2,
                         "the raw picks became records the rest of the system reads")
        self.assertEqual(
            sorted(line.sukkar_flavour_line_ids.mapped("qty")), [3, 4])
        self.assertIn("4 Test Red Velvet", line.sukkar_flavour_summary)
        self.assertEqual(pos_order.sukkar_flavour_summary,
                         line.sukkar_flavour_summary,
                         "and the receipt reads them off the order")

        pos_order._sukkar_create_delivery_order()
        sale_line = pos_order.sukkar_sale_order_id.order_line.filtered(
            lambda l: l.product_id == self.bouquet)
        self.assertEqual(len(sale_line.sukkar_flavour_line_ids), 2,
                         "and they survive the trip to the delivery order")
        self.assertEqual(sale_line.sukkar_flavour_summary,
                         line.sukkar_flavour_summary)

    def test_picks_the_register_cannot_send_do_not_stop_the_sale(self):
        config = self.env["pos.config"].search([], limit=1)
        session = self.env["pos.session"].create(
            {"config_id": config.id, "user_id": self.env.uid})
        pos_order = self.env["pos.order"].create({
            "session_id": session.id,
            "partner_id": self.partner.id,
            "amount_tax": 0.0, "amount_total": 25.0,
            "amount_paid": 25.0, "amount_return": 0.0,
            "lines": [(0, 0, {
                "product_id": self.bouquet.id, "qty": 1,
                "price_unit": 25.0, "price_subtotal": 25.0,
                "price_subtotal_incl": 25.0,
                "sukkar_flavour_json": "not json at all",
            })],
        })
        self.assertFalse(pos_order.lines.sukkar_flavour_line_ids)
        self.assertEqual(pos_order.amount_total, 25.0,
                         "the sale goes through without its flavours")
