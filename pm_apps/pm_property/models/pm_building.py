from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

from .pm_config import param_int


class PmBuilding(models.Model):
    _name = "pm.building"
    _description = "Building"
    _inherit = ["mail.thread"]

    name = fields.Char(
        string="Building name",
        required=True,
        tracking=True,
        help="The name people know the building by. Example: Sunrise Apartments.",
    )

    address = fields.Char(
        help="Where the building is. Example: Kilimani, Nairobi.",
    )

    owner_id = fields.Many2one(
        "pm.owner",
        string="Owner",
        required=True,
        tracking=True,
        default=lambda self: self.env["pm.owner"].search([("is_default", "=", True)], limit=1),
        help="Who owns this building. Default: the default owner.",
    )

    deposit_refund_days = fields.Integer(
        string="Deposit refund days",
        default=lambda self: param_int(self.env, "pm_property.deposit_refund_days", 14),
        help="Days allowed to return a deposit after the tenant moves out. Default 14.",
    )

    max_photo_mb = fields.Integer(
        string="Biggest photo (MB)",
        default=lambda self: param_int(self.env, "pm_property.max_photo_mb", 5),
        help="The biggest photo or file a tenant or staff member can upload, in megabytes. Photos are made smaller on the phone first. Default 5.",
    )
    help_contact_ids = fields.One2many("pm.help.contact", "building_id", string="Help contacts")
    active = fields.Boolean(default=True)

    @api.constrains("deposit_refund_days", "max_photo_mb")
    def _check_not_negative(self):
        for building in self:
            if min(building.deposit_refund_days, building.max_photo_mb) < 0:
                raise ValidationError(
                    _("Days and percentages cannot be below zero. Please enter 0 or more.")
                )
