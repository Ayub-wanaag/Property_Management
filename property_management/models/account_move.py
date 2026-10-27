from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    pm_lease_id = fields.Many2one(
        "pm.lease",
        string="Lease",
        compute="_compute_pm_lease_id",
        store=True,
        readonly=False,
        copy=False,
        index=True,
        ondelete="restrict",
        groups="property_management.group_pm_caretaker,property_management.group_pm_accountant",
        help="The lease this invoice is for. It fills itself in when you pick the tenant as customer. You can change it by hand. The chase list and arrears view use it.",
    )

    pm_kind = fields.Selection(
        [("rent", "Rent"), ("service_charge", "Service charge"), ("other", "Other")],
        string="Charge type",
        compute="_compute_pm_kind",
        store=True,
        readonly=False,
        copy=False,
        groups="property_management.group_pm_caretaker,property_management.group_pm_accountant",
        help="Rent or service charge. It is worked out from the products on the invoice: a unit's rent product means rent. You can change it. Rent and service charge are judged separately.",
    )
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

    @api.depends("pm_lease_id", "invoice_line_ids.product_id")
    def _compute_pm_kind(self):
        rent_products = self.env["pm.unit.type"].sudo().with_context(active_test=False).search([]).mapped("product_id")
        for move in self:
            if not move.pm_lease_id:
                move.pm_kind = False
            elif move.invoice_line_ids.mapped("product_id") & rent_products:
                move.pm_kind = "rent"
            else:
                move.pm_kind = "service_charge"

    @api.depends("partner_id", "move_type")
    def _compute_pm_lease_id(self):
        leases = self.env["pm.lease"].sudo()
        for move in self:
            lease = leases
            if move.move_type == "out_invoice" and move.partner_id:
                tenant_ids = (move.partner_id | move.partner_id.commercial_partner_id).ids
                domain = [("tenant_id", "in", tenant_ids)]
                order = "start_date desc, id desc"
                # the tenant's current lease, or their latest finished one (people can owe rent after moving out)
                lease = leases.search(domain + [("state", "in", ("active", "notice"))], order=order, limit=1)
                lease = lease or leases.search(domain + [("state", "=", "ended")], order=order, limit=1)
            move.pm_lease_id = lease


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
