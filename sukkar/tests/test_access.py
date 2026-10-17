from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "sukkar")
class TestSukkarDriverAccess(TransactionCase):
    """A driver login exists so a name can go on a run sheet.

    Odoo copies the Default User Template's groups onto any user created without
    an explicit list, and in a shop that runs payroll and accounting that means a
    delivery driver arrives holding both: every wage in the shop, the chart of
    accounts, journal entries and the customer list.
    """

    DANGEROUS = [
        "payroll.group_payroll_manager",
        "payroll.group_payroll_user",
        "account.group_account_manager",
        "account.group_account_user",
        "hr.group_hr_manager",
        "base.group_system",
    ]

    def _driver(self, login="sukkar.test.driver"):
        return self.env["res.users"].create({
            "name": "Test Driver", "login": login, "sukkar_is_driver": True})

    def test_a_new_driver_login_holds_no_payroll_or_accounting_rights(self):
        driver = self._driver()
        self.assertTrue(driver.has_group("base.group_user"),
                        "he still has to sign in and see his run sheet")
        for xmlid in self.DANGEROUS:
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if not group:
                continue
            self.assertFalse(
                driver.has_group(xmlid),
                "a delivery driver must not hold %s" % group.name)

    def test_ticking_delivery_driver_takes_the_rights_away(self):
        """The four live driver logins were made before this guard existed."""
        user = self.env["res.users"].create({
            "name": "Was An Admin", "login": "sukkar.test.exadmin",
            "groups_id": [(4, self.env.ref("account.group_account_manager").id)]})
        self.assertTrue(user.has_group("account.group_account_manager"))
        user.sukkar_is_driver = True
        self.assertFalse(user.has_group("account.group_account_manager"))
        self.assertFalse(user.has_group("account.group_account_user"))

    def test_the_repair_pass_finds_every_driver(self):
        driver = self._driver(login="sukkar.test.driver.repair")
        # Slip the group past the guard the way an upgrade pass would.
        driver.sudo().with_context(sukkar_driver_groups=True).write(
            {"groups_id": [(4, self.env.ref("payroll.group_payroll_manager").id)]})
        self.assertTrue(driver.has_group("payroll.group_payroll_manager"))
        self.env["res.users"]._sukkar_repair_driver_groups()
        self.assertFalse(driver.has_group("payroll.group_payroll_manager"),
                         "an upgrade must never leave a driver in payroll")

    def test_the_accounting_pass_does_not_reach_drivers(self):
        """Every install and upgrade gives full accounting to the billing
        administrators. It must not be the way a driver gets it back."""
        driver = self._driver(login="sukkar.test.driver.books")
        driver.sudo().with_context(sukkar_driver_groups=True).write(
            {"groups_id": [(4, self.env.ref("account.group_account_manager").id)]})
        self.env["sukkar.books.setup"]._setup_accountant_access(self.env.company)
        self.assertFalse(driver.has_group("account.group_account_user"))
