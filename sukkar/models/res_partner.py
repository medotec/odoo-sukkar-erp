from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    """A customer and the addresses they have.

    An address is an ordinary Odoo child contact of type "delivery", so every
    screen that already knows how to deliver — the order, the driver receipt,
    the invoice — keeps working without being taught anything new. What this
    adds is the Kuwaiti shape of an address: an area chosen from the ones its
    governorate contains, plus the parts Kuwait says out loud.
    """
    _inherit = "res.partner"

    sukkar_label = fields.Char(
        string="Address Name",
        help="What the shop calls this address — Home, Office, Mum's house.")
    sukkar_area_id = fields.Many2one(
        "sukkar.area", string="Area", index=True)
    sukkar_block = fields.Char(string="Block")
    sukkar_street = fields.Char(string="Street Name / No.")
    sukkar_avenue = fields.Char(string="Avenue")
    sukkar_building = fields.Char(string="House")
    sukkar_floor = fields.Char(string="Floor")
    sukkar_apartment = fields.Char(string="Apartment")
    sukkar_directions = fields.Text(string="Directions for the Driver")
    sukkar_is_primary = fields.Boolean(
        string="Primary Address",
        help="The address the shop uses for this customer unless told otherwise.")
    sukkar_is_recipient = fields.Boolean(
        string="Gift Recipient",
        help="Someone this customer sends gifts to — a mother, a friend, an "
             "office. Kept under the customer, with addresses of their own, so "
             "the counter picks them next time instead of typing them again.")

    # ------------------------------------------------------------------ helpers
    @api.onchange("sukkar_area_id")
    def _onchange_sukkar_area_id(self):
        """The area decides the governorate — never the other way round, so a
        real area can never end up filed under the wrong governorate."""
        for partner in self:
            if partner.sukkar_area_id:
                partner.state_id = partner.sukkar_area_id.state_id
                partner.city = partner.sukkar_area_id.name
                partner.country_id = partner.sukkar_area_id.state_id.country_id

    def _sukkar_address_vals(self, address, parent):
        """The address, in the shape res.partner stores it."""
        area = self.env["sukkar.area"].browse(
            address.get("area_id")) if address.get("area_id") else None
        street = " ".join(p for p in (
            f"Block {address['block']}" if address.get("block") else "",
            f"Street {address['street']}" if address.get("street") else "",
            f"Avenue {address['avenue']}" if address.get("avenue") else "",
        ) if p)
        street2 = " ".join(p for p in (
            f"House {address['building']}" if address.get("building") else "",
            f"Floor {address['floor']}" if address.get("floor") else "",
            f"Apt {address['apartment']}" if address.get("apartment") else "",
        ) if p)
        return {
            "type": "delivery",
            "parent_id": parent.id,
            "name": (address.get("contact_name") or "").strip() or parent.name,
            "phone": (address.get("contact_phone") or "").strip() or parent.phone,
            "email": (address.get("contact_email") or "").strip() or parent.email,
            "sukkar_label": (address.get("label") or "").strip(),
            "sukkar_area_id": area.id if area else False,
            "sukkar_block": address.get("block") or False,
            "sukkar_street": address.get("street") or False,
            "sukkar_avenue": address.get("avenue") or False,
            "sukkar_building": address.get("building") or False,
            "sukkar_floor": address.get("floor") or False,
            "sukkar_apartment": address.get("apartment") or False,
            "sukkar_directions": address.get("directions") or False,
            "street": street or False,
            "street2": street2 or False,
            "city": area.name if area else False,
            "state_id": area.state_id.id if area else (address.get("state_id") or False),
            "country_id": (area.state_id.country_id.id if area
                           else self.env.ref("base.kw").id),
        }

    @api.model
    def sukkar_create_from_pos(self, payload):
        """Create a customer, and its first address if the cashier gave one.

        Called by the register's own customer screen. The first address a
        customer gets is their primary one; the shop can move that later.
        """
        partner = self.create({
            "name": (payload.get("name") or "").strip(),
            "phone": (payload.get("phone") or "").strip() or False,
            "email": (payload.get("email") or "").strip() or False,
            "company_type": "person",
            "country_id": self.env.ref("base.kw").id,
        })
        address = payload.get("address")
        if address:
            child = self.create(self._sukkar_address_vals(address, partner))
            child.sukkar_is_primary = True
            # The customer's own card shows where they are, so the counter can
            # read it without opening the address.
            partner.write({
                "sukkar_area_id": child.sukkar_area_id.id,
                "state_id": child.state_id.id,
                "city": child.city,
            })
        return partner.id

    @api.model
    def sukkar_update_from_pos(self, partner_id, payload):
        """Edit a customer from the till, with the same form that created them
        (owner, 2 Oct 2026): name, phone, e-mail."""
        partner = self.browse(partner_id).exists()
        if not partner:
            raise UserError(_("This customer no longer exists."))
        partner.write({
            "name": (payload.get("name") or "").strip() or partner.name,
            "phone": (payload.get("phone") or "").strip() or False,
            "email": (payload.get("email") or "").strip() or False,
        })
        return partner.id

    @api.model
    def sukkar_pos_update_address(self, address_id, address):
        """Correct a saved address. It stays under the same person and keeps
        its primary flag; only what the cashier sees in the form changes."""
        child = self.browse(address_id).exists()
        if not child or child.type != "delivery" or not child.parent_id:
            raise UserError(_("This address no longer exists."))
        vals = self._sukkar_address_vals(address, child.parent_id)
        vals.pop("parent_id")
        vals.pop("type")
        child.write(vals)
        if child.sukkar_is_primary and child.sukkar_area_id:
            child.parent_id.write({
                "sukkar_area_id": child.sukkar_area_id.id,
                "state_id": child.state_id.id,
                "city": child.city,
            })
        return child._sukkar_address_card()

    # --------------------------------------------------- the register's people
    # A gift goes to a recipient, and a recipient has addresses. Both are
    # ordinary res.partner children, so the order, the driver receipt and the
    # invoice already know how to read them:
    #
    #     customer  ──┬── address        (type delivery)
    #                 └── recipient      (sukkar_is_recipient, type contact)
    #                         └── address (type delivery)
    #
    # Saved once, offered every time after — the counter never types a mother's
    # address twice. (Owner's design, 29 Sep 2026.)

    def _sukkar_address_card(self):
        """One address, in the few words the register shows on a button."""
        self.ensure_one()
        where = ", ".join(p for p in (
            self.sukkar_area_id.name,
            f"Block {self.sukkar_block}" if self.sukkar_block else "",
            f"Street {self.sukkar_street}" if self.sukkar_street else "",
            f"House {self.sukkar_building}" if self.sukkar_building else "",
        ) if p)
        return {
            "id": self.id,
            "label": self.sukkar_label or "",
            "is_primary": self.sukkar_is_primary,
            "where": where,
            "governorate_id": self.state_id.id,
            "governorate": self.state_id.name or "",
            "area_id": self.sukkar_area_id.id,
            "area": self.sukkar_area_id.name or "",
            # What the shop charges to drive to this area. The register fills
            # it in the moment the address is picked.
            "fee": self.sukkar_area_id.sukkar_delivery_fee or 0.0,
            "block": self.sukkar_block or "",
            "street": self.sukkar_street or "",
            "avenue": self.sukkar_avenue or "",
            "building": self.sukkar_building or "",
            "floor": self.sukkar_floor or "",
            "apartment": self.sukkar_apartment or "",
            "directions": self.sukkar_directions or "",
            # who receives at this address, so editing it starts from what is saved
            "contact_name": self.name or "",
            "contact_phone": self.phone or "",
            "contact_email": self.email or "",
        }

    def _sukkar_addresses(self):
        """This person's addresses, the primary one first."""
        self.ensure_one()
        addresses = self.search(
            [("parent_id", "=", self.id), ("type", "=", "delivery")],
            order="sukkar_is_primary desc, id")
        return [address._sukkar_address_card() for address in addresses]

    @api.model
    def sukkar_pos_people(self, customer_id):
        """Everyone this order could be delivered to: the customer with their
        own addresses, and every recipient they have sent a gift to before."""
        customer = self.browse(customer_id).exists()
        if not customer:
            return {"customer": False, "recipients": []}
        recipients = self.search([
            ("parent_id", "=", customer.id),
            ("sukkar_is_recipient", "=", True),
        ], order="name")
        person = lambda p: {
            "id": p.id,
            "name": p.name or "",
            "phone": p.phone or "",
            "email": p.email or "",
            "addresses": p._sukkar_addresses(),
        }
        return {
            "customer": person(customer),
            "recipients": [person(r) for r in recipients],
        }

    @api.model
    def sukkar_pos_create_recipient(self, customer_id, payload):
        """A new person this customer sends to, with their first address."""
        customer = self.browse(customer_id)
        recipient = self.create({
            "name": (payload.get("name") or "").strip(),
            "phone": (payload.get("phone") or "").strip() or False,
            "email": (payload.get("email") or "").strip() or False,
            "parent_id": customer.id,
            "type": "contact",
            "company_type": "person",
            "sukkar_is_recipient": True,
            "country_id": self.env.ref("base.kw").id,
        })
        address = payload.get("address")
        if address:
            self.sukkar_pos_add_address(recipient.id, address)
        return self.sukkar_pos_people(customer.id)

    @api.model
    def sukkar_pos_add_address(self, partner_id, address):
        """Another address for a customer or a recipient. The first one they
        get is their primary one; the shop can move that later."""
        partner = self.browse(partner_id)
        child = self.create(self._sukkar_address_vals(address, partner))
        if not self.search_count([
                ("parent_id", "=", partner.id), ("type", "=", "delivery"),
                ("sukkar_is_primary", "=", True), ("id", "!=", child.id)]):
            child.sukkar_is_primary = True
        # The card shows where they are, so the counter reads it without
        # opening the address.
        if not partner.sukkar_area_id and child.sukkar_area_id:
            partner.write({
                "sukkar_area_id": child.sukkar_area_id.id,
                "state_id": child.state_id.id,
                "city": child.city,
            })
        return child._sukkar_address_card()

    def sukkar_set_primary_address(self):
        """Make this address the customer's primary one; the others step down."""
        for address in self:
            siblings = self.search([
                ("parent_id", "=", address.parent_id.id),
                ("type", "=", "delivery"), ("id", "!=", address.id)])
            siblings.write({"sukkar_is_primary": False})
            address.sukkar_is_primary = True
        return True
