from odoo import api, fields, models


class PosPaymentMethod(models.Model):
    """Cash on Delivery at the till (owner, 10 Oct 2026).

    A delivery order the driver will collect cash for is not paid at the
    counter: the money is still with the customer. Paid with a method flagged
    here — a Customer Account method, so nothing lands in the till drawer —
    the order goes to the kitchen and the driver UNPAID, the driver receipt
    says how much to collect, and it turns paid when the driver hands the cash
    in on Delivery → Cash with Drivers.
    """
    _inherit = "pos.payment.method"

    sukkar_is_cod = fields.Boolean(
        string="Cash on Delivery (driver collects)",
        help="Orders paid with this method stay unpaid until the driver hands "
             "the cash in. Shown at the till for delivery orders only.")

    # In-store methods are Cash, K-Net and Link; a delivery can also be Cash
    # on Delivery (owner, 10 Oct 2026). A Link payment at the till is checked
    # by the cashier before Validate.
    sukkar_is_link = fields.Boolean(
        string="Link Payment",
        help="Orders paid with this method are recorded as Link payments.")

    @api.model
    def _load_pos_data_fields(self, config_id):
        return super()._load_pos_data_fields(config_id) + ["sukkar_is_cod"]
