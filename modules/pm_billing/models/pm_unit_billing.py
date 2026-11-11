from odoo import fields, models


class PmUnitBilling(models.Model):
    """What Billing adds to a unit: the water category it is billed by."""

    _inherit = "pm.unit"

    water_category_id = fields.Many2one(
        "pm.water.category",
        string="Water category",
        help="Fill this only if this unit is billed differently from its building. Example: a shop on the ground floor of a residential building. Leave empty to use the building's water category.",
    )

    def _water_category(self):
        """The category this unit is billed by: its own, otherwise its building's. Empty if neither is set."""
        self.ensure_one()
        return self.water_category_id or self.building_id.water_category_id
