from odoo.addons.pm_billing.tests.water_common import WaterCommon
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestTodayWater(WaterCommon):
    def test_today_counts_meters_not_yet_read_this_month(self):
        meter = self._meter(self.units[0], "SM-1", 100)
        month = self.today.replace(day=1)
        self.assertIn(meter, self.env["pm.today"]._meters_without_reading(month))
        self._read(meter, 110)
        self.assertNotIn(meter, self.env["pm.today"]._meters_without_reading(month))

    def test_meter_missed_twice_in_a_row_is_listed(self):
        meter = self._meter(self.units[0], "SM-1", 90)
        for value in (100, 110, 120):
            self._read(meter, value)
        meter._make_estimate(self.today)
        self.assertNotIn(meter, self.env["pm.today"]._meters_missed_twice())
        meter._make_estimate(self.today)
        self.assertIn(meter, self.env["pm.today"]._meters_missed_twice())
