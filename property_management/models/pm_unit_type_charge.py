from odoo import api, fields, models


class PmUnitTypeCharge(models.Model):
    """The fixed part of the service charge: a set amount per month for each type of unit."""

    _name = "pm.unit.type.charge"
    _description = "Service Charge Item"

    @api.depends("product_id.name", "unit_type_id.name")
    def _compute_display_name(self):
        for charge in self:
            charge.display_name = "%s / %s" % (charge.unit_type_id.name or "", charge.product_id.name or "")
    _order = "unit_type_id, id"

    unit_type_id = fields.Many2one("pm.unit.type", string="Unit type", required=True, ondelete="cascade")
    product_id = fields.Many2one(
        "product.product",
        string="Item",
        required=True,
        domain="[('detailed_type', '=', 'service')]",
        help="What the money pays for. Example: Security.",
    )
    amount = fields.Float(string="Amount per month (KES)", help="Example: 500.")
