from datetime import date, timedelta

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestDisplayProductionSales(TransactionCase):
    """Requirement 12.4 — what a display batch made, and what it sold.

    The attribution is by TIME WINDOW, not lot traceability: sales of a product
    from the moment a batch is made until the next batch of that product replaces
    it. These tests pin that behaviour, including the boundary, because the number
    reads as more precise than it is.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = date.today()
        cls.customer = cls.env["res.partner"].create({"name": "Counter Customer"})
        cls.cupcake = cls.env["product.product"].create({
            "name": "Display Cupcake", "list_price": 2.0, "standard_price": 0.5,
            "sale_ok": True, "is_storable": True})
        # A recipe, because a batch can only produce something that has one.
        flour = cls.env["product.product"].create({
            "name": "Test Flour", "standard_price": 0.1, "is_storable": True})
        cls.env["mrp.bom"].create({
            "product_tmpl_id": cls.cupcake.product_tmpl_id.id,
            "product_qty": 1.0,
            "bom_line_ids": [(0, 0, {"product_id": flour.id, "product_qty": 1.0})]})

    def _batch(self, qty, day=None, state="done"):
        batch = self.env["sukkar.display.production"].create({
            "date": day or self.today,
            "line_ids": [(0, 0, {"product_id": self.cupcake.id, "qty": qty})]})
        # Marked done directly: action_produce() runs real manufacturing orders,
        # which is covered elsewhere. What is under test here is the attribution.
        batch.state = state
        return batch

    def _invoice(self, qty, day=None, refund=False):
        move = self.env["account.move"].create({
            "move_type": "out_refund" if refund else "out_invoice",
            "partner_id": self.customer.id,
            "invoice_date": day or self.today,
            "invoice_line_ids": [(0, 0, {
                "product_id": self.cupcake.id, "quantity": qty, "price_unit": 2.0})]})
        move.action_post()
        return move

    # ------------------------------------------------------------------ tests
    def test_batch_with_no_sales(self):
        batch = self._batch(24)
        self.assertEqual(batch.sold_qty, 0.0)
        self.assertEqual(batch.unsold_qty, 24.0)
        self.assertEqual(batch.sold_revenue, 0.0)

    def test_draft_batch_reports_nothing(self):
        """A batch that was never made cannot have sold anything."""
        batch = self._batch(24, state="draft")
        self._invoice(5)
        self.assertEqual(batch.sold_qty, 0.0)

    def test_sales_inside_the_window_are_counted(self):
        batch = self._batch(24)
        self._invoice(5)
        batch.invalidate_recordset()
        self.assertEqual(batch.sold_qty, 5.0)
        self.assertEqual(batch.unsold_qty, 19.0)
        self.assertAlmostEqual(batch.sold_revenue, 10.0, places=2)

    def test_margin_is_revenue_less_ingredient_cost(self):
        batch = self._batch(24)
        self._invoice(10)
        batch.invalidate_recordset()
        self.assertAlmostEqual(batch.margin, batch.sold_revenue - batch.total_cost, places=2)

    def test_a_later_batch_closes_the_window(self):
        """Sales after the next batch belong to that batch, not this one."""
        first = self._batch(24, day=self.today - timedelta(days=2))
        self._batch(24, day=self.today)               # replaces it
        self._invoice(5, day=self.today)              # sold after the replacement
        first.invalidate_recordset()
        self.assertEqual(first.sold_qty, 0.0,
                         "the earlier batch must not claim sales made after it was replaced")

    def test_sales_before_the_batch_are_not_counted(self):
        batch = self._batch(24)
        self._invoice(5, day=self.today - timedelta(days=1))
        batch.invalidate_recordset()
        self.assertEqual(batch.sold_qty, 0.0,
                         "a batch cannot have sold anything before it existed")

    def test_sold_more_than_made_is_capped(self):
        """Surplus belongs to stock already on the counter, not to this batch."""
        batch = self._batch(10)
        self._invoice(25)
        batch.invalidate_recordset()
        self.assertEqual(batch.sold_qty, 10.0)
        self.assertEqual(batch.unsold_qty, 0.0)
        self.assertAlmostEqual(batch.sold_revenue, 20.0, places=2,
                               msg="revenue must be capped in the same proportion")

    def test_a_credit_note_reverses_the_sale(self):
        batch = self._batch(24)
        self._invoice(5)
        self._invoice(2, refund=True)
        batch.invalidate_recordset()
        self.assertEqual(batch.sold_qty, 3.0)
        self.assertAlmostEqual(batch.sold_revenue, 6.0, places=2)

    def test_two_batches_the_same_day_do_not_double_count(self):
        """The live database had exactly this: two batches, same item, same day.
        Without a tie-break both claim the same sales and revenue is counted twice."""
        first = self._batch(24)
        second = self._batch(24)
        self._invoice(10)
        first.invalidate_recordset()
        second.invalidate_recordset()
        self.assertEqual(
            first.sold_qty + second.sold_qty, 10.0,
            "the two batches together must not claim more than was actually sold")

    def test_line_breakdown_matches_the_batch_total(self):
        batch = self._batch(24)
        self._invoice(7)
        batch.invalidate_recordset()
        self.assertEqual(sum(batch.line_ids.mapped("sold_qty")), batch.sold_qty)
        self.assertEqual(batch.line_ids[0].unsold_qty, 17.0)

    def test_an_invoiced_till_sale_is_counted_once(self):
        """A counter sale the cashier ticked "Invoice" is one sale with two
        records: the POS line and the invoice the register generated for it.
        Counting both told the owner he sold twice what he made, and that the
        rest went to waste."""
        batch = self._batch(24)
        config = self.env["pos.config"].search([], limit=1)
        session = self.env["pos.session"].create(
            {"config_id": config.id, "user_id": self.env.uid})
        # A second past local midnight: the attribution window runs from the
        # batch's own day up to now, and "now" has no microseconds in Odoo.
        opened_at = self.env["sukkar.delivery.slot"]._tz_midnight_utc(self.today)
        pos_order = self.env["pos.order"].create({
            "session_id": session.id,
            "partner_id": self.customer.id,
            "date_order": opened_at + timedelta(seconds=1),
            "amount_tax": 0.0, "amount_total": 20.0,
            "amount_paid": 20.0, "amount_return": 0.0,
            "lines": [(0, 0, {
                "product_id": self.cupcake.id, "qty": 10,
                "price_unit": 2.0, "price_subtotal": 20.0,
                "price_subtotal_incl": 20.0})],
        })
        pos_order.state = "paid"
        batch.invalidate_recordset()
        self.assertAlmostEqual(batch.sold_qty, 10.0, places=2)
        # Now the same sale is invoiced for the customer.
        invoice = self._invoice(10)
        invoice.write({"pos_order_ids": [(4, pos_order.id)]})
        batch.invalidate_recordset()
        self.assertAlmostEqual(batch.sold_qty, 10.0, places=2,
                               msg="one sale, counted once")
        self.assertAlmostEqual(batch.sold_revenue, 20.0, places=2)
        self.assertAlmostEqual(batch.unsold_qty, 14.0, places=2)
