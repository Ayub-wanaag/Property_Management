from odoo import fields, models

from odoo.addons.pm_property.models.pm_config import param_float


class PmBuildingMaintenance(models.Model):
    """What Maintenance adds to a building: when a repair needs approval."""

    _inherit = "pm.building"

    approval_threshold = fields.Float(
        string="Ask approval above (KES)",
        groups="pm_property.group_pm_accountant",
        default=lambda self: param_float(self.env, "pm_maintenance.approval_threshold", 0.0),
        help="Repairs and expenses above this amount wait for approval. Example: 20,000. Leave at 0 for no approval needed.",
    )
