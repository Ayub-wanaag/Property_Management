from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSetupScreens(TransactionCase):
    def _guide(self, **extra):
        """A setup guide that already did step 1."""
        owner = self.env["pm.owner"].search([("is_default", "=", True)], limit=1)
        guide = self.env["pm.setup"].create({"building_name": "Screens Plaza", "owner_id": owner.id, **extra})
        guide._save_building()
        return guide

    def test_steps_that_need_a_building_say_so(self):
        guide = self.env["pm.setup"].create({})
        for saver in (guide._save_units, guide._save_water, guide._save_payment, guide._save_help):
            with self.assertRaises(UserError):
                saver()

    def test_unit_types_are_created_once_and_never_overwritten(self):
        guide = self._guide()
        guide.type_line_ids = [(0, 0, {"name": "Screen Bedsitter", "default_rent": 8000}), (0, 0, {"name": "  ", "default_rent": 1})]
        guide._save_unit_types()
        guide._save_unit_types()  # pressing Next twice
        types = self.env["pm.unit.type"].search([("name", "=", "Screen Bedsitter")])
        self.assertEqual(len(types), 1)
        self.assertEqual(types.default_rent, 8000)
        guide.type_line_ids[0].default_rent = 9999
        guide._save_unit_types()
        self.assertEqual(types.default_rent, 8000)
        self.assertTrue(types.product_id.sale_ok)

    def test_bulk_create_a1_to_a24_on_four_floors(self):
        unit_type = self.env["pm.unit.type"].create({"name": "Screen 2 bed", "default_rent": 18000})
        guide = self._guide(units_prefix="A", units_first=1, units_last=24, units_floors=4, units_type_id=unit_type.id)
        guide._save_units()
        units = self.env["pm.unit"].search([("building_id", "=", guide.building_id.id)])
        self.assertEqual(len(units), 24)
        floors = {}
        for unit in units:
            floors.setdefault(unit.floor, []).append(unit.number)
        self.assertEqual(sorted(floors), [1, 2, 3, 4])
        self.assertEqual(floors[1], ["A1", "A2", "A3", "A4", "A5", "A6"])
        self.assertEqual(floors[4][-1], "A24")
        guide._save_units()  # again: nothing is created twice
        self.assertEqual(self.env["pm.unit"].search_count([("building_id", "=", guide.building_id.id)]), 24)

    def test_bulk_create_checks_its_input(self):
        unit_type = self.env["pm.unit.type"].create({"name": "Screen check", "default_rent": 1})
        with self.assertRaises(UserError):
            self._guide(units_first=5, units_last=2, units_type_id=unit_type.id)._save_units()
        with self.assertRaises(UserError):
            self._guide(units_first=1, units_last=100000, units_type_id=unit_type.id)._save_units()

    def test_uneven_floors_do_not_lose_units(self):
        unit_type = self.env["pm.unit.type"].create({"name": "Screen uneven", "default_rent": 1})
        guide = self._guide(units_prefix="B", units_first=1, units_last=10, units_floors=3, units_type_id=unit_type.id)
        guide._save_units()
        self.assertEqual(self.env["pm.unit"].search_count([("building_id", "=", guide.building_id.id)]), 10)

    def test_water_choice_sets_the_building(self):
        for choice, ncwsc, borehole in (("ncwsc", True, False), ("borehole", False, True), ("both", True, True), ("neither", False, False)):
            guide = self._guide(water_choice=choice)
            guide._save_water()
            self.assertEqual((guide.building_id.uses_ncwsc, guide.building_id.uses_borehole), (ncwsc, borehole), choice)

    def test_service_charge_items_make_a_sellable_product_and_a_charge(self):
        unit_type = self.env["pm.unit.type"].create({"name": "Screen charge", "default_rent": 1})
        guide = self._guide()
        guide.charge_line_ids = [(0, 0, {"unit_type_id": unit_type.id, "item": "Security", "amount": 500})]
        guide._save_service()
        guide._save_service()
        charge = unit_type.charge_ids
        self.assertEqual((len(charge), charge.amount), (1, 500))
        self.assertTrue(charge.product_id.sale_ok)
        guide.charge_line_ids[0].amount = 650
        guide._save_service()
        self.assertEqual((len(unit_type.charge_ids), unit_type.charge_ids.amount), (1, 650))

    def test_payment_methods_and_help_contacts(self):
        guide = self._guide()
        guide.provider_line_ids = [(0, 0, {"type": "mpesa_paybill", "name": "M-Pesa Paybill", "details": "Paybill 123456"})]
        guide.help_line_ids = [(0, 0, {"label": "Caretaker", "phone": "0700 111 222", "hours": "8 to 6"})]
        for _i in range(2):
            guide._save_payment()
            guide._save_help()
        self.assertEqual(guide.building_id.provider_ids.mapped("name"), ["M-Pesa Paybill"])
        self.assertEqual(guide.building_id.help_contact_ids.mapped("phone"), ["0700 111 222"])

    def test_first_tenant_gets_a_started_lease_once(self):
        unit_type = self.env["pm.unit.type"].create({"name": "Screen tenant", "default_rent": 5000})
        guide = self._guide(units_prefix="T", units_first=1, units_last=2, units_floors=1, units_type_id=unit_type.id)
        guide._save_units()
        unit = self.env["pm.unit"].search([("building_id", "=", guide.building_id.id), ("number", "=", "T1")])
        guide.write({"tenant_name": "Screen Tenant", "tenant_phone": "0700", "tenant_unit_id": unit.id})
        guide._save_tenant()
        guide._save_tenant()
        lease = guide.tenant_lease_id
        self.assertEqual((lease.state, lease.rent_amount, unit.status), ("active", 5000, "occupied"))
        self.assertEqual(self.env["pm.lease"].search_count([("unit_id", "=", unit.id)]), 1)

    def test_first_tenant_needs_a_unit(self):
        guide = self._guide(tenant_name="No Unit")
        with self.assertRaises(UserError):
            guide._save_tenant()

    def test_next_moves_through_all_eight_steps(self):
        unit_type = self.env["pm.unit.type"].create({"name": "Screen walk", "default_rent": 1})
        guide = self._guide(units_first=1, units_last=3, units_type_id=unit_type.id)
        guide.step = "unit_types"
        for _i in range(7):
            guide.action_next()
        self.assertEqual(guide.state, "done")
