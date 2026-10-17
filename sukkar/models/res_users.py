from odoo import api, fields, models

# Rights a delivery driver must never hold. A driver login exists so a name can
# be put on a run sheet; left as Odoo creates it, a new user copies the Default
# User Template and arrives holding payroll, the chart of accounts and every
# admin menu in the shop.
DRIVER_FORBIDDEN_GROUPS = [
    "base.group_system",
    "base.group_erp_manager",
    "base.group_allow_export",
    "account.group_account_manager",
    "account.group_account_user",
    "account.group_account_readonly",
    "account.group_account_invoice",
    "payroll.group_payroll_manager",
    "payroll.group_payroll_user",
    "hr.group_hr_manager",
    "hr.group_hr_user",
    "hr_contract.group_hr_contract_manager",
    "hr_contract.group_hr_contract_employee_manager",
    "purchase.group_purchase_user",
    "product.group_product_manager",
    "base.group_partner_manager",
    "hr_expense.group_hr_expense_manager",
    "hr_expense.group_hr_expense_user",
    "hr_holidays.group_hr_holidays_manager",
    "hr_holidays.group_hr_holidays_user",
    "hr_attendance.group_hr_attendance_manager",
    "hr_attendance.group_hr_attendance_officer",
    "stock.group_stock_manager",
    "purchase.group_purchase_manager",
    "mrp.group_mrp_manager",
    "point_of_sale.group_pos_manager",
    "sales_team.group_sale_manager",
]


class ResUsers(models.Model):
    """Configuration, not code: who counts as a delivery driver is a checkbox
    on the user (Settings → Users), and every driver dropdown in the system —
    Dispatch, the register's Delivery popup — reads it.

    Ticking that box also LIMITS the login: a driver carries cakes, he does not
    read wages or post journal entries.
    """
    _inherit = "res.users"

    sukkar_is_driver = fields.Boolean(
        string="Delivery Driver",
        help="Shown in the driver dropdowns on the Dispatch screen and the "
             "register's Delivery popup. A driver login is also kept out of "
             "payroll, the accounts and the admin menus.")

    @api.model
    def _sukkar_forbidden_driver_groups(self):
        """The groups above, plus every group that hands one of them over.

        Odoo groups imply one another: taking "Manage all employees" away from a
        driver achieves nothing while he still holds a group that grants it. So
        the list is closed over implication — and base.group_user is put back at
        the end, because without it he cannot sign in at all.
        """
        Groups = self.env["res.groups"]
        forbidden = Groups
        for xmlid in DRIVER_FORBIDDEN_GROUPS:
            forbidden |= self.env.ref(xmlid, raise_if_not_found=False) or Groups
        while True:
            grantors = Groups.search([("implied_ids", "in", forbidden.ids)]) - forbidden
            if not grantors:
                break
            forbidden |= grantors
        return forbidden - (self.env.ref("base.group_user", raise_if_not_found=False)
                            or Groups)

    def _sukkar_strip_driver_groups(self):
        """A driver login keeps the plain internal-user rights and nothing else.

        The groups are REPLACED rather than subtracted: taking a group away one
        at a time lets Odoo put it straight back through another group that
        implies it, and the pass then chases its own tail.
        """
        if self.env.context.get("sukkar_driver_groups"):
            return
        forbidden = self._sukkar_forbidden_driver_groups()
        if not forbidden:
            return
        for user in self.sudo().filtered("sukkar_is_driver"):
            keep = user.groups_id - forbidden
            if keep != user.groups_id:
                user.with_context(sukkar_driver_groups=True).write(
                    {"groups_id": [(6, 0, keep.ids)]})

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        users._sukkar_strip_driver_groups()
        return users

    def write(self, vals):
        res = super().write(vals)
        if "sukkar_is_driver" in vals or "groups_id" in vals:
            self._sukkar_strip_driver_groups()
        return res

    @api.model
    def _sukkar_repair_driver_groups(self):
        """Run on every install and upgrade: no driver keeps admin rights.

        Odoo copies the Default User Template's groups onto any user created
        without an explicit list, so logins made before this guard existed are
        already holding them.
        """
        self.with_context(active_test=False).search(
            [("sukkar_is_driver", "=", True)])._sukkar_strip_driver_groups()
        return True
