from datetime import timedelta

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.property_management.models.pm_payment_claim import parse_mpesa_text


@tagged("post_install", "-at_install")
class TestPayments(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)
        cls.env.user.groups_id |= cls.env.ref("property_management.group_pm_manager")
        owner = cls.env["pm.owner"].create({"partner_id": cls.env["res.partner"].create({"name": "Owner"}).id})
        cls.building = cls.env["pm.building"].create({"name": "Pay Plaza", "owner_id": owner.id})
        cls.unit_type = cls.env["pm.unit.type"].create({"name": "Pay 1 bed", "default_rent": 1000})
        cls.tenant = cls.env["res.partner"].create({"name": "Pay Tenant"})
        cls.unit = cls.env["pm.unit"].create(
            {"building_id": cls.building.id, "number": "P1", "floor": 1, "unit_type_id": cls.unit_type.id}
        )
        cls.lease = cls.env["pm.lease"].create(
            {"unit_id": cls.unit.id, "tenant_id": cls.tenant.id, "state": "active", "rent_amount": 1000}
        )
        cls.env.cr.execute("SELECT (NOW() AT TIME ZONE 'Africa/Nairobi')::date")
        cls.today = cls.env.cr.fetchone()[0]
        cls.other_product = cls.env["product.product"].create({"name": "Garbage collection", "type": "service"})

    def _invoice(self, days_past_due=3, amount=1000, product=None):
        due = self.today - timedelta(days=days_past_due)
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.tenant.id,
                "invoice_date": due - timedelta(days=5),
                "invoice_date_due": due,
                "invoice_payment_term_id": False,
                "invoice_line_ids": [
                    (0, 0, {"product_id": (product or self.unit_type.product_id).id, "name": "Charge", "quantity": 1,
                            "price_unit": amount, "tax_ids": [(6, 0, [])]})
                ],
            }
        )
        move.action_post()
        return move

    def _claim(self, **extra):
        vals = {"lease_id": self.lease.id, "kind": "rent", "amount": 1000}
        vals.update(extra)
        return self.env["pm.payment.claim"].create(vals)

    def _make_user(self, login, group):
        return self.env["res.users"].with_context(no_reset_password=True).create(
            {"login": login, "name": login, "password": "Passw0rd!x",
             "groups_id": [(6, 0, [self.env.ref("property_management." + group).id])]}
        )

    # ----- reading the M-Pesa message -----
    def test_parse_message(self):
        self.assertEqual(parse_mpesa_text("shl7k2m9qx Confirmed. Ksh12,500.00 sent to Sunrise"), ("SHL7K2M9QX", 12500.0))
        self.assertEqual(parse_mpesa_text("RCD1A2B3C4 Confirmed. You have received Ksh 1,500 from X")[1], 1500.0)
        self.assertEqual(parse_mpesa_text("Ksh 700. Thanks")[0], "")  # no code in the text
        self.assertEqual(parse_mpesa_text("")[1], 0.0)

    def test_claim_reads_code_and_amount_from_the_message(self):
        claim = self._claim(sms_text="rdk4m9q2xz Confirmed. Ksh1,000.00 sent to Pay Plaza", amount=1000)
        self.assertEqual((claim.transaction_code, claim.parsed_amount), ("RDK4M9Q2XZ", 1000.0))
        self.assertFalse(claim.amount_mismatch)

    def test_amount_difference_is_flagged(self):
        claim = self._claim(sms_text="AAB1C2D3E4 Confirmed. Ksh800.00 sent", amount=1000)
        self.assertTrue(claim.amount_mismatch)

    def test_a_code_can_only_be_used_once_whatever_the_capital_letters(self):
        self._claim(transaction_code="DUP1C2A3TE")
        with self.assertRaises(ValidationError):
            self._claim(transaction_code="dup1c2a3te")
        with self.assertRaises(ValidationError):
            self._claim(sms_text="DUP1C2A3TE Confirmed. Ksh100.00 sent")
        with self.assertRaises(ValidationError):  # the demo data already used this one
            self._claim(transaction_code="shl7k2m9qx")

    # ----- verifying a claim and recording payments -----
    def test_verifying_a_claim_pays_the_invoice_and_makes_a_receipt(self):
        invoice = self._invoice()
        claim = self._claim(transaction_code="VER1F2Y3ZZ")
        wizard = self.env["pm.payment.wizard"].browse(claim.action_verify()["res_id"])
        self.assertEqual(wizard.invoice_id, invoice)  # the oldest unpaid rent invoice
        self.assertEqual(wizard.amount, 1000)
        with self.assertRaises(UserError):
            wizard.action_confirm()  # must confirm the real statement was checked
        wizard.checked = True
        action = wizard.action_confirm()
        self.assertEqual(action["type"], "ir.actions.report")
        self.assertIn(invoice.payment_state, ("paid", "in_payment"))
        self.assertEqual(claim.state, "verified")
        self.assertEqual(claim.verified_by, self.env.user)
        self.assertTrue(claim.receipt_id.name.startswith("RCT/"))
        self.assertEqual(claim.receipt_id.amount, 1000)

    def test_claim_picks_the_invoice_of_the_right_kind(self):
        self._invoice(product=self.other_product)  # a service charge invoice, older
        rent = self._invoice(days_past_due=1)
        claim = self._claim(transaction_code="KIN1D2R3EN")
        self.assertEqual(self.env["pm.payment.wizard"].browse(claim.action_verify()["res_id"]).invoice_id, rent)

    def test_cash_payment_and_part_payment(self):
        invoice = self._invoice()
        wizard = self.env["pm.payment.wizard"].with_context(default_invoice_id=invoice.id).create({"amount": 400, "method": "cash"})
        self.assertEqual(wizard.journal_id.type, "cash")
        wizard.action_confirm()
        self.assertEqual(invoice.payment_state, "partial")
        self.assertAlmostEqual(invoice.amount_residual, 600)

    def test_overpayment_stays_as_a_credit(self):
        invoice = self._invoice()
        wizard = self.env["pm.payment.wizard"].with_context(default_invoice_id=invoice.id).create({"amount": 1500, "method": "bank"})
        wizard.action_confirm()
        self.assertIn(invoice.payment_state, ("paid", "in_payment"))
        payment = self.env["pm.receipt"].search([("invoice_id", "=", invoice.id)]).payment_id
        receivable = payment.move_id.line_ids.filtered(lambda line: line.account_id.account_type == "asset_receivable")
        self.assertAlmostEqual(receivable.amount_residual, -500)

    def test_cannot_pay_a_paid_invoice_twice(self):
        invoice = self._invoice()
        wizard = self.env["pm.payment.wizard"].with_context(default_invoice_id=invoice.id).create({"amount": 1000})
        wizard.action_confirm()
        again = self.env["pm.payment.wizard"].create({"invoice_id": invoice.id, "amount": 10})
        with self.assertRaises(UserError):
            again.action_confirm()

    def test_rejecting_needs_a_reason(self):
        claim = self._claim(transaction_code="REJ1E2C3TD")
        with self.assertRaises(UserError):
            claim.action_reject()
        claim.rejection_reason = "Not on the statement"
        claim.action_reject()
        self.assertEqual(claim.state, "rejected")

    def test_charge_type_follows_the_products(self):
        self.assertEqual(self._invoice().pm_kind, "rent")
        self.assertEqual(self._invoice(product=self.other_product).pm_kind, "service_charge")

    # ----- late-payment requests -----
    def _request(self, invoice, days_ahead=7, **extra):
        vals = {"invoice_id": invoice.id, "proposed_date": invoice.invoice_date_due + timedelta(days=days_ahead), "reason": "Salary is late"}
        vals.update(extra)
        return self.env["pm.late.request"].create(vals)

    def test_approving_moves_the_due_date_and_keeps_the_original(self):
        invoice = self._invoice(days_past_due=10)
        original = invoice.invoice_date_due
        self.assertTrue(self.env["pm.overdue.invoice"].search([("invoice_id", "=", invoice.id)]))
        request = self._request(invoice, days_ahead=20)  # ten days from today
        request.action_approve()
        self.assertEqual(request.state, "approved")
        self.assertEqual(invoice.invoice_date_due, original + timedelta(days=20))
        self.assertEqual(invoice.pm_original_due_date, original)
        self.assertTrue(invoice.pm_extended)
        self.assertFalse(self.env["pm.overdue.invoice"].search([("invoice_id", "=", invoice.id)]))  # leaves the chase list

    def test_declining_changes_nothing(self):
        invoice = self._invoice(days_past_due=10)
        due = invoice.invoice_date_due
        request = self._request(invoice)
        request.action_decline()
        self.assertEqual((request.state, invoice.invoice_date_due, invoice.pm_extended), ("declined", due, False))

    def test_request_rules(self):
        invoice = self._invoice(days_past_due=5)
        with self.assertRaises(ValidationError):
            self._request(invoice, days_ahead=-1)  # must be later than the current due date
        self._request(invoice)
        with self.assertRaises(ValidationError):
            self._request(invoice)  # one waiting request per invoice

    # ----- who can do what -----
    def test_caretaker_cannot_see_payments_or_requests(self):
        caretaker = self._make_user("care_p", "group_pm_caretaker")
        self._claim(transaction_code="CAR1E2T3AK")
        with self.assertRaises(AccessError):
            self.env["pm.payment.claim"].with_user(caretaker).search([])
        with self.assertRaises(AccessError):
            self.env["pm.late.request"].with_user(caretaker).search([])
        with self.assertRaises(AccessError):
            self.env["pm.receipt"].with_user(caretaker).search([])

    def test_accountant_records_payments_but_cannot_decide_requests(self):
        accountant = self._make_user("acct_p", "group_pm_accountant")
        invoice = self._invoice()
        wizard = self.env["pm.payment.wizard"].with_user(accountant).with_context(default_invoice_id=invoice.id).create({"amount": 1000})
        wizard.action_confirm()
        self.assertIn(invoice.payment_state, ("paid", "in_payment"))
        self.assertEqual(self.env["pm.receipt"].search([("invoice_id", "=", invoice.id)]).recorded_by, accountant)
        request = self._request(self._invoice(days_past_due=4))
        with self.assertRaises(AccessError):
            request.with_user(accountant).write({"reason": "changed"})

    def test_owner_sees_only_receipts_for_own_building(self):
        owner_partner = self.building.owner_id.partner_id
        owner_user = self.env["res.users"].with_context(no_reset_password=True).create(
            {"login": "own_p", "partner_id": owner_partner.id, "password": "Passw0rd!x",
             "groups_id": [(6, 0, [self.env.ref("property_management.group_pm_owner").id])]}
        )
        invoice = self._invoice()
        self.env["pm.payment.wizard"].with_context(default_invoice_id=invoice.id).create({"amount": 1000}).action_confirm()
        mine = self.env["pm.receipt"].with_user(owner_user).search([])
        self.assertEqual(mine.mapped("building_id"), self.building)
        with self.assertRaises(AccessError):
            self.env["pm.payment.claim"].with_user(owner_user).search([])
