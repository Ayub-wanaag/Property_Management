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
        groups="pm_property.group_pm_caretaker,pm_property.group_pm_accountant",
        help="The lease this invoice is for. It fills itself in when you pick the tenant as customer. You can change it by hand. The chase list and arrears view use it.",
    )

    pm_kind = fields.Selection(
        [("rent", "Rent"), ("service_charge", "Service charge"), ("other", "Other")],
        string="Charge type",
        compute="_compute_pm_kind",
        store=True,
        readonly=False,
        copy=False,
        groups="pm_property.group_pm_caretaker,pm_property.group_pm_accountant",
        help="Rent or service charge. It is worked out from the products on the invoice: a unit's rent product means rent. You can change it. Rent and service charge are judged separately.",
    )

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
