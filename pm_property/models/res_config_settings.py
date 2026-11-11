from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pm_deposit_refund_days = fields.Integer(
        string="Deposit refund days",
        config_parameter="pm_property.deposit_refund_days",
        default=14,
        help="Days after move-out by which the deposit should be refunded. Example: 14. Default 14.",
    )
    pm_max_photo_mb = fields.Integer(
        string="Biggest photo (MB)",
        config_parameter="pm_property.max_photo_mb",
        default=5,
        help="Largest photo a tenant or caretaker can upload. Example: 5. Default 5.",
    )
    pm_notice_days = fields.Integer(
        string="Notice days",
        config_parameter="pm_property.notice_days",
        default=30,
        help="Days of notice a tenant must give before leaving. Example: 30. Default 30.",
    )

    @api.constrains("pm_deposit_refund_days", "pm_max_photo_mb", "pm_notice_days")
    def _check_pm_property_settings(self):
        for settings in self:
            if min(settings.pm_deposit_refund_days, settings.pm_notice_days) < 0 or settings.pm_max_photo_mb < 1:
                raise ValidationError(
                    _("Days cannot be below zero and a photo must be at least 1 MB. Please check the Property settings.")
                )
