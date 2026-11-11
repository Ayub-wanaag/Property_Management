from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


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

    @api.constrains("pm_grace_days", "pm_invoice_lead_days", "pm_leak_threshold_pct")
    def _check_pm_billing_settings(self):
        for settings in self:
            if min(settings.pm_grace_days, settings.pm_invoice_lead_days, settings.pm_leak_threshold_pct) < 0:
                raise ValidationError(_("Days and percentages cannot be below zero. Please check the Billing settings."))
