from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    pm_is_water = fields.Boolean(
        string="Water charge",
        help="Tick for the product used for the water line on service charge invoices. Meter readings and the water rate show in the Water tab, not with the other costs. Default: No.",
    )
    pm_optional = fields.Boolean(
        string="Tenant can switch it on or off",
        help="Tick if tenants choose whether to pay for this. Example: the generator plan. Default: No.",
    )
    pm_subscription = fields.Boolean(
        string="Monthly plan",
        help="Tick if this is billed every month from rent day to rent day. Example: the generator plan. Default: No.",
    )
