from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


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
    # ----- M-Pesa STK Push -----
    pm_mpesa_enabled = fields.Boolean(
        string="M-Pesa prompt (STK Push)",
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

    @api.constrains("pm_mpesa_enabled", "pm_mpesa_consumer_key", "pm_mpesa_consumer_secret", "pm_mpesa_shortcode", "pm_mpesa_passkey")
    def _check_pm_mpesa_details(self):
        for settings in self.filtered("pm_mpesa_enabled"):
            if not all((settings.pm_mpesa_consumer_key, settings.pm_mpesa_consumer_secret, settings.pm_mpesa_shortcode, settings.pm_mpesa_passkey)):
                raise ValidationError(
                    _("Fill in the consumer key, consumer secret, shortcode and passkey before turning on M-Pesa STK Push.")
                )

    @api.constrains("pm_grace_days", "pm_invoice_lead_days")
    def _check_pm_billing_settings(self):
        for settings in self:
            if min(settings.pm_grace_days, settings.pm_invoice_lead_days) < 0:
                raise ValidationError(_("Days and percentages cannot be below zero. Please check the Billing settings."))
