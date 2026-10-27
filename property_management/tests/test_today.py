from datetime import timedelta

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestToday(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)
        cls.env.user.groups_id |= cls.env.ref("property_management.group_pm_manager")
        partner = cls.env["res.partner"].create({"name": "Today Owner"})
        cls.owner = cls.env["pm.owner"].create({"partner_id": partner.id})
        cls.building = cls.env["pm.building"].create({"name": "Today Plaza", "owner_id": cls.owner.id})
        cls.unit_type = cls.env["pm.unit.type"].create({"name": "Today type", "default_rent": 1000})
        cls.tenant = cls.env["res.partner"].create({"name": "Today Tenant"})
        cls.units = cls.env["pm.unit"].create(
            [{"building_id": cls.building.id, "number": "D%s" % i, "floor": 1, "unit_type_id": cls.unit_type.id} for i in range(1, 5)]
        )
        cls.units[0].status = "occupied"
        cls.units[1].status = "occupied"
        cls.lease = cls.env["pm.lease"].create(
            {"unit_id": cls.units[0].id, "tenant_id": cls.tenant.id, "state": "active", "rent_amount": 1000}
        )
        cls.env.cr.execute("SELECT (NOW() AT TIME ZONE 'Africa/Nairobi')::date")
        cls.today = cls.env.cr.fetchone()[0]

    def _invoice(self, days_past_due):
        due = self.today - timedelta(days=days_past_due)
        move = self.env["account.move"].create(
            {"move_type": "out_invoice", "partner_id": self.tenant.id, "invoice_date": due - timedelta(days=5),
             "invoice_date_due": due, "invoice_payment_term_id": False,
             "invoice_line_ids": [(0, 0, {"product_id": self.unit_type.product_id.id, "name": "Rent", "quantity": 1,
                                          "price_unit": 1000, "tax_ids": [(6, 0, [])]})]}
        )
        move.action_post()
        return move

    def _user(self, login, group, partner=None):
        vals = {"login": login, "password": "Passw0rd!x", "groups_id": [(6, 0, [self.env.ref("property_management." + group).id])]}
        vals.update({"partner_id": partner.id} if partner else {"name": login})
        return self.env["res.users"].with_context(no_reset_password=True).create(vals)

    def _today_for(self, user=None):
        model = self.env["pm.today"].with_user(user) if user else self.env["pm.today"]
        card = model.browse(model.action_open_today()["res_id"])
        card.read(list(self.env["pm.today"]._fields))  # the counts are worked out lazily: read them now
        return card

    def test_manager_cards_count_what_needs_doing(self):
        base = self._today_for()
        self._invoice(3)    # grace
        self._invoice(20)   # arrears
        claim = self.env["pm.payment.claim"].create({"lease_id": self.lease.id, "amount": 5})
        move = self._invoice(4)
        self.env["pm.late.request"].create({"invoice_id": move.id, "proposed_date": self.today + timedelta(days=5), "reason": "x"})
        now = self._today_for()
        self.assertEqual(now.payments_count - base.payments_count, 1)
        self.assertEqual(now.grace_count - base.grace_count, 2)
        self.assertEqual(now.arrears_count - base.arrears_count, 1)
        self.assertEqual(now.late_requests_count - base.late_requests_count, 1)
        self.assertGreaterEqual(now.vacant_count, 2)
        claim.sudo().unlink()

    def test_cards_clear_when_things_are_fixed(self):
        self.env["pm.payment.claim"].sudo().search([]).unlink()
        self.assertEqual(self._today_for().payments_count, 0)
        claim = self.env["pm.payment.claim"].create({"lease_id": self.lease.id, "amount": 5})
        self.assertEqual(self._today_for().payments_count, 1)
        claim.write({"state": "rejected", "rejection_reason": "x"})
        self.assertEqual(self._today_for().payments_count, 0)

    def test_caretaker_only_gets_the_chase_list_card(self):
        caretaker = self._user("care_today", "group_pm_caretaker")
        self._invoice(3)
        self._invoice(20)
        card = self._today_for(caretaker)
        self.assertGreaterEqual(card.grace_count, 1)
        self.assertEqual((card.arrears_count, card.payments_count, card.late_requests_count, card.vacant_count), (0, 0, 0, 0))

    def test_accountant_gets_payments_and_arrears_only(self):
        accountant = self._user("acct_today", "group_pm_accountant")
        self._invoice(20)
        card = self._today_for(accountant)
        self.assertGreaterEqual(card.arrears_count, 1)
        self.assertEqual((card.grace_count, card.late_requests_count), (0, 0))

    def test_owner_sees_occupancy_collection_and_arrears_of_their_own_buildings(self):
        owner_user = self._user("own_today", "group_pm_owner", partner=self.owner.partner_id)
        self._invoice(20)
        invoice = self._invoice(2)
        self.env["pm.payment.wizard"].with_context(default_invoice_id=invoice.id).create({"amount": 700}).action_confirm()
        card = self._today_for(owner_user)
        self.assertEqual((card.unit_count, card.occupied_count), (4, 2))
        self.assertAlmostEqual(card.collected_month, 700)
        self.assertEqual(card.owner_arrears_count, 1)
        self.assertEqual((card.payments_count, card.grace_count, card.vacant_count), (0, 0, 0))

    def test_owner_of_another_building_sees_none_of_it(self):
        other = self.env["res.partner"].create({"name": "Someone Else"})
        self.env["pm.owner"].create({"partner_id": other.id})
        stranger = self._user("stranger_today", "group_pm_owner", partner=other)
        self._invoice(20)
        card = self._today_for(stranger)
        self.assertEqual((card.unit_count, card.collected_month, card.owner_arrears_count), (0, 0, 0))

    def test_click_throughs_open_the_right_screens(self):
        card = self._today_for()
        self.assertEqual(card.action_open_payments()["res_model"], "pm.payment.claim")
        self.assertEqual(card.action_open_late_requests()["res_model"], "pm.late.request")
        self.assertEqual(card.action_open_grace()["domain"], "[('stage', '=', 'grace')]")
        self.assertEqual(card.action_open_arrears()["domain"], "[('stage', '=', 'arrears')]")
        self.assertEqual(card.action_open_vacant()["domain"], "[('status', '=', 'vacant')]")
