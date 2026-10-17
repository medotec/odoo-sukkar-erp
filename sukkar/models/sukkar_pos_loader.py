from odoo import api, models


class PosSession(models.Model):
    """What the register loads at startup.

    One place, not six: every Sukkar model and field the register needs is
    listed here, so no two pieces of register work can fight over the same
    loading list.
    """
    _inherit = "pos.session"

    @api.model
    def _load_pos_data_models(self, config_id):
        return super()._load_pos_data_models(config_id) + ["sukkar.cake.flavour"]


class ResCompany(models.Model):
    _inherit = "res.company"

    @api.model
    def _load_pos_data_fields(self, config_id):
        # The register reads its limits from the company settings, never from code.
        return super()._load_pos_data_fields(config_id) + [
            "sukkar_card_message_max",
            "sukkar_default_flavour_id",
            "sukkar_pos_fee_amounts",
            "sukkar_pos_fee_product_id",
        ]


class ProductProduct(models.Model):
    _inherit = "product.product"

    @api.model
    def _load_pos_data_fields(self, config_id):
        # Arabic first on the register: every product brings its Arabic name
        # and size. product.product publishes a real list, so ours is added.
        return super()._load_pos_data_fields(config_id) + [
            "sukkar_name_ar",
            "sukkar_variant_ar",
        ]


class PosCategory(models.Model):
    _inherit = "pos.category"

    @api.model
    def _load_pos_data_fields(self, config_id):
        return super()._load_pos_data_fields(config_id) + ["sukkar_name_ar"]


# pos.order is deliberately NOT listed here. Odoo gives the order an EMPTY
# field list, and an empty list means "send every field" — so the register
# already receives sukkar_order_type, sukkar_delivery_fee and the rest without
# being told. Naming them turns the empty list into a list of three, and the
# order arrives stripped of everything else: the payments among them, which
# the register needs to total an order. It then hangs on the loading screen
# before it ever reaches the products. (Found 20 Sep 2026, the day the shop
# could not open the till.)


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    @api.model
    def _load_pos_data_fields(self, config_id):
        return super()._load_pos_data_fields(config_id) + [
            "sukkar_flavour_json",
            "sukkar_flavour_summary",
            "sukkar_note_image",
        ]
