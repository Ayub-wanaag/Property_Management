from datetime import timedelta
from unittest.mock import MagicMock, patch

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.pm_billing.models.pm_mpesa import normalize_phone

MODULE = "odoo.addons.pm_billing.models.pm_mpesa.requests"


def _reply(data, status=200):
    reply = MagicMock()
    reply.status_code = status
    reply.json.return_value = data
    return reply


def _callback(checkout_id, code=0, receipt="SHL7K2M9QX", amount=1000):
    callback = {"MerchantRequestID": "M-1", "CheckoutRequestID": checkout_id, "ResultCode": code, "ResultDesc": "x"}
    if code == 0:
        callback["CallbackMetadata"] = {
            "Item": [
                {"Name": "Amount", "Value": amount},
                {"Name": "MpesaReceiptNumber", "Value": receipt},
                {"Name": "PhoneNumber", "Value": 254712345678},
            ]
        }
    return {"Body": {"stkCallback": callback}}


@tagged("post_install", "-at_install")
class TestMpesa(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)
        cls.env.user.groups_id |= cls.env.ref("pm_property.group_pm_manager")
        owner = cls.env["pm.owner"].create({"partner_id": cls.env["res.partner"].create({"name": "Owner"}).id})
        cls.building = cls.env["pm.building"].create({"name": "Stk Plaza", "owner_id": owner.id})
        cls.unit_type = cls.env["pm.unit.type"].create({"name": "Stk 1 bed", "default_rent": 1000, "building_id": cls.building.id})
        cls.tenant = cls.env["res.partner"].create({"name": "Stk Tenant", "mobile": "0712345678"})
        cls.unit = cls.env["pm.unit"].create(
            {"building_id": cls.building.id, "number": "S1", "floor": 1, "unit_type_id": cls.unit_type.id}
        )
        cls.lease = cls.env["pm.lease"].create(
            {"unit_id": cls.unit.id, "tenant_id": cls.tenant.id, "state": "active", "rent_amount": 1000}
        )
        cls.env.cr.execute("SELECT (NOW() AT TIME ZONE 'Africa/Nairobi')::date")
        cls.today = cls.env.cr.fetchone()[0]
        cls.Tx = cls.env["pm.mpesa.transaction"]

    def _turn_on(self):
        params = self.env["ir.config_parameter"].sudo()
        for key, value in (
            ("mpesa_enabled", "True"),
            ("mpesa_env", "sandbox"),
            ("mpesa_consumer_key", "key"),
            ("mpesa_consumer_secret", "secret"),
            ("mpesa_shortcode", "174379"),
            ("mpesa_passkey", "passkey"),
        ):
            params.set_param("pm_billing." + key, value)

    def _invoice(self, amount=1000):
        due = self.today - timedelta(days=1)
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.tenant.id,
                "invoice_date": due - timedelta(days=5),
                "invoice_date_due": due,
                "invoice_payment_term_id": False,
                "invoice_line_ids": [
                    (0, 0, {"product_id": self.unit_type.product_id.id, "name": "Rent", "quantity": 1,
                            "price_unit": amount, "tax_ids": [(6, 0, [])]})
                ],
            }
        )
        move.action_post()
        return move

    def _push(self, invoice, phone="0712345678"):
        with patch(MODULE + ".get", return_value=_reply({"access_token": "tok"})), patch(
            MODULE + ".post",
            return_value=_reply({"ResponseCode": "0", "MerchantRequestID": "M-1", "CheckoutRequestID": "ws_CO_1"}),
        ) as post:
            tx = self.Tx.start_for_invoice(invoice, phone)
        return tx, post

    # ----- phone numbers -----
    def test_phone_numbers(self):
        for raw in ("0712345678", "+254712345678", "254712345678", "712345678", "0712 345 678"):
            self.assertEqual(normalize_phone(raw), "254712345678", raw)
        self.assertEqual(normalize_phone("0112345678"), "254112345678")
        for bad in ("", "12345", "0612345678", "07123456789", None):
            self.assertFalse(normalize_phone(bad), bad)

    # ----- the on/off setting -----
    def test_off_by_default_and_nothing_is_sent(self):
        invoice = self._invoice()
        self.assertFalse(invoice.pm_mpesa_can_pay)
        with self.assertRaises(UserError):
            self.Tx.start_for_invoice(invoice, "0712345678")

    def test_on_shows_pay_now_only_while_unpaid(self):
        self._turn_on()
        invoice = self._invoice()
        self.assertTrue(invoice.pm_mpesa_can_pay)

    def test_cannot_turn_on_without_details(self):
        with self.assertRaises(ValidationError):
            self.env["res.config.settings"].create({"pm_mpesa_enabled": True})

    # ----- sending the prompt -----
    def test_push_is_sent_with_the_right_amount_and_phone(self):
        self._turn_on()
        tx, post = self._push(self._invoice(amount=1000))
        self.assertEqual((tx.state, tx.amount, tx.phone), ("pending", 1000, "254712345678"))
        payload = post.call_args.kwargs["json"]
        self.assertEqual((payload["Amount"], payload["PhoneNumber"], payload["BusinessShortCode"]), (1000, "254712345678", "174379"))
        self.assertIn("/pm_billing/stk/callback/", payload["CallBackURL"])

    def test_double_click_sends_one_prompt(self):
        self._turn_on()
        invoice = self._invoice()
        first, _post = self._push(invoice)
        second, post = self._push(invoice)
        self.assertEqual(first, second)
        post.assert_not_called()

    # ----- Safaricom answers -----
    def test_paid_callback_approves_by_itself(self):
        self._turn_on()
        invoice = self._invoice()
        tx, _post = self._push(invoice)
        self.assertTrue(self.Tx.handle_callback(_callback("ws_CO_1")))
        self.assertEqual((tx.state, tx.mpesa_receipt, tx.paid_amount), ("success", "SHL7K2M9QX", 1000))
        self.assertIn(invoice.payment_state, ("paid", "in_payment"))
        self.assertEqual((tx.claim_id.state, tx.claim_id.auto_approved), ("verified", True))
        self.assertTrue(tx.receipt_id)

    def test_same_answer_twice_pays_once(self):
        self._turn_on()
        invoice = self._invoice()
        tx, _post = self._push(invoice)
        self.Tx.handle_callback(_callback("ws_CO_1"))
        self.assertFalse(self.Tx.handle_callback(_callback("ws_CO_1")))
        self.assertEqual(self.env["pm.receipt"].search_count([("invoice_id", "=", invoice.id)]), 1)

    def test_unknown_request_is_ignored(self):
        self._turn_on()
        self.assertFalse(self.Tx.handle_callback(_callback("nobody")))
        self.assertFalse(self.Tx.handle_callback({}))

    def test_cancelled_by_tenant(self):
        self._turn_on()
        invoice = self._invoice()
        tx, _post = self._push(invoice)
        self.Tx.handle_callback(_callback("ws_CO_1", code=1032))
        self.assertEqual(tx.state, "failed")
        self.assertIn("cancelled", tx.friendly_result())
        self.assertEqual(invoice.payment_state, "not_paid")

    def test_money_for_an_invoice_paid_meanwhile_goes_to_staff(self):
        self._turn_on()
        invoice = self._invoice()
        tx, _post = self._push(invoice)
        self.env["pm.payment.wizard"].create(
            {"invoice_id": invoice.id, "amount": 1000, "method": "cash"}
        ).action_confirm()
        self.Tx.handle_callback(_callback("ws_CO_1"))
        self.assertEqual(tx.state, "review")
        self.assertEqual(tx.claim_id.state, "submitted")  # waits in Payments to check, as a manual proof does
        self.assertFalse(tx.claim_id.auto_approved)

    def test_callback_address_can_be_typed(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("pm_billing.mpesa_callback_url", "https://rent.example.com/pm_billing/stk/callback/abc123")
        api_model = self.env["pm.mpesa.api"]
        self.assertEqual(api_model._callback_url(), "https://rent.example.com/pm_billing/stk/callback/abc123")
        self.assertEqual(api_model._callback_secret(), "abc123")
        with self.assertRaises(ValidationError):
            self.env["res.config.settings"].create({"pm_mpesa_callback_url": "http://rent.example.com/pm_billing/stk/callback/abc"})

    def test_test_prompt_goes_to_the_typed_number(self):
        params = self.env["ir.config_parameter"].sudo()
        for key, value in (("consumer_key", "k"), ("consumer_secret", "s"), ("shortcode", "174379"), ("passkey", "p")):
            params.set_param("pm_billing.mpesa_" + key, value)
        wizard = self.env["pm.mpesa.test.wizard"].create({"phone": "0712345678"})
        with patch(MODULE + ".get", return_value=_reply({"access_token": "tok"})), patch(
            MODULE + ".post", return_value=_reply({"ResponseCode": "0", "CustomerMessage": "ok"})
        ) as post:
            result = wizard.action_send()
        payload = post.call_args.kwargs["json"]
        self.assertEqual((payload["Amount"], payload["PhoneNumber"]), (1, "254712345678"))
        self.assertEqual(result["tag"], "display_notification")
        self.assertEqual(result["params"]["next"], {"type": "ir.actions.act_window_close"})
        self.assertFalse(params.get_param("pm_billing.mpesa_test_phone"))  # the number is never saved
        wizard.phone = "123"
        with self.assertRaises(UserError):
            wizard.action_send()
