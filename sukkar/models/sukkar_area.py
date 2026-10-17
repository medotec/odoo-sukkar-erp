from odoo import api, fields, models


class SukkarArea(models.Model):
    """A Kuwaiti area (منطقة), inside its governorate.

    The cashier picks the governorate, then the area from the areas that
    governorate actually contains — so an address can never name an area that
    does not exist, or put a real area in the wrong governorate. The rest of
    the address (block, street, building) is typed: Kuwait publishes no open
    list of blocks and streets complete enough to choose from, and a list that
    is wrong is worse than a box.

    Loaded from the published PACI area list, both names, with the official
    area number kept so the data can be reconciled later.
    """
    _name = "sukkar.area"
    _description = "Kuwait Area"
    _order = "state_id, name"
    _rec_names_search = ["name", "name_ar"]

    name = fields.Char(string="Area (English)", required=True, index=True)
    name_ar = fields.Char(string="Area (Arabic)", required=True, index=True)
    state_id = fields.Many2one(
        "res.country.state", string="Governorate",
        required=True, ondelete="cascade", index=True,
        domain="[('country_id.code', '=', 'KW')]")
    sukkar_delivery_fee = fields.Float(
        string="Delivery Fee (KWD)", digits="Product Price",
        help="What the shop charges to drive an order to this area. The "
             "register fills it in the moment the area is picked; the cashier "
             "can still override it. From the team's own price list "
             "(29 Sep 2026) — an area the shop does not deliver to is "
             "archived rather than priced.")
    paci_no = fields.Integer(
        string="PACI Area No.",
        help="The area's official number, kept so this list can be checked "
             "against PACI's own.")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("sukkar_area_unique", "unique(state_id, name)",
         "That area already exists in this governorate."),
    ]

    @api.depends("name", "name_ar")
    def _compute_display_name(self):
        # Both names, always: the cashier reads Arabic, the reports print both.
        for area in self:
            area.display_name = " · ".join(p for p in (area.name, area.name_ar) if p)

    # ------------------------------------------------------------------ loading
    @api.model
    def _sukkar_load_areas(self):
        """Load Kuwait's areas from the file shipped beside this module.

        Matched to the governorate by its two-letter code rather than by an XML
        id: the six governorates were created here before this module existed
        and carry no XML id of their own, and a fresh database may not have them
        until the delivery setup runs. Idempotent — it fills what is missing and
        leaves anything the shop has edited alone.
        """
        import json
        from odoo.modules.module import get_resource_path

        path = get_resource_path("sukkar", "data", "kuwait_areas.json")
        if not path:
            return False
        with open(path, encoding="utf-8") as handle:
            areas = json.load(handle)

        kuwait = self.env["res.country"].search([("code", "=", "KW")], limit=1)
        states = {
            state.code: state
            for state in self.env["res.country.state"].search(
                [("country_id", "=", kuwait.id)])
        }
        # A fresh database has no Kuwait governorates: create the six missing ones
        governorates = {
            "KU": "Al Asimah", "HA": "Hawalli", "FA": "Al Farwaniyah",
            "MU": "Mubarak Al-Kabeer", "AH": "Al Ahmadi", "JA": "Al Jahra",
        }
        for code, name in governorates.items():
            if kuwait and code not in states:
                states[code] = self.env["res.country.state"].create(
                    {"name": name, "code": code, "country_id": kuwait.id})
        existing = {
            (area.state_id.code, (area.name or "").strip().lower()): area
            for area in self.with_context(active_test=False).search([])
        }
        created = 0
        for row in areas:
            state = states.get(row["gov"])
            if not state:
                continue
            if (row["gov"], row["en"].strip().lower()) in existing:
                continue
            self.create({
                "name": row["en"], "name_ar": row["ar"],
                "state_id": state.id, "paci_no": row.get("paci_no") or 0,
            })
            created += 1
        return created
