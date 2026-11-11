from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class WaterCommon(AccountTestInvoicingCommon):
    """Shared set-up: one building with both water sources, three units, two with a lease."""

    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)
        cls.env.user.groups_id |= cls.env.ref("pm_property.group_pm_manager")
        partner = cls.env["res.partner"].create({"name": "Water Owner"})
        cls.owner = cls.env["pm.owner"].create({"partner_id": partner.id})
        cls.building = cls.env["pm.building"].create(
            {"name": "Water Plaza", "owner_id": cls.owner.id, "uses_ncwsc": True, "uses_borehole": True}
        )
        cls.unit_type = cls.env["pm.unit.type"].create({"name": "Water type", "default_rent": 1000})
        cls.security = cls.env["product.product"].create({"name": "Water test security", "detailed_type": "service"})
        cls.cleaning = cls.env["product.product"].create({"name": "Water test cleaning", "detailed_type": "service"})
        cls.env["pm.unit.type.charge"].create({"unit_type_id": cls.unit_type.id, "product_id": cls.security.id, "amount": 3000})
        cls.env["pm.unit.type.charge"].create({"unit_type_id": cls.unit_type.id, "product_id": cls.cleaning.id, "amount": 1000})
        cls.units = cls.env["pm.unit"].create(
            [{"building_id": cls.building.id, "number": "W%s" % i, "floor": 1, "unit_type_id": cls.unit_type.id} for i in range(1, 4)]
        )
        cls.tenants = cls.env["res.partner"].create([{"name": "Water Tenant %s" % i} for i in range(1, 3)])
        cls.leases = cls.env["pm.lease"].create(
            [
                {"unit_id": cls.units[i].id, "tenant_id": cls.tenants[i].id, "state": "active", "rent_amount": 1000}
                for i in range(2)
            ]
        )
        cls.units[0].status = "occupied"
        cls.units[1].status = "occupied"
        cls.today = fields.Date.context_today(cls.env["pm.meter"])

    def _meter(self, unit, serial, start=0.0):
        return self.env["pm.meter"].create({"unit_id": unit.id, "serial": serial, "start_reading": start})

    def _read(self, meter, current, kind="actual"):
        return self.env["pm.meter.reading"].create(
            {"meter_id": meter.id, "date": self.today, "kind": kind, "current": current}
        )

    def _cycle(self, ncwsc=18200, borehole=6000, main=220):
        return self.env["pm.water.cycle"].create(
            {"building_id": self.building.id, "month": self.today, "ncwsc_cost": ncwsc, "borehole_cost": borehole, "main_meter_units": main}
        )

    def _user(self, login, group):
        return self.env["res.users"].with_context(no_reset_password=True).create(
            {"login": login, "name": login, "password": "Passw0rd!x", "groups_id": [(6, 0, [self.env.ref("pm_property." + group).id])]}
        )
