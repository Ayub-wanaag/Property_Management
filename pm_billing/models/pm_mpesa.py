import base64
import logging
import math
import re
import secrets
from datetime import datetime, timedelta

import requests

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

SAFARICOM_URLS = {
    "sandbox": "https://sandbox.safaricom.co.ke",
    "production": "https://api.safaricom.co.ke",
}
PHONE_RE = re.compile(r"^254[17]\d{8}$")
# What Safaricom's result codes mean, in words a tenant understands.
FRIENDLY_RESULTS = {
    "1": "The M-Pesa balance was not enough.",
    "1001": "The phone is busy with another M-Pesa request. Please wait a minute and try again.",
    "1019": "The request expired. Please try again.",
    "1025": "The request could not be sent to that phone. Please try again.",
    "1032": "You cancelled the request on your phone.",
    "1037": "We did not get a reply from the phone. Check that it is on and has network, then try again.",
    "2001": "The M-Pesa PIN was wrong.",
}


def normalize_phone(raw):
    """07xx, 01xx, +2547xx and 2547xx all become 2547xxxxxxxx. Returns False when it is not a Kenyan mobile number."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10 and digits.startswith("0"):
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "17":
        digits = "254" + digits
    return digits if PHONE_RE.match(digits) else False


def _is_true(value):
    return bool(value) and str(value).strip().lower() not in ("false", "0", "")


class PmMpesaApi(models.AbstractModel):
    """Talks to Safaricom's Daraja service. All the API details come from Billing > Settings."""

    _name = "pm.mpesa.api"
    _description = "M-Pesa Daraja connection"

    # ----- settings -----
    @api.model
    def _is_enabled(self):
        return _is_true(self.env["ir.config_parameter"].sudo().get_param("pm_billing.mpesa_enabled"))

    @api.model
    def _credentials(self):
        get = self.env["ir.config_parameter"].sudo().get_param
        return {
            "env": get("pm_billing.mpesa_env") or "sandbox",
            "key": (get("pm_billing.mpesa_consumer_key") or "").strip(),
            "secret": (get("pm_billing.mpesa_consumer_secret") or "").strip(),
            "shortcode": (get("pm_billing.mpesa_shortcode") or "").strip(),
            "passkey": (get("pm_billing.mpesa_passkey") or "").strip(),
            "transaction_type": get("pm_billing.mpesa_txn_type") or "CustomerPayBillOnline",
            "till": (get("pm_billing.mpesa_till") or "").strip(),
        }

    @api.model
    def _check_credentials(self, creds):
        missing = [
            label
            for label, value in (
                (_("Consumer key"), creds["key"]),
                (_("Consumer secret"), creds["secret"]),
                (_("Shortcode"), creds["shortcode"]),
                (_("Passkey"), creds["passkey"]),
            )
            if not value
        ]
        if missing:
            raise UserError(_("These M-Pesa details are still empty: %s.") % ", ".join(missing))

    @api.model
    def _custom_callback(self):
        """The full callback address typed in Billing > Settings, if any."""
        return (self.env["ir.config_parameter"].sudo().get_param("pm_billing.mpesa_callback_url") or "").strip()

    @api.model
    def _callback_secret(self):
        """The secret at the end of the callback address, so only Safaricom (who we told) can reach it."""
        custom = self._custom_callback()
        if custom:
            return custom.rstrip("/").rsplit("/", 1)[-1]
        params = self.env["ir.config_parameter"].sudo()
        secret = params.get_param("pm_billing.mpesa_callback_secret")
        if not secret:
            secret = secrets.token_urlsafe(24)
            params.set_param("pm_billing.mpesa_callback_secret", secret)
        return secret

    @api.model
    def _default_callback_url(self):
        params = self.env["ir.config_parameter"].sudo()
        base = (params.get_param("web.base.url") or "").strip().replace("http://", "https://", 1)
        return "%s/pm_billing/stk/callback/%s" % (base.rstrip("/"), self._callback_secret())

    @api.model
    def _callback_url(self):
        return self._custom_callback() or self._default_callback_url()

    # ----- talking to Safaricom -----
    @api.model
    def _token(self, creds):
        self._check_credentials(creds)
        try:
            response = requests.get(
                SAFARICOM_URLS[creds["env"]] + "/oauth/v1/generate",
                params={"grant_type": "client_credentials"},
                auth=(creds["key"], creds["secret"]),
                timeout=30,
            )
        except requests.RequestException as error:
            raise UserError(_("Could not reach Safaricom. Check the internet connection. (%s)") % error)
        if response.status_code != 200:
            raise UserError(
                _("Safaricom refused the consumer key and secret (error %s). Check them and the Sandbox/Production choice.")
                % response.status_code
            )
        token = response.json().get("access_token")
        if not token:
            raise UserError(_("Safaricom did not send an access token. Check the consumer key and secret."))
        return token

    @api.model
    def _post(self, creds, path, payload):
        token = self._token(creds)
        try:
            response = requests.post(
                SAFARICOM_URLS[creds["env"]] + path,
                json=payload,
                headers={"Authorization": "Bearer " + token},
                timeout=30,
            )
            data = response.json()
        except requests.RequestException as error:
            raise UserError(_("Could not reach Safaricom. Check the internet connection. (%s)") % error)
        except ValueError:
            raise UserError(_("Safaricom sent a reply we could not read (error %s).") % response.status_code)
        return data

    @api.model
    def _password(self, creds):
        timestamp = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y%m%d%H%M%S")  # Nairobi time
        raw = (creds["shortcode"] + creds["passkey"] + timestamp).encode()
        return base64.b64encode(raw).decode(), timestamp

    @api.model
    def stk_push(self, creds, phone, amount, reference, description, callback_url=None):
        password, timestamp = self._password(creds)
        payload = {
            "BusinessShortCode": creds["shortcode"],
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": creds["transaction_type"],
            "Amount": int(amount),
            "PartyA": phone,
            "PartyB": creds["till"] or creds["shortcode"],
            "PhoneNumber": phone,
            "CallBackURL": callback_url or self._callback_url(),
            "AccountReference": (reference or "Rent")[:12],
            "TransactionDesc": (description or "Rent")[:13],
        }
        data = self._post(creds, "/mpesa/stkpush/v1/processrequest", payload)
        if str(data.get("ResponseCode")) != "0":
            message = data.get("errorMessage") or data.get("ResponseDescription") or str(data)
            _logger.warning("STK push refused: %s", data)
            raise UserError(_("Safaricom did not accept the payment request: %s") % message)
        return data

    @api.model
    def stk_query(self, creds, checkout_request_id):
        password, timestamp = self._password(creds)
        payload = {
            "BusinessShortCode": creds["shortcode"],
            "Password": password,
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        }
        return self._post(creds, "/mpesa/stkpushquery/v1/query", payload)


class PmMpesaTransaction(models.Model):
    """One STK Push request, from the moment the tenant presses Pay now until Safaricom answers."""

    _name = "pm.mpesa.transaction"
    _description = "M-Pesa STK Push Payment"
    _order = "id desc"

    name = fields.Char(compute="_compute_name", store=True)
    invoice_id = fields.Many2one("account.move", string="Invoice", required=True, ondelete="restrict", readonly=True)
    lease_id = fields.Many2one("pm.lease", string="Lease", required=True, ondelete="restrict", readonly=True)
    tenant_id = fields.Many2one(related="lease_id.tenant_id", store=True, string="Tenant")
    building_id = fields.Many2one(related="lease_id.building_id", store=True, string="Building")
    phone = fields.Char(string="Phone asked to pay", readonly=True)
    amount = fields.Float(string="Amount asked", readonly=True)
    paid_amount = fields.Float(string="Amount paid", readonly=True)
    state = fields.Selection(
        [
            ("pending", "Waiting for the tenant"),
            ("success", "Paid"),
            ("failed", "Failed or cancelled"),
            ("review", "Paid, needs checking"),
        ],
        string="Status",
        default="pending",
        required=True,
        readonly=True,
        help="Paid means the payment was recorded and the receipt made automatically. 'Paid, needs checking' means the money came in but could not be matched to the invoice by itself, so it waits in Payments to check.",
    )
    merchant_request_id = fields.Char(readonly=True, copy=False)
    checkout_request_id = fields.Char(string="Safaricom request ID", readonly=True, copy=False, index=True)
    mpesa_receipt = fields.Char(string="M-Pesa code", readonly=True, copy=False)
    result_code = fields.Char(string="Safaricom result code", readonly=True)
    result_desc = fields.Char(string="Safaricom message", readonly=True)
    note = fields.Text(string="Note", readonly=True)
    claim_id = fields.Many2one("pm.payment.claim", string="Payment proof", readonly=True)
    receipt_id = fields.Many2one("pm.receipt", string="Receipt", readonly=True)

    @api.depends("invoice_id.name", "mpesa_receipt")
    def _compute_name(self):
        for tx in self:
            tx.name = "%s %s" % (tx.invoice_id.name or "", tx.mpesa_receipt or "")

    # ----- starting a payment -----
    @api.model
    def start_for_invoice(self, invoice, phone):
        """Sends the STK Push to the tenant's phone. Returns the transaction."""
        api_model = self.env["pm.mpesa.api"]
        if not api_model._is_enabled():
            raise UserError(_("M-Pesa payment is switched off. Please pay another way."))
        invoice = invoice.sudo()
        if invoice.state != "posted" or invoice.payment_state not in ("not_paid", "partial"):
            raise UserError(_("This invoice is already paid."))
        phone = normalize_phone(phone)
        if not phone:
            raise UserError(_("Please type a Safaricom number like 0712345678."))
        amount = int(math.ceil(invoice.amount_residual - 0.0001))
        if amount < 1:
            raise UserError(_("There is nothing left to pay on this invoice."))
        recent = self.sudo().search(
            [
                ("invoice_id", "=", invoice.id),
                ("state", "=", "pending"),
                ("phone", "=", phone),
                ("create_date", ">=", fields.Datetime.now() - timedelta(minutes=2)),
            ],
            limit=1,
        )
        if recent:  # a double click must not send two prompts
            return recent
        creds = api_model._credentials()
        data = api_model.stk_push(
            creds,
            phone,
            amount,
            (invoice.name or "Rent").replace("/", "")[-12:],
            _("Rent") if invoice.pm_kind == "rent" else _("Payment"),
        )
        return self.sudo().create(
            {
                "invoice_id": invoice.id,
                "lease_id": invoice.pm_lease_id.id,
                "phone": phone,
                "amount": amount,
                "merchant_request_id": data.get("MerchantRequestID"),
                "checkout_request_id": data.get("CheckoutRequestID"),
            }
        )

    # ----- Safaricom's answer -----
    @api.model
    def handle_callback(self, payload):
        """Safaricom tells us how the payment went. Returns True when it was a request we know."""
        callback = ((payload or {}).get("Body") or {}).get("stkCallback") or {}
        checkout_id = callback.get("CheckoutRequestID")
        if not checkout_id:
            return False
        tx = self.sudo().search([("checkout_request_id", "=", checkout_id)], limit=1)
        if not tx or tx.state != "pending":  # unknown, or already answered once
            return False
        result_code = str(callback.get("ResultCode"))
        if result_code != "0":
            tx.write({"state": "failed", "result_code": result_code, "result_desc": callback.get("ResultDesc")})
            return True
        items = {item.get("Name"): item.get("Value") for item in (callback.get("CallbackMetadata") or {}).get("Item", [])}
        tx.write({"result_code": result_code, "result_desc": callback.get("ResultDesc")})
        tx._settle(str(items.get("MpesaReceiptNumber") or "").strip().upper(), float(items.get("Amount") or tx.amount))
        return True

    def _settle(self, code, paid):
        """The money is in. Record the payment and receipt by ourselves. If that is not possible, send it to staff to check."""
        self.ensure_one()
        root = self.with_user(SUPERUSER_ID)
        invoice = root.invoice_id
        kind = "rent" if invoice.pm_kind == "rent" else "service_charge"
        try:
            with self.env.cr.savepoint():
                claim = root.env["pm.payment.claim"].create(
                    {
                        "lease_id": root.lease_id.id,
                        "kind": kind,
                        "amount": paid,
                        "method": "mpesa",
                        "transaction_code": code or False,
                        "auto_approved": True,
                    }
                )
                wizard = root.env["pm.payment.wizard"].create(
                    {
                        "claim_id": claim.id,
                        "invoice_id": invoice.id,
                        "amount": min(paid, invoice.amount_residual),
                        "method": "mpesa",
                        "transaction_code": code,
                        "checked": True,
                    }
                )
                wizard.action_confirm()
        except Exception as error:  # whatever went wrong, the money is in: staff must see it
            reason = error.args[0] if error.args and isinstance(error.args[0], str) else str(error)
            _logger.exception("STK payment %s could not be recorded by itself", code)
            self._send_to_staff(code, paid, reason)
            return False
        self.write(
            {
                "state": "success",
                "mpesa_receipt": code,
                "paid_amount": paid,
                "claim_id": claim.id,
                "receipt_id": claim.receipt_id.id,
            }
        )
        return True

    def _send_to_staff(self, code, paid, reason):
        """Keep the manual route: a normal payment proof that waits in Payments to check."""
        self.ensure_one()
        root = self.with_user(SUPERUSER_ID)
        claim = root.env["pm.payment.claim"]
        try:
            with self.env.cr.savepoint():
                claim = claim.create(
                    {
                        "lease_id": root.lease_id.id,
                        "kind": "rent" if root.invoice_id.pm_kind == "rent" else "service_charge",
                        "amount": paid,
                        "method": "mpesa",
                        "transaction_code": code or False,
                        "sms_text": _("M-Pesa STK Push, code %(code)s, KES %(amount)s. Not recorded automatically: %(reason)s")
                        % {"code": code or "-", "amount": "{:,.2f}".format(paid), "reason": reason},
                    }
                )
        except Exception:
            _logger.exception("Could not make a payment proof for STK payment %s", code)
            claim = root.env["pm.payment.claim"]
        self.write(
            {
                "state": "review",
                "mpesa_receipt": code,
                "paid_amount": paid,
                "claim_id": claim.id,
                "note": _("Money received, but not recorded automatically: %s") % reason,
            }
        )

    # ----- staff -----
    def action_check_status(self):
        """Asks Safaricom what happened, for a request whose answer never reached us."""
        api_model = self.env["pm.mpesa.api"]
        creds = api_model._credentials()
        for tx in self.filtered(lambda t: t.state == "pending"):
            data = api_model.stk_query(creds, tx.checkout_request_id)
            code = data.get("ResultCode")
            if code is None:
                raise UserError(
                    _("Safaricom says: %s") % (data.get("errorMessage") or data.get("ResponseDescription") or _("still working on it"))
                )
            if str(code) == "0":
                tx.write({"result_code": "0", "result_desc": data.get("ResultDesc")})
                tx._send_to_staff("", tx.amount, _("Confirmed by asking Safaricom. The M-Pesa code is not known, find it on the statement."))
            else:
                tx.write({"state": "failed", "result_code": str(code), "result_desc": data.get("ResultDesc")})
        return True

    def friendly_result(self):
        self.ensure_one()
        return FRIENDLY_RESULTS.get(self.result_code or "", _("The payment did not go through."))
