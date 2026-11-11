from odoo import fields, models

PAYMENT_METHODS = [("cash", "Cash"), ("mpesa", "M-Pesa"), ("bank", "Bank")]


class PmReceipt(models.Model):
    _name = "pm.receipt"
    _description = "Payment Receipt"
    _order = "id desc"

    name = fields.Char(string="Receipt number", readonly=True, copy=False)
    date = fields.Date(string="Date", default=fields.Date.context_today, readonly=True)
    lease_id = fields.Many2one("pm.lease", string="Lease", readonly=True)
    tenant_id = fields.Many2one(related="lease_id.tenant_id", store=True, string="Tenant")
    building_id = fields.Many2one(related="lease_id.building_id", store=True, string="Building")
    invoice_id = fields.Many2one("account.move", string="Invoice", readonly=True)
    invoice_number = fields.Char(string="Invoice number", readonly=True)
    kind = fields.Char(string="For", readonly=True)
    amount = fields.Float(string="Amount received", readonly=True)
    method = fields.Selection(PAYMENT_METHODS, string="How", readonly=True)
    transaction_code = fields.Char(string="Transaction code", readonly=True)
    payment_id = fields.Many2one("account.payment", string="Payment", readonly=True)
    claim_id = fields.Many2one("pm.payment.claim", string="Tenant's proof", readonly=True)
    recorded_by = fields.Many2one("res.users", string="Received by", readonly=True)
