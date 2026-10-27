from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class PmLateRequest(models.Model):
    _name = "pm.late.request"
    _description = "Late-Payment Request"

    @api.depends("invoice_number")
    def _compute_display_name(self):
        for request in self:
            request.display_name = _("More time for %s") % (request.invoice_number or "")
    _inherit = ["mail.thread"]
    _order = "id desc"

    invoice_id = fields.Many2one("account.move", string="Invoice", required=True, ondelete="cascade")
    invoice_number = fields.Char(string="Invoice number", readonly=True)
    lease_id = fields.Many2one("pm.lease", string="Lease", required=True, ondelete="restrict")
    tenant_id = fields.Many2one(related="lease_id.tenant_id", store=True, string="Tenant")
    building_id = fields.Many2one(related="lease_id.building_id", store=True, string="Building")
    current_due_date = fields.Date(string="Due date now", readonly=True)
    proposed_date = fields.Date(string="Tenant proposes to pay by", required=True, tracking=True)
    reason = fields.Text(string="Reason", required=True, help="Why the tenant needs more time.")
    state = fields.Selection(
        [("new", "Waiting for a decision"), ("approved", "Approved"), ("declined", "Declined")],
        string="Status",
        default="new",
        required=True,
        tracking=True,
        copy=False,
    )
    decided_by = fields.Many2one("res.users", string="Decided by", readonly=True, copy=False)
    decided_on = fields.Datetime(string="Decided on", readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        invoices = self.env["account.move"].sudo()
        for vals in vals_list:
            invoice = invoices.browse(vals.get("invoice_id"))
            vals.setdefault("invoice_number", invoice.name)
            vals.setdefault("current_due_date", invoice.invoice_date_due)
            vals.setdefault("lease_id", invoice.pm_lease_id.id)
        return super().create(vals_list)

    @api.constrains("invoice_id", "proposed_date", "state")
    def _check_request(self):
        for request in self:
            due = request.invoice_id.sudo().invoice_date_due
            if request.state == "new" and due and request.proposed_date <= due:
                raise ValidationError(
                    _("The new date must be after the current due date (%s). Please pick a later date.")
                    % due.strftime("%d/%m/%Y")
                )
            other = self.sudo().search(
                [("invoice_id", "=", request.invoice_id.id), ("state", "=", "new"), ("id", "!=", request.id)], limit=1
            )
            if request.state == "new" and other:
                raise ValidationError(_("There is already a request waiting for this invoice."))

    def action_approve(self):
        for request in self:
            if request.state != "new":
                raise UserError(_("This request was already decided."))
            invoice = request.invoice_id.sudo()
            invoice.write(
                {
                    "pm_original_due_date": invoice.pm_original_due_date or invoice.invoice_date_due,
                    "invoice_date_due": request.proposed_date,
                    "pm_extended": True,
                }
            )
            request.write({"state": "approved", "decided_by": self.env.user.id, "decided_on": fields.Datetime.now()})
        return True

    def action_decline(self):
        for request in self:
            if request.state != "new":
                raise UserError(_("This request was already decided."))
            request.write({"state": "declined", "decided_by": self.env.user.id, "decided_on": fields.Datetime.now()})
        return True
