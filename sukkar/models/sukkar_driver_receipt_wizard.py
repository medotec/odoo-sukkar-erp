from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SukkarDriverReceiptWizard(models.TransientModel):
    """Print a driver's Delivery Receipt from the Drivers screen (owner,
    10 Oct 2026): every order in his column, ticked; untick what should not
    print, press Print, and only the ticked orders come out."""
    _name = "sukkar.driver.receipt.wizard"
    _description = "Print Delivery Receipt"

    driver_id = fields.Many2one("res.users", string="Driver", readonly=True)
    line_ids = fields.One2many(
        "sukkar.driver.receipt.wizard.line", "wizard_id", string="Orders")

    @api.model
    def sukkar_open(self, ticket_ids):
        """The window for the tickets in one driver's column, in run order."""
        tickets = self.env["sukkar.production"].browse(ticket_ids).exists().filtered(
            "sale_order_id").sorted(key=lambda t: (
                str(t.delivery_date or "9999-12-31"), t.sequence,
                str(t.delivery_datetime or ""), t.id))
        wizard = self.create({
            "driver_id": tickets.driver_id[:1].id,
            "line_ids": [(0, 0, {"production_id": t.id}) for t in tickets],
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Delivery Receipt — %s", wizard.driver_id.name or _("Driver")),
            "res_model": self._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
        }

    def action_print(self):
        self.ensure_one()
        tickets = self.line_ids.filtered("to_print").production_id
        if not tickets:
            raise UserError(_("Tick at least one order to print."))
        return self.env.ref("sukkar.action_report_driver_receipt").report_action(tickets)


class SukkarDriverReceiptWizardLine(models.TransientModel):
    _name = "sukkar.driver.receipt.wizard.line"
    _description = "Print Delivery Receipt — order"

    wizard_id = fields.Many2one(
        "sukkar.driver.receipt.wizard", required=True, ondelete="cascade")
    production_id = fields.Many2one("sukkar.production", required=True, readonly=True)
    to_print = fields.Boolean(string="Print", default=True)
    sale_order_id = fields.Many2one(related="production_id.sale_order_id", string="Order")
    addr_area = fields.Char(related="production_id.addr_area")
    addr_block = fields.Char(related="production_id.addr_block")
    delivery_date = fields.Date(related="production_id.delivery_date")
    delivery_time_range = fields.Char(related="production_id.delivery_time_range", string="Window")
    cash_due = fields.Monetary(related="production_id.cash_due", string="Collect")
    currency_id = fields.Many2one(related="production_id.currency_id")
    status = fields.Selection(related="production_id.status")
