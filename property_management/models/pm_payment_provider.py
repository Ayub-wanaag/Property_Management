from odoo import fields, models


class PmPaymentProvider(models.Model):
    _name = "pm.payment.provider"
    _description = "Payment Method"
    _order = "building_id, sequence, id"

    sequence = fields.Integer(default=10)
    building_id = fields.Many2one("pm.building", string="Building", required=True, ondelete="cascade")
    type = fields.Selection(
        [
            ("mpesa_paybill", "M-Pesa Paybill"),
            ("mpesa_till", "M-Pesa Till"),
            ("bank", "Bank"),
            ("card", "Card (coming later)"),
            ("cash", "Cash"),
        ],
        string="Type",
        required=True,
        default="mpesa_paybill",
        help="How tenants can pay. Card is listed but not working yet.",
    )
    name = fields.Char(
        string="Name shown to tenants",
        required=True,
        help="Example: M-Pesa Paybill.",
    )
    details = fields.Text(
        string="Details and instructions",
        help="Shown to tenants. Example: Paybill 123456, account number is your house number.",
    )
    active = fields.Boolean(default=True)
