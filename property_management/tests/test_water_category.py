from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .water_common import WaterCommon


@tagged("post_install", "-at_install")
class TestWaterCategory(WaterCommon):
    def _category(self, xmlid="property_management.pm_water_category_domestic"):
        return self.env.ref(xmlid)

    def test_domestic_tiers_add_up(self):
        # 12 units: water 6 x 68 + 6 x 85 = 918. Sewerage on 75% = 9 units: 6 x 58 + 3 x 65 = 543.
        water, sewerage = self._category()._charge(12)
        self.assertEqual((water, sewerage), (918, 543))

    def test_flat_category_charges_one_price(self):
        water, sewerage = self._category("property_management.pm_water_category_mdu")._charge(100)
        self.assertEqual((water, sewerage), (8500, 4875))

    def test_borehole_category_charges_sewerage_only_on_all_units(self):
        category = self.env["pm.water.category"].create(
            {
                "name": "Test BH",
                "is_borehole": True,
                "tier_ids": [(0, 0, {"tier_type": "sewerage", "from_unit": 1, "to_unit": 999999, "price_per_unit": 50})],
            }
        )
        self.assertEqual(category._charge(10), (0.0, 500.0))

    def test_nothing_used_costs_nothing(self):
        self.assertEqual(self._category()._charge(0), (0.0, 0.0))

    def test_a_category_without_prices_cannot_bill(self):
        empty = self.env["pm.water.category"].create({"name": "Test empty"})
        with self.assertRaises(ValidationError):
            empty._check_ready()

    def test_a_tier_cannot_end_before_it_starts(self):
        with self.assertRaises(ValidationError):
            self.env["pm.water.category.tier"].create(
                {"category_id": self._category().id, "tier_type": "water", "from_unit": 10, "to_unit": 5, "price_per_unit": 1}
            )

    def test_unit_category_beats_building_category(self):
        self.building.water_category_id = self._category()
        shop = self._category("property_management.pm_water_category_commercial")
        self.units[0].water_category_id = shop
        self.assertEqual(self.units[0]._water_category(), shop)
        self.assertEqual(self.units[1]._water_category(), self._category())

    def test_invoice_is_billed_by_the_category_not_the_cycle_rate(self):
        self.building.water_category_id = self._category()
        meter = self._meter(self.units[0], "CAT-A1", start=1240)
        self._read(meter, 1252)
        wizard = self.env["pm.service.invoice.wizard"].create({"building_id": self.building.id, "month": self.today})
        wizard.action_create()  # no water cost typed: the category does not need one
        invoice = self.env["account.move"].search([("pm_lease_id", "=", self.leases[0].id), ("pm_kind", "=", "service_charge")])
        water = invoice.pm_water_line_ids
        self.assertEqual(water.quantity, 12)
        self.assertAlmostEqual(water.price_subtotal, 1461, delta=0.1)  # 918 water + 543 sewerage

    def test_building_generator_price_edits_the_product(self):
        product = self.env["product.product"].search([("pm_subscription", "=", True)], limit=1)
        self.building.generator_product_id = product
        self.building.generator_price = 950
        self.assertEqual(product.list_price, 950)
        self.assertEqual(self.building.generator_price, 950)

    def test_new_generator_plan_uses_the_buildings_product(self):
        product = self.env["product.product"].search([("pm_subscription", "=", True)], limit=1)
        self.building.generator_product_id = product
        found = self.env["pm.generator.subscription"]._default_product(self.building)
        self.assertEqual(found, product)
