from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarCustomerCreation(TransactionCase):
    """Creating a customer at the till, and the address that comes with them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Partner = cls.env["res.partner"]
        cls.hawalli = cls.env["res.country.state"].search(
            [("code", "=", "HA"), ("country_id.code", "=", "KW")], limit=1)
        cls.area = cls.env["sukkar.area"].search(
            [("state_id", "=", cls.hawalli.id)], limit=1)

    # ------------------------------------------------------------- the areas
    def test_every_governorate_has_its_areas(self):
        Area = self.env["sukkar.area"]
        self.assertGreaterEqual(Area.search_count([]), 190,
                                "Kuwait's areas should be loaded")
        for code in ("KU", "HA", "FA", "MU", "AH", "JA"):
            state = self.env["res.country.state"].search(
                [("code", "=", code), ("country_id.code", "=", "KW")], limit=1)
            self.assertTrue(
                Area.search_count([("state_id", "=", state.id)]),
                f"{code} must have areas, or the cashier cannot pick one")

    def test_an_area_carries_both_names(self):
        salmiya = self.env["sukkar.area"].search([("name", "=", "Salmiya")], limit=1)
        self.assertTrue(salmiya, "Salmiya is one of the busiest areas")
        self.assertEqual(salmiya.name_ar, "السالمية")
        self.assertEqual(salmiya.state_id.code, "HA")
        self.assertIn("السالمية", salmiya.display_name)

    def test_loading_twice_does_not_double_the_list(self):
        Area = self.env["sukkar.area"]
        before = Area.search_count([])
        Area._sukkar_load_areas()
        self.assertEqual(Area.search_count([]), before,
                         "an upgrade must not add the same areas again")

    # -------------------------------------------------------- the customer
    def test_a_customer_can_be_saved_with_no_address(self):
        partner = self.Partner.browse(self.Partner.sukkar_create_from_pos({
            "name": "Walk In", "phone": "99001122", "email": ""}))
        self.assertEqual(partner.name, "Walk In")
        self.assertEqual(partner.phone, "99001122")
        self.assertFalse(partner.email)
        self.assertEqual(partner.company_type, "person",
                         "the till saves a person, never a company")
        self.assertFalse(partner.child_ids)

    def test_the_address_is_a_real_delivery_address(self):
        partner = self.Partner.browse(self.Partner.sukkar_create_from_pos({
            "name": "Noura", "phone": "99887766", "email": "noura@example.com",
            "address": {
                "label": "Home", "contact_name": "", "contact_phone": "",
                "contact_email": "", "area_id": self.area.id,
                "block": "6", "street": "12", "avenue": "3",
                "building": "9", "floor": "2", "apartment": "4",
                "directions": "Beside the mosque",
            }}))
        address = partner.child_ids
        self.assertEqual(len(address), 1)
        self.assertEqual(address.type, "delivery",
                         "so every delivery screen already understands it")
        self.assertEqual(address.sukkar_label, "Home")
        self.assertEqual(address.sukkar_area_id, self.area)
        self.assertEqual(address.state_id, self.hawalli,
                         "the area decides the governorate")
        self.assertEqual(address.city, self.area.name)
        self.assertIn("Block 6", address.street)
        self.assertIn("Apt 4", address.street2)
        self.assertEqual(address.sukkar_directions, "Beside the mosque")
        self.assertTrue(address.sukkar_is_primary,
                        "the first address a customer gets is the primary one")

    def test_the_address_contact_defaults_to_the_customer_but_can_differ(self):
        partner = self.Partner.browse(self.Partner.sukkar_create_from_pos({
            "name": "Ahmad", "phone": "99110011", "email": "",
            "address": {"label": "Home", "area_id": self.area.id,
                        "contact_name": "", "contact_phone": ""}}))
        self.assertEqual(partner.child_ids.name, "Ahmad",
                         "left blank, the address is the customer's own")
        self.assertEqual(partner.child_ids.phone, "99110011")

        other = self.Partner.browse(self.Partner.sukkar_create_from_pos({
            "name": "Ahmad Two", "phone": "99110012", "email": "",
            "address": {"label": "Mum", "area_id": self.area.id,
                        "contact_name": "Um Ahmad", "contact_phone": "97000001"}}))
        self.assertEqual(other.child_ids.name, "Um Ahmad",
                         "whoever receives there may not be the one who pays")
        self.assertEqual(other.child_ids.phone, "97000001")

    def test_only_one_address_is_primary_at_a_time(self):
        partner = self.Partner.browse(self.Partner.sukkar_create_from_pos({
            "name": "Two Homes", "phone": "99220022", "email": "",
            "address": {"label": "Home", "area_id": self.area.id}}))
        first = partner.child_ids
        second = self.Partner.create(self.Partner._sukkar_address_vals(
            {"label": "Office", "area_id": self.area.id}, partner))
        second.sukkar_set_primary_address()
        first.invalidate_recordset()
        self.assertTrue(second.sukkar_is_primary)
        self.assertFalse(first.sukkar_is_primary,
                         "a customer has one primary address, not two")
