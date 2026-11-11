from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPropertySettings(TransactionCase):
    def test_new_building_uses_the_saved_defaults(self):
        self.env["ir.config_parameter"].sudo().set_param("pm_property.deposit_refund_days", "21")
        self.env["ir.config_parameter"].sudo().set_param("pm_property.max_photo_mb", "8")
        building = self.env["pm.building"].create({"name": "Test block", "address": "Nairobi"})
        self.assertEqual(building.deposit_refund_days, 21)
        self.assertEqual(building.max_photo_mb, 8)

    def test_new_building_falls_back_when_nothing_is_saved(self):
        params = self.env["ir.config_parameter"].sudo()
        params.search([("key", "=like", "pm_property.%")]).unlink()
        building = self.env["pm.building"].create({"name": "Plain block", "address": "Nairobi"})
        self.assertEqual(building.deposit_refund_days, 14)
        self.assertEqual(building.max_photo_mb, 5)

    def test_saving_settings_stores_the_value(self):
        self.env["res.config.settings"].create({"pm_notice_days": 45}).execute()
        self.assertEqual(self.env["ir.config_parameter"].sudo().get_param("pm_property.notice_days"), "45")
