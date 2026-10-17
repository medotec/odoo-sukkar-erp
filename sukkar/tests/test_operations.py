from odoo import fields
from odoo.tools.safe_eval import safe_eval, datetime as safe_datetime
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarOperations(TransactionCase):
    """The order, receipt and kitchen behaviours that have bitten us before.

    Each of these corresponds to a real defect found by hand at some point. They
    are here so the same defect cannot come back unnoticed.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Receipt Test Customer", "phone": "+96550001111"})
        cls.product = cls.env["product.product"].create({
            "name": "Test Bouquet", "list_price": 20.0, "sale_ok": True})
        cls.governorate = cls.env["res.country.state"].search(
            [("country_id.code", "=", "KW")], limit=1)

    def _order(self, **vals):
        base = {"partner_id": self.partner.id,
                "order_line": [(0, 0, {"product_id": self.product.id,
                                       "product_uom_qty": 1})]}
        base.update(vals)
        return self.env["sale.order"].create(base)

    def _render_receipt(self, order):
        pdf, _ftype = self.env["ir.actions.report"]._render_qweb_pdf(
            "sukkar.report_order_receipt", res_ids=order.ids)
        return pdf

    # ------------------------------------------------------------- the receipt
    def test_receipt_renders(self):
        self.assertTrue(len(self._render_receipt(self._order())) > 0)

    def test_receipt_renders_with_no_lines(self):
        order = self.env["sale.order"].create({"partner_id": self.partner.id})
        self.assertTrue(len(self._render_receipt(order)) > 0)

    def test_receipt_renders_with_no_payment_method(self):
        order = self._order()
        order.sukkar_payment_method = False
        self.assertTrue(len(self._render_receipt(order)) > 0)

    def test_receipt_renders_with_no_phone_anywhere(self):
        nobody = self.env["res.partner"].create({"name": "No Phone"})
        order = self._order(partner_id=nobody.id, sukkar_gift_to="Someone")
        self.assertTrue(len(self._render_receipt(order)) > 0)

    def test_receipt_renders_with_many_lines(self):
        order = self._order(order_line=[
            (0, 0, {"product_id": self.product.id, "product_uom_qty": 1})
            for _ in range(40)])
        self.assertTrue(len(self._render_receipt(order)) > 0)

    def test_price_shows_only_for_cash_and_cod(self):
        """A gift slip travelling with the order must not show the receiver a price."""
        for method, should_show in (("cash", True), ("cod", True),
                                    ("knet", False), ("link", False)):
            order = self._order(sukkar_payment_method=method)
            html = self.env["ir.qweb"]._render(
                "sukkar.report_order_receipt",
                {"docs": order, "doc_ids": order.ids, "doc_model": "sale.order"})
            self.assertEqual("قيمة الطلب" in str(html), should_show,
                             f"amount block wrong for payment method {method}")

    def test_payment_badge_does_not_rely_on_colour(self):
        """The receipt prints on a 1-bit thermal printer, where colour carries no
        meaning at all. UNPAID must reverse out of solid black; PAID is outlined.
        This caught a silent failure once already — the stylesheet was rewritten
        while the markup kept classes that no longer existed, so both badges
        rendered as plain unstyled text and the slip still looked plausible."""
        unpaid = self._order(sukkar_payment_method="cod")
        html = str(self.env["ir.qweb"]._render(
            "sukkar.report_order_receipt",
            {"docs": unpaid, "doc_ids": unpaid.ids, "doc_model": "sale.order"}))
        self.assertIn("kr-badge-solid", html, "UNPAID must use the reversed-out bar")

        paid = self._order(sukkar_payment_method="knet")
        paid.sukkar_paid = True
        html = str(self.env["ir.qweb"]._render(
            "sukkar.report_order_receipt",
            {"docs": paid, "doc_ids": paid.ids, "doc_model": "sale.order"}))
        self.assertIn("kr-badge-open", html, "PAID must use the outlined badge")

    def test_receipt_carries_no_colour(self):
        """Nothing on this slip may depend on a colour a mono printer cannot show."""
        order = self._order()
        html = str(self.env["ir.qweb"]._render(
            "sukkar.report_order_receipt",
            {"docs": order, "doc_ids": order.ids, "doc_model": "sale.order"}))
        style = html[html.find("<style>"):html.find("</style>")]
        for colour in ("#D82B7D", "#8E1650", "#A0607A", "#FFF4F9", "#F3D3E2",
                       "#1FA752", "#C0392B"):
            self.assertNotIn(colour, style,
                             f"{colour} would print as an illegible grey")

    # ------------------------------------------------------------ payment gate
    def test_unpaid_link_order_cannot_reach_ready(self):
        order = self._order(sukkar_payment_method="link")
        order.action_confirm()
        ticket = order.sukkar_production_ids[:1]
        done = self.env["sukkar.production.stage"].search(
            [("is_done", "=", True)], order="sequence", limit=1)
        with self.assertRaises(UserError):
            ticket.stage_id = done.id

    def test_link_payment_needs_a_reference(self):
        order = self._order(sukkar_payment_method="link")
        with self.assertRaises(ValidationError):
            order.sukkar_paid = True
            order.flush_recordset()

    # --------------------------------------------------------- delivery address
    def test_duplicating_an_order_does_not_share_the_address(self):
        """Editing a copy used to rewrite the original's delivery address."""
        original = self._order(
            sukkar_deliver_to="other",
            sukkar_addr_governorate_id=self.governorate.id,
            sukkar_addr_area="Salmiya", sukkar_receiver_name="Receiver A")
        copy = original.copy()
        copy.write({"sukkar_receiver_name": "Receiver B",
                    "sukkar_addr_area": "Hawalli"})
        original.invalidate_recordset()
        self.assertNotEqual(original.partner_shipping_id,
                            copy.partner_shipping_id,
                            "the copy must get its own address record")
        self.assertEqual(original.partner_shipping_id.city, "Salmiya")

    # ---------------------------------------------------------- delivery slots
    def test_slot_ending_before_it_starts_is_refused(self):
        with self.assertRaises(ValidationError):
            self.env["sukkar.delivery.slot"].create(
                {"start_hour": 20.0, "end_hour": 9.0})

    # ------------------------------------------------------------ kitchen setup
    def test_confirming_an_order_creates_one_kitchen_ticket(self):
        order = self._order()
        order.action_confirm()
        self.assertEqual(len(order.sukkar_production_ids), 1)
        # Odoo itself refuses a second action_confirm(), so the double-ticket risk
        # is not there — it is in the ticket creation being called again by any
        # other path. That is the guard worth testing.
        order._create_sukkar_production()
        self.assertEqual(len(order.sukkar_production_ids), 1,
                         "ticket creation must be idempotent")

    def test_delivery_fee_is_not_sent_to_the_kitchen(self):
        """The kitchen must never be told to make a 'Delivery Fee'."""
        order = self._order()
        order.action_confirm()
        summary = order.sukkar_production_ids[:1].line_summary or ""
        self.assertNotIn("Delivery", summary)

    def test_finish_before_delivery_cannot_be_negative(self):
        with self.assertRaises(ValidationError):
            self.env.company.sukkar_finish_before_min = -10
            self.env.company.flush_recordset()


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarTimezone(TransactionCase):
    """One timezone setting has to reach everything that reads a timezone.

    Odoo takes its timezone from several unrelated places and each defaults
    somewhere unhelpful — a blank company partner, a UTC resource calendar, a
    user with none set. In Kuwait every one of those is three hours wrong.
    """

    def test_company_partner_carries_the_timezone(self):
        """stock_enhancement reads this exact field and falls back to UTC."""
        company = self.env.company
        company._sukkar_propagate_tz()
        self.assertEqual(company.partner_id.tz, company.sukkar_tz)

    def test_resource_calendars_carry_the_timezone(self):
        """A contract on a UTC calendar has its hours — and overtime — mis-computed."""
        company = self.env.company
        company._sukkar_propagate_tz()
        for calendar in self.env["resource.calendar"].search([]):
            self.assertEqual(calendar.tz, company.sukkar_tz,
                             f"{calendar.name} is not on the operating timezone")

    def test_users_without_a_timezone_are_given_one(self):
        company = self.env.company
        user = self.env["res.users"].create({
            "name": "TZ Test", "login": "tz.test@sukkar.test", "tz": False})
        company._sukkar_propagate_tz()
        self.assertEqual(user.tz, company.sukkar_tz)

    def test_changing_the_setting_moves_everyone_who_followed_it(self):
        company = self.env.company
        original = company.sukkar_tz
        follower = self.env["res.users"].create({
            "name": "Follower", "login": "follower@sukkar.test", "tz": original})
        independent = self.env["res.users"].create({
            "name": "Independent", "login": "independent@sukkar.test", "tz": "Europe/Berlin"})
        company.sukkar_tz = "Asia/Dubai"
        self.assertEqual(follower.tz, "Asia/Dubai", "followers move with the company")
        self.assertEqual(independent.tz, "Europe/Berlin",
                         "someone who chose their own timezone must be left alone")
        company.sukkar_tz = original


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarKitchenScreen(TransactionCase):
    """The wall screen: one big button, and only the buttons that are needed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Screen Customer"})
        cls.product = cls.env["product.product"].create({
            "name": "Screen Bouquet", "list_price": 20.0, "sale_ok": True})

    def _ticket(self, **order_vals):
        vals = {"partner_id": self.partner.id,
                "order_line": [(0, 0, {"product_id": self.product.id,
                                       "product_uom_qty": 1})]}
        vals.update(order_vals)
        order = self.env["sale.order"].create(vals)
        order.action_confirm()
        return order.sukkar_production_ids[:1]

    def test_done_advances_one_step(self):
        ticket = self._ticket()
        first = ticket.stage_id
        expected = ticket.next_stage_id
        ticket.action_sukkar_done()
        self.assertNotEqual(ticket.stage_id, first)
        self.assertEqual(ticket.stage_id, expected)

    def test_done_respects_the_payment_gate(self):
        """The screen is a faster way to do the same thing, never a way round it."""
        ticket = self._ticket(sukkar_payment_method="link")
        for _ in range(20):
            if not ticket.next_stage_id:
                break
            if ticket.next_stage_id.is_done:
                with self.assertRaises(UserError):
                    ticket.action_sukkar_done()
                return
            ticket.action_sukkar_done()
        self.fail("never reached the gated step")

    def test_the_last_step_offers_no_done_button(self):
        ticket = self._ticket()
        while ticket.next_stage_id:
            ticket.action_sukkar_done()
        self.assertFalse(ticket.next_stage_id)
        with self.assertRaises(UserError):
            ticket.action_sukkar_done()

    def test_problem_toggles_both_ways(self):
        ticket = self._ticket()
        ticket.action_sukkar_problem()
        self.assertEqual(ticket.kanban_state, "blocked")
        ticket.action_sukkar_problem()
        self.assertEqual(ticket.kanban_state, "normal")

    def test_a_ticket_with_no_delivery_date_still_reaches_the_kitchen(self):
        """Filtering the wall screen on the date alone hid undated tickets entirely
        — they still have to be made."""
        action = self.env.ref("sukkar.sukkar_kitchen_screen_action")
        ticket = self._ticket()
        ticket.sale_order_id.sukkar_delivery_date = False
        ticket.invalidate_recordset()
        model = self.env["sukkar.production"]
        domain = safe_eval(action.domain, {
            # context_today needs a record to read the timezone from.
            "context_today": lambda: fields.Date.context_today(model),
            "datetime": safe_datetime, "self": model})
        self.assertIn(ticket, self.env["sukkar.production"].search(domain),
                      "an undated ticket must still appear on the kitchen screen")

    def test_countdown_reads_as_time_left_not_a_clock(self):
        ticket = self._ticket()
        ticket.sale_order_id.sukkar_delivery_date = fields.Date.today()
        ticket.invalidate_recordset()
        self.assertIn(ticket.urgency, ("late", "soon", "ok", "none"))
        if ticket.deadline:
            self.assertTrue(
                "min" in ticket.countdown or "h " in ticket.countdown,
                f"countdown should be a duration, got {ticket.countdown!r}")

    def test_a_ticket_with_no_deadline_is_not_marked_urgent(self):
        ticket = self._ticket()
        ticket.sale_order_id.sukkar_delivery_date = False
        ticket.invalidate_recordset()
        self.assertEqual(ticket.urgency, "none")
        self.assertEqual(ticket.countdown, "")
