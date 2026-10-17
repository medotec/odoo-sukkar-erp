from odoo import api, fields, models


class DeliveryCarrier(models.Model):
    """Mark the methods where the customer collects the order themselves.

    The order receipt has to say whether the date and window printed on it is a
    delivery or a collection from the branch. Matching on the method's name does
    not work — the methods are named in Arabic ("استلام من المحل"), and a rename
    would silently break the slip. A flag on the record is the only stable way.
    """
    _inherit = "delivery.carrier"

    sukkar_is_pickup = fields.Boolean(
        string="Collected from the Branch",
        help="Tick for methods where the customer comes to the shop. The order "
             "receipt then prints the time as a collection appointment instead of "
             "a delivery, and leaves the delivery address off.",
    )

    @api.model
    def _sukkar_pickup_carrier(self):
        """The branch-collection method, whatever it has been renamed to.

        The back office says 'collected from the branch' with this method; the
        register says it with Pickup. They are the same fact, so the order keeps
        them in step and the receipt reads the same either way.
        """
        return self.search([
            ("sukkar_is_pickup", "=", True),
            ("company_id", "in", (False, self.env.company.id)),
        ], limit=1)
