from odoo.exceptions import UserError
from odoo.tests import tagged

from .water_common import WaterCommon


@tagged("post_install", "-at_install")
class TestServiceInvoice(WaterCommon):
    def setUp(self):
        super().setUp()
        # tenant 1 is "Amina": 12 units at 110 = 1,320, generator 800, fixed 4,000
        self.meter = self._meter(self.units[0], "SM-A12", start=1240)
        self._read(self.meter, 1252)
        self._cycle()
        self.generator_product = self.env.ref("property_management.product_generator")
        self.sub = self.env["pm.generator.subscription"].create({"lease_id": self.leases[0].id, "on_plan": True})

    def _make(self):
        wizard = self.env["pm.service.invoice.wizard"].create({"building_id": self.building.id, "month": self.today})
        wizard.action_create()
        return wizard

    def _invoice(self, lease):
        return self.env["account.move"].search([("pm_lease_id", "=", lease.id), ("pm_kind", "=", "service_charge")])

    def test_invoice_adds_up_fixed_water_and_generator(self):
        self._make()
        invoice = self._invoice(self.leases[0])
        self.assertEqual(invoice.state, "draft")
        self.assertEqual(invoice.amount_total, 6120)
        self.assertEqual(invoice.pm_period_start, self.today.replace(day=1))

    def test_water_lines_are_kept_apart_from_the_other_costs(self):
        self._make()
        invoice = self._invoice(self.leases[0])
        self.assertTrue(invoice.pm_has_water)
        water = invoice.pm_water_line_ids
        self.assertEqual(len(water), 1)
        self.assertEqual((water.pm_prev_reading, water.pm_curr_reading, water.quantity, water.price_unit), (1240, 1252, 12, 110))
        self.assertEqual(water.price_subtotal, 1320)
        others = invoice.invoice_line_ids - water
        self.assertEqual(sorted(others.mapped("price_subtotal")), [800, 1000, 3000])
        self.assertFalse(any(others.mapped("pm_is_water")))

    def test_a_tenant_without_a_meter_gets_only_the_fixed_items(self):
        self._make()
        invoice = self._invoice(self.leases[1])
        self.assertEqual(invoice.amount_total, 4000)
        self.assertFalse(invoice.pm_has_water)

    def test_running_it_twice_does_not_bill_twice(self):
        self._make()
        second = self._make()
        self.assertEqual(len(self._invoice(self.leases[0])), 1)
        self.assertIn("already invoiced", second.summary)

    def test_readings_and_costs_are_locked_once_billed(self):
        self._make()
        reading = self.meter._last_reading()
        self.assertTrue(reading.billed)
        self.assertTrue(self.env["pm.water.cycle"].search([("building_id", "=", self.building.id)]).invoiced)

    def test_deleting_a_draft_invoice_frees_its_readings(self):
        self._make()
        self._invoice(self.leases[0]).unlink()
        self.assertFalse(self.meter._last_reading().billed)

    def test_water_costs_must_be_typed_before_invoicing(self):
        self.env["pm.water.cycle"].search([]).unlink()
        wizard = self.env["pm.service.invoice.wizard"].create({"building_id": self.building.id, "month": self.today})
        with self.assertRaises(UserError):
            wizard.action_create()

    def test_a_building_without_water_has_no_water_line(self):
        self.building.write({"uses_ncwsc": False, "uses_borehole": False})
        self._make()
        invoice = self._invoice(self.leases[0])
        self.assertFalse(invoice.pm_has_water)
        self.assertEqual(invoice.amount_total, 4800)

    # ----- generator plan -----
    def test_generator_switch_changes_a_draft_invoice(self):
        self._make()
        invoice = self._invoice(self.leases[0])
        self.sub.action_set_plan(False)
        self.assertEqual(invoice.amount_total, 5320)
        self.sub.action_set_plan(True)
        self.assertEqual(invoice.amount_total, 6120)

    def test_generator_choice_locks_once_the_invoice_is_paid(self):
        self._make()
        invoice = self._invoice(self.leases[0])
        invoice.action_post()
        self.env["pm.payment.wizard"].with_context(default_invoice_id=invoice.id).create(
            {"amount": invoice.amount_total}
        ).action_confirm()
        self.sub.invalidate_recordset()
        self.assertTrue(self.sub.locked)
        self.assertEqual(self.sub.status, "paid")
        with self.assertRaises(UserError):
            self.sub.action_set_plan(False)

    def test_generator_period_runs_from_rent_day_to_rent_day(self):
        from datetime import date

        start, end = self.env["pm.generator.subscription"]._period_for(5, date(2026, 10, 20))
        self.assertEqual((start, end), (date(2026, 10, 5), date(2026, 11, 4)))
        start, end = self.env["pm.generator.subscription"]._period_for(5, date(2026, 10, 3))
        self.assertEqual((start, end), (date(2026, 9, 5), date(2026, 10, 4)))

    def test_one_generator_record_per_lease(self):
        with self.assertRaises(Exception):
            self.env["pm.generator.subscription"].create({"lease_id": self.leases[0].id})
