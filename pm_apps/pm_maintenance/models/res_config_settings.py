from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pm_approval_threshold = fields.Float(
        string="Ask approval above (KES)",
        config_parameter="pm_maintenance.approval_threshold",
        default=0.0,
        help="Repairs above this amount wait for approval. Example: 20,000. Leave at 0 for no approval needed.",
    )
    pm_repair_charge_days = fields.Integer(
        string="Days to pay a repair charge",
        config_parameter="pm_maintenance.repair_charge_days",
        default=7,
        help="When a repair is charged to a tenant, the invoice is due after this many days. Example: 7. Default 7.",
    )

    @api.constrains("pm_approval_threshold", "pm_repair_charge_days")
    def _check_pm_maintenance_settings(self):
        for settings in self:
            if min(settings.pm_approval_threshold, settings.pm_repair_charge_days) < 0:
                raise ValidationError(_("Amounts and days cannot be below zero. Please check the Maintenance settings."))
