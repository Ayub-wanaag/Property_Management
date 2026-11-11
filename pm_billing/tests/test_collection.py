from datetime import timedelta

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestCollection(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)
        cls.env.user.groups_id |= cls.env.ref("pm_property.group_pm_manager")
        owner_partner = cls.env["res.partner"].create({"name": "Test Owner"})
        cls.owner = cls.env["pm.owner"].create({"partner_id": owner_partner.id})
        cls.building = cls.env["pm.building"].create({"name": "Test Plaza", "owner_id": cls.owner.id})
        cls.unit_type = cls.env["pm.unit.type"].create({"name": "Test 1 bedroom", "default_rent": 1000})
        cls.tenant = cls.env["res.partner"].create({"name": "Test Tenant"})
        cls.unit = cls.env["pm.unit"].create(
            {"building_id": cls.building.id, "number": "T1", "floor": 1, "unit_type_id": cls.unit_type.id}
        )
        cls.lease = cls.env["pm.lease"].create(
            {"unit_id": cls.unit.id, "tenant_id": cls.tenant.id, "state": "active", "rent_amount": 1000}
        )
        cls.env.cr.execute("SELECT (NOW() AT TIME ZONE 'Africa/Nairobi')::date")
        cls.today = cls.env.cr.fetchone()[0]

    def _invoice(self, days_past_due, partner=None, post=True):
        due = self.today - timedelta(days=days_past_due)
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": (partner or self.tenant).id,
                "invoice_date": due - timedelta(days=5),
                "invoice_date_due": due,
                "invoice_payment_term_id": False,
                "invoice_line_ids": [
                    (0, 0, {"product_id": self.unit_type.product_id.id, "name": "Rent", "quantity": 1,
                            "price_unit": 1000, "tax_ids": [(6, 0, [])]})
                ],
            }
        )
        if post:
            move.action_post()
        return move

    def _overdue(self, move):
        return self.env["pm.overdue.invoice"].search([("invoice_id", "=", move.id)])

    def _make_user(self, login, group):
        return self.env["res.users"].with_context(no_reset_password=True).create(
            {"login": login, "name": login, "password": "Passw0rd!x",
             "groups_id": [(6, 0, [self.env.ref("pm_property." + group).id])]}
        )

    # ----- the Lease field on the invoice -----
    def test_invoice_picks_up_the_tenants_lease(self):
        self.assertEqual(self._invoice(0).pm_lease_id, self.lease)

    def test_invoice_for_a_tenant_who_moved_out_still_links_to_the_old_lease(self):
        gone = self.env["res.partner"].create({"name": "Gone Tenant"})
        other_unit = self.env["pm.unit"].create(
            {"building_id": self.building.id, "number": "T2", "floor": 1, "unit_type_id": self.unit_type.id}
        )
        old = self.env["pm.lease"].create({"unit_id": other_unit.id, "tenant_id": gone.id, "state": "ended"})
        self.assertEqual(self._invoice(10, partner=gone).pm_lease_id, old)

    def test_invoice_for_a_stranger_has_no_lease_and_stays_off_the_lists(self):
        stranger = self.env["res.partner"].create({"name": "Not A Tenant"})
        move = self._invoice(10, partner=stranger)
        self.assertFalse(move.pm_lease_id)
        self.assertFalse(self._overdue(move))

    def test_lease_can_be_set_by_hand(self):
        move = self._invoice(10, post=False)
        other = self.env["res.partner"].create({"name": "Someone Else"})
        unit = self.env["pm.unit"].create(
            {"building_id": self.building.id, "number": "T3", "floor": 1, "unit_type_id": self.unit_type.id}
        )
        lease2 = self.env["pm.lease"].create({"unit_id": unit.id, "tenant_id": other.id, "state": "active"})
        move.pm_lease_id = lease2
        self.assertEqual(move.pm_lease_id, lease2)

    # ----- grace period vs arrears -----
    def test_stages_and_age_buckets(self):
        cases = {3: ("grace", "grace"), 5: ("grace", "grace"), 6: ("arrears", "1_30"), 30: ("arrears", "1_30"),
                 31: ("arrears", "31_60"), 60: ("arrears", "31_60"), 61: ("arrears", "61_plus"), 200: ("arrears", "61_plus")}
        for days, (stage, bucket) in cases.items():
            row = self._overdue(self._invoice(days))
            self.assertEqual((row.stage, row.age_bucket, row.days_past_due), (stage, bucket, days), "days %s" % days)

    def test_not_yet_overdue_is_not_listed(self):
        self.assertFalse(self._overdue(self._invoice(0)))   # due today
        self.assertFalse(self._overdue(self._invoice(-5)))  # due in five days

    def test_draft_and_paid_invoices_are_not_listed(self):
        self.assertFalse(self._overdue(self._invoice(10, post=False)))
        paid = self._invoice(10)
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=paid.ids).create({})._create_payments()
        self.assertFalse(self._overdue(paid))

    def test_part_paid_invoice_shows_what_is_still_owed(self):
        move = self._invoice(10)
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=move.ids).create({"amount": 400})._create_payments()
        self.assertAlmostEqual(self._overdue(move).amount_due, 600)

    def test_grace_days_follow_the_building_setting(self):
        move = self._invoice(8)
        self.assertEqual(self._overdue(move).stage, "arrears")
        self.building.grace_days = 10
        self.assertEqual(self._overdue(move).stage, "grace")

    # ----- contact log -----
    def test_logging_a_contact_shows_on_the_list(self):
        move = self._invoice(3)
        promised = self.today + timedelta(days=2)
        log = self.env["pm.chase.log"].with_context(default_invoice_id=move.id).create(
            {"channel": "whatsapp", "note": "Will pay soon", "promised_date": promised}
        )
        self.assertEqual(log.invoice_number, move.name)
        self.assertEqual(log.lease_id, self.lease)
        row = self._overdue(move)
        self.assertEqual((row.contact_count, row.promised_date), (1, promised))

    def test_the_log_contact_button_opens_the_right_form(self):
        move = self._invoice(3)
        action = self._overdue(move).action_log_contact()
        self.assertEqual(action["context"]["default_invoice_id"], move.id)

    # ----- who can see what -----
    def test_caretaker_sees_the_lists_and_logs_contacts_but_cannot_open_invoices(self):
        caretaker = self._make_user("care_t", "group_pm_caretaker")
        move = self._invoice(3)
        rows = self.env["pm.overdue.invoice"].with_user(caretaker).search([("invoice_id", "=", move.id)])
        self.assertEqual(len(rows), 1)
        arrears = self._invoice(20)
        self.assertFalse(self.env["pm.overdue.invoice"].with_user(caretaker).search([("invoice_id", "=", arrears.id)]))
        manager = self._make_user("mgr_t", "group_pm_manager")
        both = self.env["pm.overdue.invoice"].with_user(manager).search([("invoice_id", "in", (move + arrears).ids)])
        self.assertEqual(len(both), 2)  # managers see the grace period and the arrears
        log = self.env["pm.chase.log"].with_user(caretaker).with_context(default_invoice_id=move.id).create(
            {"channel": "call", "note": "Phoned, no answer"}
        )
        self.assertEqual(log.invoice_number, move.name)
        with self.assertRaises(AccessError):
            self.env["account.move"].with_user(caretaker).browse(move.id).read(["name"])
        with self.assertRaises(AccessError):
            log.unlink()

    def test_owner_cannot_see_overdue_invoices_or_the_log(self):
        owner_user = self._make_user("own_t", "group_pm_owner")
        self._invoice(3)
        with self.assertRaises(AccessError):
            self.env["pm.overdue.invoice"].with_user(owner_user).search([])
        with self.assertRaises(AccessError):
            self.env["pm.chase.log"].with_user(owner_user).search([])

    def test_accountant_can_read_arrears_but_not_write_logs(self):
        accountant = self._make_user("acct_t", "group_pm_accountant")
        self._invoice(10)
        self.assertTrue(self.env["pm.overdue.invoice"].with_user(accountant).search([("stage", "=", "arrears")]))
        with self.assertRaises(AccessError):
            self.env["pm.chase.log"].with_user(accountant).with_context(
                default_invoice_id=self._invoice(10).id).create({"channel": "call"})
