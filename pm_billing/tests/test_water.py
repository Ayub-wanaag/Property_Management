from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .water_common import WaterCommon


@tagged("post_install", "-at_install")
class TestWater(WaterCommon):
    # ----- the rate and the water lost -----
    def test_rate_and_water_lost(self):
        self._read(self._meter(self.units[0], "SM-1"), 100)
        self._read(self._meter(self.units[1], "SM-2"), 105)
        cycle = self._cycle()
        self.assertEqual(cycle.total_cost, 24200)
        self.assertEqual(cycle.billed_units, 205)
        self.assertEqual(cycle.rate, 110)
        self.assertEqual(cycle.loss_units, 15)
        self.assertAlmostEqual(cycle.loss_percent, 6.8, delta=0.05)

    def test_big_loss_does_not_change_the_rate(self):
        self._read(self._meter(self.units[0], "SM-1"), 100)
        self._read(self._meter(self.units[1], "SM-2"), 105)
        cycle = self._cycle(main=300)
        self.assertEqual(cycle.loss_units, 95)
        self.assertEqual(cycle.rate, round(24200 / 300, 2))

    def test_one_source_uses_only_that_cost(self):
        self.building.uses_borehole = False
        cycle = self._cycle()
        self.assertEqual(cycle.total_cost, 18200)

    def test_no_source_means_no_water_costs(self):
        self.building.write({"uses_ncwsc": False, "uses_borehole": False})
        with self.assertRaises(ValidationError):
            self._cycle()

    def test_costs_are_locked_after_invoicing(self):
        cycle = self._cycle()
        cycle.invoiced = True
        with self.assertRaises(UserError):
            cycle.ncwsc_cost = 1

    # ----- readings -----
    def test_reading_lower_than_last_time_is_blocked(self):
        meter = self._meter(self.units[0], "SM-1", start=100)
        self._read(meter, 120)
        with self.assertRaises(ValidationError):
            self._read(meter, 110)

    def test_meter_serial_can_be_any_text_but_not_repeated(self):
        self._meter(self.units[0], "plain text 42")
        with self.assertRaises(ValidationError):
            self._meter(self.units[1], "PLAIN TEXT 42")

    def test_replacing_a_meter_closes_the_old_one_and_allows_a_low_new_reading(self):
        old = self._meter(self.units[0], "SM-OLD", start=100)
        self._read(old, 120)
        self.env["pm.meter.swap.wizard"].create(
            {"meter_id": old.id, "final_reading": 130, "new_serial": "SM-NEW", "new_start_reading": 0}
        ).action_confirm()
        self.assertEqual(old.state, "replaced")
        self.assertEqual(old.final_reading, 130)
        self.assertEqual(old._last_reading().kind, "final")
        new = self.env["pm.meter"].search([("unit_id", "=", self.units[0].id), ("state", "=", "active")])
        self.assertEqual(new.serial, "SM-NEW")
        self.assertEqual(self._read(new, 5).units, 5)
        with self.assertRaises(ValidationError):
            self._read(old, 140)

    def test_a_swap_cannot_use_a_final_reading_below_the_last_one(self):
        old = self._meter(self.units[0], "SM-OLD", start=100)
        self._read(old, 120)
        with self.assertRaises(ValidationError):
            self.env["pm.meter.swap.wizard"].create(
                {"meter_id": old.id, "final_reading": 110, "new_serial": "SM-NEW"}
            ).action_confirm()

    # ----- estimates and true-up -----
    def test_estimate_then_true_up(self):
        meter = self._meter(self.units[0], "SM-1", start=90)
        for value in (100, 110, 120):
            self._read(meter, value)
        estimate = meter._make_estimate(self.today)
        self.assertEqual((estimate.kind, estimate.current, estimate.units), ("estimated", 130, 10))
        actual = self._read(meter, 145)
        # real use since the last real reading is 25, the estimate already billed 10, so 15 is left to bill
        self.assertEqual((actual.previous, actual.last_real, actual.units, actual.real_units), (130, 120, 15, 25))

    def test_true_up_can_be_a_credit(self):
        meter = self._meter(self.units[0], "SM-1", start=90)
        for value in (100, 110, 120):
            self._read(meter, value)
        meter._make_estimate(self.today)  # billed 10
        actual = self._read(meter, 125)   # really only used 5
        self.assertEqual(actual.units, -5)

    def test_no_history_means_no_estimate(self):
        meter = self._meter(self.units[0], "SM-1")
        self.assertFalse(meter._make_estimate(self.today))

    # ----- the readings sheet -----
    def _sheet(self, typed):
        sheet = self.env["pm.reading.sheet"].create({"building_id": self.building.id, "date": self.today})
        sheet.write(
            {"line_ids": [(0, 0, {"meter_id": meter.id, "unit_id": meter.unit_id.id, "serial": meter.serial, "current": value}) for meter, value in typed]}
        )
        return sheet

    def test_sheet_saves_what_was_typed(self):
        m1, m2 = self._meter(self.units[0], "SM-1", 100), self._meter(self.units[1], "SM-2", 200)
        self._sheet([(m1, 112), (m2, 0)]).action_save()
        self.assertEqual(self.env["pm.meter.reading"].search_count([("meter_id", "in", (m1 | m2).ids)]), 1)

    def test_sheet_blocks_a_lower_number_and_saves_nothing(self):
        m1, m2 = self._meter(self.units[0], "SM-1", 100), self._meter(self.units[1], "SM-2", 200)
        with self.assertRaises(UserError):
            self._sheet([(m1, 150), (m2, 190)]).action_save()
        self.assertFalse(self.env["pm.meter.reading"].search([("meter_id", "in", (m1 | m2).ids)]))

    def test_sheet_warns_about_a_much_higher_number_but_allows_it(self):
        meter = self._meter(self.units[0], "SM-1", 90)
        for value in (100, 110, 120):
            self._read(meter, value)
        sheet = self._sheet([(meter, 300)])
        self.assertTrue(sheet.line_ids.warning)
        sheet.action_save()
        self.assertEqual(meter._last_value(), 300)

    def test_sheet_can_estimate_the_missed_units(self):
        read, missed = self._meter(self.units[0], "SM-1", 90), self._meter(self.units[1], "SM-2", 90)
        for meter in (read, missed):
            for value in (100, 110, 120):
                self._read(meter, value)
        self._sheet([(read, 135), (missed, 0)]).action_estimate_missing()
        self.assertEqual(read._last_reading().kind, "actual")
        self.assertEqual(missed._last_reading().kind, "estimated")

    # ----- who can see what -----
    def test_caretaker_enters_readings_but_cannot_see_water_money(self):
        caretaker = self._user("water_caretaker", "group_pm_caretaker")
        meter = self._meter(self.units[0], "SM-1", 100)
        self.env["pm.meter.reading"].with_user(caretaker).create({"meter_id": meter.id, "current": 110})
        self._cycle()
        with self.assertRaises(AccessError):
            self.env["pm.water.cycle"].with_user(caretaker).search([])


    # ----- the readings sheet works from zero (no meters yet) -----
    def test_sheet_lists_lived_in_units_even_when_they_have_no_meter_yet(self):
        rows = self.env["pm.reading.sheet"]._line_values(self.building, self.today)
        self.assertEqual({row["unit_id"] for row in rows}, {self.units[0].id, self.units[1].id})  # W3 is vacant
        self.assertTrue(all(not row["meter_id"] for row in rows))

    def test_sheet_opens_already_filled_in(self):
        sheet = self.env["pm.reading.sheet"].with_context(default_building_id=self.building.id).create({"building_id": self.building.id})
        defaults = self.env["pm.reading.sheet"].with_context(default_building_id=self.building.id).default_get(["building_id", "date", "line_ids"])
        self.assertEqual(len(defaults["line_ids"]), 2)
        self.assertTrue(sheet)

    def test_a_vacant_unit_with_a_meter_is_listed_and_a_read_meter_is_not(self):
        vacant = self._meter(self.units[2], "SM-V")
        rows = self.env["pm.reading.sheet"]._line_values(self.building, self.today)
        self.assertIn(self.units[2].id, [row["unit_id"] for row in rows])
        self._read(vacant, 5)
        rows = self.env["pm.reading.sheet"]._line_values(self.building, self.today)
        self.assertNotIn(self.units[2].id, [row["unit_id"] for row in rows])

    def test_a_first_reading_starts_the_meter_and_bills_nothing(self):
        sheet = self.env["pm.reading.sheet"].create({"building_id": self.building.id, "date": self.today})
        sheet.write({"line_ids": [(0, 0, {"unit_id": self.units[0].id, "serial": "SM-FIRST", "current": 1240})]})
        action = sheet.action_save()
        meter = self.env["pm.meter"].search([("unit_id", "=", self.units[0].id)])
        self.assertEqual((meter.serial, meter.start_reading), ("SM-FIRST", 1240))
        self.assertFalse(meter.reading_ids)
        self.assertEqual(action["tag"], "display_notification")

    def test_a_first_reading_with_no_serial_gets_a_default_meter_number(self):
        sheet = self.env["pm.reading.sheet"].create({"building_id": self.building.id, "date": self.today})
        sheet.write({"line_ids": [(0, 0, {"unit_id": self.units[1].id, "serial": "", "current": 50})]})
        sheet.action_save()
        self.assertEqual(self.env["pm.meter"].search([("unit_id", "=", self.units[1].id)]).serial, "M-W2")

    def test_a_repeated_meter_number_on_the_sheet_is_refused_with_a_clear_message(self):
        self._meter(self.units[0], "SM-DUP")
        sheet = self.env["pm.reading.sheet"].create({"building_id": self.building.id, "date": self.today})
        sheet.write({"line_ids": [(0, 0, {"unit_id": self.units[1].id, "serial": "sm-dup", "current": 50})]})
        with self.assertRaises(ValidationError):
            sheet.action_save()

    def test_from_nothing_to_a_water_line_on_the_invoice(self):
        """The whole path a new user takes: first reading, next reading, water costs, invoices."""
        sheet = self.env["pm.reading.sheet"].create({"building_id": self.building.id, "date": self.today})
        sheet.write({"line_ids": [(0, 0, {"unit_id": self.units[0].id, "serial": "SM-A12", "current": 1240})]})
        sheet.action_save()
        meter = self.env["pm.meter"].search([("unit_id", "=", self.units[0].id)])
        self._read(meter, 1252)
        self._cycle()  # 24,200 over 220 units = 110 a unit
        wizard = self.env["pm.service.invoice.wizard"].create({"building_id": self.building.id, "month": self.today})
        wizard.action_create()
        invoice = self.env["account.move"].search([("pm_lease_id", "=", self.leases[0].id), ("pm_kind", "=", "service_charge")])
        water = invoice.pm_water_line_ids
        self.assertEqual((water.quantity, water.price_unit, water.price_subtotal), (12, 110, 1320))

    def test_the_replace_meter_button_opens_its_pop_up(self):
        meter = self._meter(self.units[0], "SM-OLD", start=100)
        self._read(meter, 120)
        action = meter.action_replace()
        wizard = self.env["pm.meter.swap.wizard"].browse(action["res_id"])
        self.assertEqual((wizard.meter_id, wizard.final_reading), (meter, 120))
        with self.assertRaises(UserError):
            wizard.action_confirm()  # no new meter number typed yet
