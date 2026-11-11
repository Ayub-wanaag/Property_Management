from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .pm_mpesa import normalize_phone


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pm_grace_days = fields.Integer(
        string="Grace days",
        config_parameter="pm_billing.grace_days",
        default=5,
        help="Days after the due date before an invoice moves from the chase list to arrears. Example: 5. Default 5.",
    )
    pm_invoice_lead_days = fields.Integer(
        string="Invoice days before rent is due",
        config_parameter="pm_billing.invoice_lead_days",
        default=5,
        help="How many days before the due date the monthly rent invoice is created. Example: 5. Default 5.",
    )
    pm_first_month = fields.Selection(
        [("days", "By days stayed"), ("full", "Full month")],
        string="First month rent",
        config_parameter="pm_billing.first_month",
        default="days",
        help="How the first month is charged when a tenant moves in part-way through it. Default: By days stayed.",
    )
    pm_leak_threshold_pct = fields.Integer(
        string="Water leak warning (%)",
        config_parameter="pm_billing.leak_threshold_pct",
        default=10,
        help="Warn when the building's water loss is above this percentage. Example: 10. Default 10.",
    )

    # ----- M-Pesa STK Push -----
    pm_mpesa_enabled = fields.Boolean(
        string="Pay with M-Pesa STK Push",
        config_parameter="pm_billing.mpesa_enabled",
        help="On: tenants press Pay now on their invoice and get a prompt on their phone, and the payment is approved automatically. Off: tenants see the manual payment details and send proof, which staff approve.",
    )
    pm_mpesa_env = fields.Selection(
        [("sandbox", "Sandbox (for testing)"), ("production", "Production (real money)")],
        string="Environment",
        config_parameter="pm_billing.mpesa_env",
        default="sandbox",
        help="Use Sandbox while testing. Switch to Production only with live Daraja details.",
    )
    pm_mpesa_consumer_key = fields.Char(
        string="Consumer key", config_parameter="pm_billing.mpesa_consumer_key", help="From your app on the Daraja portal."
    )
    pm_mpesa_consumer_secret = fields.Char(
        string="Consumer secret", config_parameter="pm_billing.mpesa_consumer_secret", help="From your app on the Daraja portal."
    )
    pm_mpesa_shortcode = fields.Char(
        string="Shortcode",
        config_parameter="pm_billing.mpesa_shortcode",
        help="Your Paybill number, or the store number for a Till. For the sandbox use 174379.",
    )
    pm_mpesa_passkey = fields.Char(
        string="Passkey", config_parameter="pm_billing.mpesa_passkey", help="The Lipa Na M-Pesa Online passkey from Daraja."
    )
    pm_mpesa_txn_type = fields.Selection(
        [("CustomerPayBillOnline", "Paybill"), ("CustomerBuyGoodsOnline", "Till (Buy Goods)")],
        string="Money goes to a",
        config_parameter="pm_billing.mpesa_txn_type",
        default="CustomerPayBillOnline",
    )
    pm_mpesa_till = fields.Char(
        string="Till number",
        config_parameter="pm_billing.mpesa_till",
        help="Only for a Till: the till number that receives the money. Leave empty for a Paybill.",
    )
    pm_mpesa_callback_url = fields.Char(
        string="Callback address",
        config_parameter="pm_billing.mpesa_callback_url",
        help="Where Safaricom sends the payment result. You can edit it. It must start with https and keep /pm_billing/stk/callback/ followed by a secret.",
    )

    pm_mpesa_test_phone = fields.Char(
        string="Phone number to test",
        help="A Safaricom number, for example 0712345678. Press Send test prompt to ask it for KES 1. Nothing is saved.",
    )

    @api.model
    def get_values(self):
        values = super().get_values()
        if not values.get("pm_mpesa_callback_url"):
            values["pm_mpesa_callback_url"] = self.env["pm.mpesa.api"]._default_callback_url()
        return values

    @api.constrains("pm_mpesa_callback_url")
    def _check_pm_mpesa_callback_url(self):
        for settings in self.filtered("pm_mpesa_callback_url"):
            url = settings.pm_mpesa_callback_url.strip()
            if not url.lower().startswith("https://") or "/pm_billing/stk/callback/" not in url or url.endswith("/callback/"):
                raise ValidationError(
                    _("The callback address must start with https:// and end with /pm_billing/stk/callback/ and a secret code.")
                )

    def _mpesa_creds(self):
        self.ensure_one()
        return {
            "env": self.pm_mpesa_env or "sandbox",
            "key": (self.pm_mpesa_consumer_key or "").strip(),
            "secret": (self.pm_mpesa_consumer_secret or "").strip(),
            "shortcode": (self.pm_mpesa_shortcode or "").strip(),
            "passkey": (self.pm_mpesa_passkey or "").strip(),
            "transaction_type": self.pm_mpesa_txn_type or "CustomerPayBillOnline",
            "till": (self.pm_mpesa_till or "").strip(),
        }

    def action_pm_mpesa_test(self):
        """Tests the details typed on this page with Safaricom, before they are even saved."""
        self.ensure_one()
        api_model = self.env["pm.mpesa.api"]
        api_model.test_connection(self._mpesa_creds())
        message = _("Connected to Safaricom (%s). The consumer key and secret work.") % self.pm_mpesa_env
        base = (self.pm_mpesa_callback_url or "").lower()
        if not base.startswith("https://") or "localhost" in base or "127.0.0.1" in base:
            message += " " + _(
                "Warning: the callback address is not a public https address, so Safaricom cannot send payment results back. Use a public address (for local testing, a tunnel such as ngrok)."
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("M-Pesa test"), "message": message, "type": "success", "sticky": False},
        }

    def action_pm_mpesa_test_push(self):
        """Sends a real KES 1 prompt to the number typed on this page, using the details typed on this page."""
        self.ensure_one()
        phone = normalize_phone(self.pm_mpesa_test_phone)
        if not phone:
            raise UserError(_("Type a Safaricom number like 0712345678 in the test phone box first."))
        api_model = self.env["pm.mpesa.api"]
        creds = self._mpesa_creds()
        api_model._check_credentials(creds)
        data = api_model.stk_push(creds, phone, 1, "TEST", _("Test"), callback_url=self.pm_mpesa_callback_url or None)
        message = _("Safaricom accepted the request for KES 1 to %(phone)s. %(note)s") % {
            "phone": phone,
            "note": data.get("CustomerMessage") or "",
        }
        if creds["env"] == "sandbox":
            message += " " + _("In the sandbox the prompt shows in the Daraja simulator, not on a real phone.")
        else:
            message += " " + _("Check the phone and enter the PIN. This is real money (KES 1).")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("M-Pesa test prompt"), "message": message, "type": "success", "sticky": True},
        }

    @api.constrains("pm_mpesa_enabled", "pm_mpesa_consumer_key", "pm_mpesa_consumer_secret", "pm_mpesa_shortcode", "pm_mpesa_passkey")
    def _check_pm_mpesa_details(self):
        for settings in self.filtered("pm_mpesa_enabled"):
            if not all((settings.pm_mpesa_consumer_key, settings.pm_mpesa_consumer_secret, settings.pm_mpesa_shortcode, settings.pm_mpesa_passkey)):
                raise ValidationError(
                    _("Fill in the consumer key, consumer secret, shortcode and passkey before turning on M-Pesa STK Push. Then press Test connection.")
                )

    @api.constrains("pm_grace_days", "pm_invoice_lead_days", "pm_leak_threshold_pct")
    def _check_pm_billing_settings(self):
        for settings in self:
            if min(settings.pm_grace_days, settings.pm_invoice_lead_days, settings.pm_leak_threshold_pct) < 0:
                raise ValidationError(_("Days and percentages cannot be below zero. Please check the Billing settings."))
