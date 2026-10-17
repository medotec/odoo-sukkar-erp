from odoo import fields, models

# Fulfillment status a ticket carries while sitting in a given stage.
STATUS_SELECTION = [
    ("in_progress", "In Progress"),
    ("ready_for_pickup", "Ready for Pickup"),
    ("in_delivery", "In Delivery"),
    ("delivered", "Delivered"),
    ("closed", "Closed"),
]


class SukkarProductionStage(models.Model):
    """A column on the kitchen board (Piping … Ready for Pickup … Closed)."""
    _name = "sukkar.production.stage"
    _description = "Kitchen Production Stage"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    default_duration = fields.Float(
        string="Planned Duration (min)",
        help="Typical minutes a ticket should spend in this stage (used for timing).")
    fold = fields.Boolean(string="Folded in Kanban")
    is_done = fields.Boolean(
        string="Production Finished",
        help="From this stage on the item is made (stock is consumed at the first such stage).")
    is_closed = fields.Boolean(
        string="Closed Stage", help="The final stage — the order is fully closed.")
    status = fields.Selection(
        STATUS_SELECTION, string="Fulfillment Status", default="in_progress", required=True,
        help="The status a ticket takes automatically while in this stage.")
    kitchen_stage = fields.Boolean(
        string="On Kitchen Board", default=False,
        help="This stage appears as a column on the Kitchen board.")
    delivery_stage = fields.Boolean(
        string="On Delivery Board", default=False,
        help="This stage appears as a column on the Delivery board.")
