from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTenantLogin(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        owner_partner = cls.env["res.partner"].create({"name": "Test Owner"})
        owner = cls.env["pm.owner"].create({"partner_id": owner_partner.id})
        cls.building = cls.env["pm.building"].create({"name": "Test Plaza", "owner_id": owner.id})
        unit_type = cls.env["pm.unit.type"].create({"name": "Test 2 bedroom", "default_rent": 18000})
        cls.unit = cls.env["pm.unit"].create(
            {"building_id": cls.building.id, "number": "T1", "floor": 1, "unit_type_id": unit_type.id}
        )
        cls.first = cls.env["res.partner"].create({"name": "First Tenant"})
        cls.second = cls.env["res.partner"].create({"name": "Second Tenant"})
        cls.lease = cls.env["pm.lease"].create(
            {"unit_id": cls.unit.id, "tenant_id": cls.first.id, "state": "active"}
        )

    def _make_login(self, lease, password="abc123"):
        wizard = self.env["pm.lease.login.wizard"].create({"lease_id": lease.id})
        wizard.password = password
        wizard.action_create()
        return lease.tenant_user_id

    def test_login_belongs_to_the_tenant(self):
        user = self._make_login(self.lease)
        self.assertEqual(user.partner_id, self.first)
        self.assertTrue(user.share)

    def test_sign_in_by_name_and_building_name_or_unit(self):
        user = self._make_login(self.lease)
        find = self.env["pm.lease"]._tenant_login_for
        self.assertEqual(find("  first TENANT ", "test plaza"), user.login)
        self.assertEqual(find("First Tenant", "T1"), user.login)
        self.assertFalse(find("First Tenant", "Another Building"))
        self.assertFalse(find("Someone Else", "Test Plaza"))
        self.assertFalse(find("", "Test Plaza"))

    def test_changing_the_tenant_retires_the_old_login(self):
        old_user = self._make_login(self.lease)
        self.lease.tenant_id = self.second
        self.assertFalse(self.lease.tenant_user_id)
        self.assertFalse(old_user.active)
        self.assertFalse(self.env["pm.lease"]._tenant_login_for("First Tenant", "Test Plaza"))
        new_user = self._make_login(self.lease, "xyz789")
        self.assertEqual(new_user.partner_id, self.second)
        self.assertNotEqual(new_user, old_user)
        self.assertEqual(self.env["pm.lease"]._tenant_login_for("Second Tenant", "T1"), new_user.login)

    def test_a_login_that_belongs_to_someone_else_is_never_reused(self):
        user = self._make_login(self.lease)
        # simulate older data: the tenant on the lease changed without the login being touched
        self.lease.with_context(tracking_disable=True).write({"tenant_user_id": user.id})
        self.env.cr.execute("UPDATE pm_lease SET tenant_id = %s WHERE id = %s", (self.second.id, self.lease.id))
        self.lease.invalidate_recordset()
        fresh = self._make_login(self.lease, "xyz789")
        self.assertEqual(fresh.partner_id, self.second)
        self.assertFalse(user.active)

    def test_ending_the_lease_turns_the_login_off(self):
        user = self._make_login(self.lease)
        self.lease.action_end()
        self.assertFalse(user.active)
        self.assertEqual(self.unit.status, "vacant")

    def test_rent_product_can_be_sold(self):
        self.assertTrue(self.unit.unit_type_id.product_id.sale_ok)
