from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    pm_original_due_date = fields.Date(
        string="Original due date",
        copy=False,
        help="The due date before the tenant was given more time. It is kept for the record.",
    )

    pm_extended = fields.Boolean(
        string="More time given",
        copy=False,
        help="Ticked when the manager approved a late-payment request and moved the due date.",
    )

    pm_period_start = fields.Date(
        string="Period starts",
        copy=False,
        help="The first day this invoice covers (the rent day). Used to avoid billing the same month twice.",
    )

    pm_period_end = fields.Date(string="Period ends", copy=False, help="The last day this invoice covers.")

    pm_mpesa_can_pay = fields.Boolean(
        string="Can pay with M-Pesa",
        compute="_compute_pm_mpesa_can_pay",
        help="Shows the Pay now button on the tenant's invoice page.",
    )

    @api.depends("state", "payment_state", "move_type")
    def _compute_pm_mpesa_can_pay(self):
        enabled = self.env["pm.mpesa.api"]._is_enabled()
        for move in self:
            record = move.sudo()  # tenants cannot read the lease field themselves
            move.pm_mpesa_can_pay = bool(
                enabled
                and record.move_type == "out_invoice"
                and record.state == "posted"
                and record.payment_state in ("not_paid", "partial")
                and record.pm_lease_id
            )

    def _has_to_be_paid(self):
        """Odoo's own online "Pay Now" button is hidden on rent invoices.

        Tenants pay with the M-Pesa "Pay now" button, so a second button would only confuse them.
        """
        self.ensure_one()
        if self.sudo().pm_lease_id:
            return False
        return super()._has_to_be_paid()

    pm_has_water = fields.Boolean(string="Has a water line", compute="_compute_pm_water")

    pm_water_line_ids = fields.Many2many(
        "account.move.line",
        string="Water",
        compute="_compute_pm_water",
        help="The water lines with their meter readings. Other costs stay in Invoice Lines.",
    )

    @api.depends("invoice_line_ids.pm_is_water")
    def _compute_pm_water(self):
        for move in self:
            lines = move.invoice_line_ids.filtered("pm_is_water")
            move.pm_water_line_ids = lines
            move.pm_has_water = bool(lines)


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    pm_is_water = fields.Boolean(
        related="product_id.product_tmpl_id.pm_is_water",
        store=True,
        string="Water line",
        help="Water lines show their meter readings in the Water tab and in their own table on the printed invoice.",
    )
    pm_reading_id = fields.Many2one("pm.meter.reading", string="Meter reading", copy=False, ondelete="set null", index=True)
    pm_meter_serial = fields.Char(string="Meter number", copy=False)
    pm_prev_reading = fields.Float(string="Previous", copy=False)
    pm_curr_reading = fields.Float(string="Now", copy=False)
    pm_is_estimated = fields.Boolean(string="Estimated", copy=False)
