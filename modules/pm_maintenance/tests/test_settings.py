from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMaintenanceSettings(TransactionCase):
    def test_new_building_uses_the_saved_approval_limit(self):
        self.env["ir.config_parameter"].sudo().set_param("pm_maintenance.approval_threshold", "20000")
        building = self.env["pm.building"].create({"name": "Test block", "address": "Nairobi"})
        self.assertEqual(building.sudo().approval_threshold, 20000.0)

    def test_no_approval_needed_when_nothing_is_saved(self):
        self.env["ir.config_parameter"].sudo().search([("key", "=like", "pm_maintenance.%")]).unlink()
        building = self.env["pm.building"].create({"name": "Plain block", "address": "Nairobi"})
        self.assertEqual(building.sudo().approval_threshold, 0.0)
