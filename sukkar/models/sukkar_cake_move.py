from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .sukkar_cake_batch import PIECE_STATES

# What each movement means: where the pieces come from and where they end up.
# The manager picks the movement in the shop's own words and the two states
# follow, so a state change can never be recorded back to front.
MOVE_STATES = {
    "receive": (False, "undecorated"),
    "decorate": ("undecorated", "decorated"),
    "reserve": ("available", "reserved"),
    "unreserve": ("reserved", "available"),
    "consume": ("available", False),
    "waste": ("available", "waste"),
    "move": ("decorated", "undecorated"),
    "adjust": (False, "undecorated"),
}


class SukkarCakeMove(models.Model):
    """One movement of N cake pieces — between states, in, or out.

    Every count on a batch is the sum of these rows, so the shop can always
    answer "where did those pieces go" without trusting a running total.
    """
    _name = "sukkar.cake.move"
    _description = "Cake Piece Movement"
    _order = "date desc, id desc"

    name = fields.Char(default="New", copy=False, readonly=True, index=True)
    batch_id = fields.Many2one(
        "sukkar.cake.batch", string="Batch", required=True,
        ondelete="cascade", index=True)
    date = fields.Datetime(
        string="When", default=fields.Datetime.now, required=True, index=True)
    qty = fields.Float(
        string="Pieces", digits="Product Unit of Measure", required=True)

    move_type = fields.Selection(
        [("receive", "Received"),
         ("decorate", "Moved to Display Fridge"),
         ("reserve", "Reserved for Tomorrow"),
         ("unreserve", "Reservation Released"),
         ("consume", "Used on an Order"),
         ("waste", "Expired / Damaged / Wasted"),
         ("move", "Moved Between States"),
         ("adjust", "Manual Adjustment (changes the count)")],
        string="Movement", required=True, index=True)
    state_from = fields.Selection(PIECE_STATES, string="From")
    state_to = fields.Selection(PIECE_STATES, string="To")

    sale_order_id = fields.Many2one(
        "sale.order", string="Order", ondelete="set null", index=True)
    pos_order_id = fields.Many2one(
        "pos.order", string="Register Order", ondelete="set null", index=True)
    production_id = fields.Many2one(
        "sukkar.production", string="Kitchen Ticket", ondelete="set null")
    user_id = fields.Many2one(
        "res.users", string="By", default=lambda self: self.env.user)
    reason = fields.Char(string="Reason")
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, required=True)

    @api.onchange("move_type")
    def _onchange_sukkar_move_type(self):
        for move in self:
            states = MOVE_STATES.get(move.move_type)
            if states:
                move.state_from, move.state_to = states

    @api.constrains("qty", "move_type")
    def _check_sukkar_qty(self):
        for move in self:
            if move.move_type != "adjust" and move.qty <= 0:
                raise ValidationError(_(
                    "Enter how many pieces are moving — a movement of none is "
                    "nothing."))

    @api.constrains("move_type", "state_from", "state_to")
    def _check_sukkar_move_states(self):
        """Moving pieces means moving them FROM somewhere TO somewhere.

        Without both ends the row reads as a manual adjustment and the shop's
        piece count goes up by pieces that only changed shelf.
        """
        for move in self.filtered(lambda m: m.move_type == "move"):
            if not move.state_from or not move.state_to:
                raise ValidationError(_(
                    "Say where the pieces are moving from and where they are "
                    "moving to."))
            if move.state_from == move.state_to:
                raise ValidationError(_(
                    "The pieces are already there — pick a different state."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "sukkar.cake.move") or "New"
        return super().create(vals_list)
