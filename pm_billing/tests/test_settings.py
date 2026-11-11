from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBillingSettings(TransactionCase):
    def test_new_building_uses_the_saved_rent_rules(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("pm_billing.grace_days", "9")
        params.set_param("pm_billing.invoice_lead_days", "3")
        building = self.env["pm.building"].create({"name": "Test block", "address": "Nairobi"})
        self.assertEqual(building.grace_days, 9)
        self.assertEqual(building.invoice_lead_days, 3)

    def test_full_month_setting_turns_off_day_by_day_first_month(self):
        self.env["ir.config_parameter"].sudo().set_param("pm_billing.first_month", "full")
        building = self.env["pm.building"].create({"name": "Full block", "address": "Nairobi"})
        self.assertFalse(building.prorate_first_month)

    def test_days_stayed_is_the_default(self):
        self.env["ir.config_parameter"].sudo().search([("key", "=like", "pm_billing.%")]).unlink()
        building = self.env["pm.building"].create({"name": "Default block", "address": "Nairobi"})
        self.assertTrue(building.prorate_first_month)
        self.assertEqual(building.grace_days, 5)
