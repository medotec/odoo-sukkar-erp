from datetime import date

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarDispatch(TransactionCase):
    """The dispatch rules: a driver before pickup, run priority from the
    delivery window, and the money rule of the driver receipt."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Dispatch Buyer"})
        cls.product = cls.env["product.product"].create({
            "name": "Dispatch Cake", "type": "consu", "list_price": 10.0})
        Slot = cls.env["sukkar.delivery.slot"]
        cls.slot_evening = Slot.search(
            [("start_hour", "=", 18.0)], limit=1) or Slot.create(
            {"start_hour": 18.0, "end_hour": 20.0})
        cls.slot_morning = Slot.search(
            [("start_hour", "=", 9.0)], limit=1) or Slot.create(
            {"start_hour": 9.0, "end_hour": 11.0})
        cls.driver = cls.env["res.users"].create({
            "name": "Test Driver", "login": "test.dispatch.driver"})

    def _order(self, **vals):
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {"product_id": self.product.id,
                                   "product_uom_qty": 1})],
            "sukkar_delivery_date": date(2026, 9, 1),
            "sukkar_delivery_slot_id": self.slot_evening.id,
            **vals,
        })
        order.action_confirm()
        return order, order.sukkar_production_ids[:1]

    # ------------------------------------------------------------- driver gate
    def test_pickup_refused_without_driver(self):
        _order, ticket = self._order()
        with self.assertRaises(UserError, msg="pickup must demand a driver"):
            ticket.action_picked_up()
        ticket.driver_id = self.driver
        ticket.action_picked_up()
        self.assertEqual(ticket.status, "in_delivery")
        self.assertEqual(ticket.driver_id, self.driver,
                         "the assigned driver must survive pickup")

    # ---------------------------------------------------------- run priority
    def test_sequence_follows_window_until_overridden(self):
        _order, ticket = self._order()
        self.assertEqual(ticket.sequence, 1080,
                         "18:00 window = 1080 minutes since midnight")
        ticket.sequence = 3  # supervisor drags the card up the run
        self.assertEqual(ticket.sequence, 3)
        ticket.delivery_slot_id = self.slot_morning  # moved to another window
        self.assertEqual(
            ticket.sequence, 540,
            "changing the window resets the priority to the window's own")
        self.assertEqual(
            ticket.sale_order_id.sukkar_delivery_slot_id, self.slot_morning,
            "dragging to another column must reschedule the ORDER, not a copy")

    # ------------------------------------------------------------- money rule
    def test_driver_receipt_money_rule(self):
        order, ticket = self._order(sukkar_payment_method="cod")
        self.assertAlmostEqual(
            order.sukkar_cash_due, order.amount_total, places=3,
            msg="unpaid COD: the driver collects the total")
        order.sukkar_paid = True
        self.assertEqual(order.sukkar_cash_due, 0.0,
                         "a PAID order prints no price — even a cash one")
        knet_order, _t = self._order(sukkar_payment_method="knet")
        self.assertEqual(knet_order.sukkar_cash_due, 0.0,
                         "KNET is not collected by the driver")
        self.assertTrue(ticket.cash_due == 0.0 or not order.sukkar_paid,
                        "the ticket mirrors the order's cash-due")


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarPayrollAccounting(TransactionCase):
    """One overtime system, one ledger: a completed payslip posts a balanced
    journal entry, and the Monthly P&L takes salaries from payroll — never
    from the expense tracker at the same time."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["hr.payslip"]._sukkar_setup_accounting()
        cls.company = cls.env.company
        cls.calendar = cls.env.ref("sukkar.sukkar_resource_calendar")
        cls.structure = cls.env.ref("sukkar.sukkar_payroll_structure")
        cls.journal = cls.env["account.journal"].search(
            [("code", "=", "SLRY"), ("company_id", "=", cls.company.id)], limit=1)
        cls.employee = cls.env["hr.employee"].create({
            "name": "Posting Test", "resource_calendar_id": cls.calendar.id})
        cls.contract = cls.env["hr.contract"].create({
            "name": "Posting Test contract",
            "employee_id": cls.employee.id,
            "wage": 300.0,
            "date_start": date(2026, 1, 1),
            "resource_calendar_id": cls.calendar.id,
            "struct_id": cls.structure.id,
            "journal_id": cls.journal.id,
            "state": "open",
        })

    def _done_slip(self, date_from=date(2026, 8, 1), date_to=date(2026, 8, 30)):
        slip = self.env["hr.payslip"].create({
            "employee_id": self.employee.id,
            "contract_id": self.contract.id,
            "struct_id": self.structure.id,
            "date_from": date_from, "date_to": date_to,
            "name": "Posting test slip",
        })
        self.env["hr.payslip.input"].create({
            "payslip_id": slip.id, "contract_id": self.contract.id,
            "name": "OT_N", "code": "OT_N", "amount": 4.0,
        })
        slip.compute_sheet()
        slip.action_payslip_done()
        return slip

    def test_done_payslip_posts_balanced_move(self):
        slip = self._done_slip()
        self.assertTrue(slip.move_id, "a completed payslip must post to accounting")
        move = slip.move_id
        self.assertAlmostEqual(
            sum(move.line_ids.mapped("debit")),
            sum(move.line_ids.mapped("credit")), places=3)
        gross = sum(l.total for l in slip.line_ids if l.code == "GROSS")
        expense_debit = sum(
            l.debit for l in move.line_ids if l.account_id.code == "6300")
        payable_credit = sum(
            l.credit for l in move.line_ids if l.account_id.code == "2300")
        self.assertAlmostEqual(expense_debit, gross, places=3,
                               msg="BASIC + OT must debit the salary expense")
        self.assertAlmostEqual(payable_credit, gross, places=3,
                               msg="NET must credit Salaries Payable")

    def test_net_splits_into_bank_and_cash(self):
        """Basic goes by WPS bank transfer, overtime is paid in cash — the
        payslip must show both amounts (owner, 29 Aug 2026)."""
        slip = self.env["hr.payslip"].create({
            "employee_id": self.employee.id, "contract_id": self.contract.id,
            "struct_id": self.structure.id,
            "date_from": date(2026, 10, 1), "date_to": date(2026, 10, 30),
            "name": "Split test slip",
        })
        self.env["hr.payslip.input"].create({
            "payslip_id": slip.id, "contract_id": self.contract.id,
            "name": "OT_N", "code": "OT_N", "amount": 4.0,
        })
        slip.compute_sheet()
        lines = {l.code: l.total for l in slip.line_ids}
        ot = 4.0 * (300.0 / 208.0) * 1.25
        self.assertAlmostEqual(lines["NET_CASH"], ot, places=3,
                               msg="cash = the overtime money")
        self.assertAlmostEqual(lines["NET_BANK"], lines["NET"] - ot, places=3,
                               msg="bank = everything except the overtime")
        self.assertAlmostEqual(lines["NET_BANK"] + lines["NET_CASH"],
                               lines["NET"], places=3,
                               msg="the split must always sum back to NET")

    def test_ot_cap_is_company_configuration(self):
        """The cap lives in Settings (company-wide); a contract cap only
        overrides it. Nothing is hard-coded per employee card."""
        self.contract.sukkar_ot_cap_hours = 0.0
        self.env.company.sukkar_ot_cap_default_hours = 2.0
        slip = self.env["hr.payslip"].create({
            "employee_id": self.employee.id, "contract_id": self.contract.id,
            "struct_id": self.structure.id,
            "date_from": date(2026, 11, 1), "date_to": date(2026, 11, 30),
            "name": "Cap config slip",
        })
        self.env["hr.payslip.input"].create({
            "payslip_id": slip.id, "contract_id": self.contract.id,
            "name": "OT_N", "code": "OT_N", "amount": 5.0,
        })
        paid, worked = slip.sukkar_ot_hours()
        self.assertEqual(sum(paid.values()), 2.0,
                         "company default cap must apply when the contract has none")
        self.contract.sukkar_ot_cap_hours = 3.0
        paid, _w = slip.sukkar_ot_hours()
        self.assertEqual(sum(paid.values()), 3.0,
                         "a contract cap overrides the company default")

    def test_monthly_close_takes_salaries_from_payroll_only(self):
        self._done_slip()
        salary_cat = self.env["sukkar.expense.category"].search(
            [("is_salary", "=", True)], limit=1) or \
            self.env["sukkar.expense.category"].create(
                {"name": "Salaries (test)", "is_salary": True})
        close = self.env["sukkar.monthly.close"].create({
            "date_from": date(2026, 8, 1), "date_to": date(2026, 8, 31)})
        close.action_compute()
        baseline = close.expenses_total
        gross = 300.0 + 4.0 * (300.0 / 208.0) * 1.25
        self.assertGreaterEqual(
            baseline + 0.001, gross,
            "payroll GROSS must be inside the operating expenses")
        # A salary typed into the expense tracker must NOT add on top.
        self.env["sukkar.expense"].create({
            "name": "Double-count attempt", "category_id": salary_cat.id,
            "date": date(2026, 8, 15), "basic_amount": 700.0,
            "state": "confirmed",
        }).action_confirm()
        close.action_compute()
        self.assertAlmostEqual(
            close.expenses_total, baseline, places=3,
            msg="with payslips in the month, salary-category expenses are "
                "excluded — one system pays wages, not two")
