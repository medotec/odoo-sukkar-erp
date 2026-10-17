from datetime import date, timedelta

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarKitchenScreen(TransactionCase):
    """The wall screen and the order preview behind it.

    The owner's rule (19 Sep 2026): the tile carries only what is MADE. The
    flavours and the address are new and have to be there;
    the ingredients list is gone, because the kitchen knows its own recipes and
    the scrolling box it lived in swallowed taps.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.sukkar_block_oversell = False
        cls.partner = cls.env["res.partner"].create({"name": "Kitchen Buyer"})
        cls.piece = cls.env["product.product"].create({
            "name": "Kitchen Screen Cupcake", "type": "consu"})
        cls.bouquet = cls.env["product.product"].create({
            "name": "Kitchen Screen Bouquet", "type": "consu", "list_price": 30.0})
        cls.env["mrp.bom"].create({
            "product_tmpl_id": cls.bouquet.product_tmpl_id.id,
            "product_id": cls.bouquet.id,
            "product_qty": 1.0,
            "bom_line_ids": [(0, 0, {"product_id": cls.piece.id,
                                     "product_qty": 7.0})],
        })
        Flavour = cls.env["sukkar.cake.flavour"]
        cls.red = Flavour.create({"name": "Screen Red Velvet",
                                  "name_ar": "ريد فيلفيت", "code": "SRV"})
        cls.choc = Flavour.create({"name": "Screen Chocolate",
                                   "name_ar": "شوكولاتة", "code": "SCH"})
        Slot = cls.env["sukkar.delivery.slot"]
        cls.slot = Slot.search([], limit=1) or Slot.create(
            {"start_hour": 16.0, "end_hour": 18.0})
        cls.governorate = cls.env["res.country.state"].search(
            [("country_id.code", "=", "KW")], limit=1)

    @classmethod
    def _ticket(cls):
        order = cls.env["sale.order"].create({
            "partner_id": cls.partner.id,
            "sukkar_order_type": "delivery",
            "sukkar_delivery_date": date.today() + timedelta(days=1),
            "sukkar_delivery_slot_id": cls.slot.id,
            "sukkar_receiver_name": "Noura",
            "sukkar_receiver_phone": "99887766",
            "sukkar_deliver_to": "other",
            "sukkar_addr_governorate_id": cls.governorate.id,
            "sukkar_addr_area": "Jabriya",
            "sukkar_addr_block": "6",
            "sukkar_addr_street": "12",
            "sukkar_addr_avenue": "3",
            "sukkar_addr_building": "House 9",
            "sukkar_addr_floor": "2",
            "sukkar_addr_apartment": "4",
            "sukkar_addr_directions": "Beside the mosque",
            "sukkar_gift_message": "Happy Birthday Noura",
            "order_line": [(0, 0, {
                "product_id": cls.bouquet.id,
                "product_uom_qty": 1,
                "sukkar_flavour_line_ids": [
                    (0, 0, {"flavour_id": cls.red.id, "qty": 4}),
                    (0, 0, {"flavour_id": cls.choc.id, "qty": 3}),
                ],
            })],
        })
        order.action_confirm()
        return order.sukkar_production_ids

    # ------------------------------------------------------- what is now there
    def test_the_tile_carries_the_cake_flavours(self):
        ticket = self._ticket()
        self.assertIn("4 Screen Red Velvet", ticket.flavour_summary)
        self.assertIn("3 Screen Chocolate", ticket.flavour_summary)

    def test_the_tile_carries_delivery_or_pickup(self):
        ticket = self._ticket()
        self.assertEqual(ticket.order_type, "delivery")

    def test_the_preview_finally_has_the_address(self):
        ticket = self._ticket()
        preview = ticket.order_preview_html
        self.assertIn("Jabriya", preview)
        self.assertIn("Block 6", preview)
        self.assertIn("Apt 4", preview)
        self.assertIn("Beside the mosque", preview)

    def test_the_preview_has_the_flavours_and_the_card(self):
        preview = self._ticket().order_preview_html
        self.assertIn("4 Screen Red Velvet", preview)
        self.assertIn("ريد فيلفيت", preview, "in Arabic as well as English")
        self.assertIn("Happy Birthday Noura", preview)
        self.assertIn("/web/image/product.product/%s" % self.bouquet.id, preview,
                      "with the photo of what is being made")

    # ---------------------------------------------------- what came off it
    def test_the_preview_no_longer_lists_the_ingredients(self):
        ticket = self._ticket()
        self.assertIn("Kitchen Screen Cupcake", ticket.components_summary,
                      "the recipe is still readable where it belongs")
        self.assertNotIn("Kitchen Screen Cupcake", ticket.order_preview_html,
                         "but the kitchen knows its own recipes — off the preview")

    # ------------------------------------------------------ the printed ticket
    def test_the_printed_ticket_carries_the_flavours_and_the_address(self):
        ticket = self._ticket()
        html = str(self.env["ir.actions.report"]._render_qweb_html(
            "sukkar.action_report_kitchen_ticket", ticket.ids)[0])
        self.assertIn("4 Screen Red Velvet", html,
                      "the baker holding the slip decorates by flavour")
        self.assertIn("Jabriya", html)
        self.assertIn("Apt 4", html)

    def test_the_wall_screen_tile_does_not_show_the_ingredients(self):
        arch = self.env.ref(
            "sukkar.sukkar_kitchen_screen_view_kanban").arch_db
        self.assertIn("flavour_summary", arch, "flavours are kitchen work")
        self.assertNotIn("components_summary", arch,
                         "ingredients are not, and came off the tile")
        self.assertNotIn("payment_status", arch,
                         "nor is the money — it is one tap away on the preview")
