from datetime import timedelta

from odoo import api, fields, models, _
from odoo.addons.pm_property.models.pm_config import param_int
from odoo.exceptions import AccessError, UserError, ValidationError

TICKET_STATES = [
    ("new", "New"),
    ("assigned", "Assigned"),
    ("in_progress", "In progress"),
    ("waiting_approval", "Waiting for approval"),
    ("done", "Done"),
    ("closed", "Closed"),
]
OPEN_STATES = ("new", "assigned", "in_progress", "waiting_approval")
MANAGER = "pm_property.group_pm_manager"


class PmTicket(models.Model):
    """A problem in a unit: a leak, a broken lock, a power fault.

    Urgent problems (burst pipe, power fault, security) must be phoned in; the ticket is still logged.
    A repair that costs more than the building's approval limit waits for approval before work starts.
    """

    _name = "pm.ticket"
    _description = "Problem"
    _inherit = ["mail.thread"]
    _order = "priority_rank, id desc"

    name = fields.Char(string="What is wrong", required=True, tracking=True, help="A few words. Example: Kitchen tap is leaking.")
    lease_id = fields.Many2one("pm.lease", string="Lease", ondelete="set null", help="The tenant who reported it, if any.")
    unit_id = fields.Many2one(
        "pm.unit",
        string="Unit",
        ondelete="restrict",
        help="Where the problem is. Example: A12. Leave empty for a problem in a shared area (stairs, gate, common lights).",
    )
    building_id = fields.Many2one(
        "pm.building",
        string="Building",
        required=True,
        ondelete="restrict",
        default=lambda self: self.env["pm.unit"]._default_building(),
        help="The building. It is filled in from the unit.",
    )
    category = fields.Selection(
        [
            ("plumbing", "Plumbing"),
            ("electrical", "Electrical"),
            ("lighting", "Common lighting"),
            ("carpentry", "Doors, locks, windows"),
            ("painting", "Painting"),
            ("other", "Other"),
        ],
        string="Kind of repair",
        default="other",
        required=True,
        help="What kind of repair it is. Used to see where the maintenance money goes. Default: Other.",
    )
    tenant_id = fields.Many2one(related="lease_id.tenant_id", store=True, string="Tenant")
    description = fields.Text(string="Details", help="What happened, since when, where exactly.")
    priority = fields.Selection(
        [("urgent", "Urgent"), ("normal", "Normal"), ("low", "Can wait")],
        string="How urgent",
        default="normal",
        required=True,
        tracking=True,
        help="Urgent means danger or damage now: burst pipe, power fault, security. Default: Normal.",
    )
    priority_rank = fields.Integer(compute="_compute_rank", store=True)
    state = fields.Selection(TICKET_STATES, string="Status", default="new", required=True, tracking=True, copy=False)
    source = fields.Selection([("tenant", "Reported by the tenant"), ("staff", "Entered by staff")], default="staff", string="Reported by")
    assigned_user_id = fields.Many2one(
        "res.users",
        string="Assigned to",
        domain="[('share', '=', False)]",
        tracking=True,
        help="The staff member who will deal with it.",
    )
    vendor_name = fields.Char(string="Or an outside worker", help="A plumber or electrician. Example: Joseph the plumber, 07XX XXX XXX.")
    estimated_cost = fields.Float(
        string="Estimated cost (KES)",
        groups="pm_property.group_pm_accountant",
        tracking=True,
        help="What the repair will cost. Above the building's approval limit it waits for approval. Example: 35,000. Default 0.",
    )
    actual_cost = fields.Float(
        string="What it really cost (KES)",
        groups="pm_property.group_pm_accountant",
        tracking=True,
        help="The final cost of the repair, once it is done. Example: 3,800. This is the figure charged to whoever pays.",
    )
    charged_to = fields.Selection(
        [
            ("pool", "Shared maintenance money (all tenants)"),
            ("owner", "The owner (normal wear and tear)"),
            ("tenant", "The tenant (damage they caused)"),
        ],
        string="Who pays",
        default="owner",
        required=True,
        tracking=True,
        help="Shared areas are paid from the shared maintenance money. Repairs inside one unit are paid by the owner, unless the tenant caused the damage: then charge the tenant. Default: The owner.",
    )
    repair_invoice_id = fields.Many2one(
        "account.move",
        string="Invoice to the tenant",
        readonly=True,
        copy=False,
        groups="pm_property.group_pm_accountant",
    )
    requires_approval = fields.Boolean(
        string="Needs approval",
        compute="_compute_requires_approval",
        store=True,
        compute_sudo=True,
        help="Ticked when the estimated cost is above the building's approval limit.",
    )
    approved = fields.Boolean(string="Approved", readonly=True, copy=False)
    approved_by = fields.Many2one("res.users", string="Approved by", readonly=True, copy=False)
    approved_on = fields.Datetime(string="Approved on", readonly=True, copy=False)
    attachment_ids = fields.Many2many("ir.attachment", string="Photos")

    @api.depends("priority")
    def _compute_rank(self):
        ranks = {"urgent": 0, "normal": 1, "low": 2}
        for ticket in self:
            ticket.priority_rank = ranks.get(ticket.priority, 1)

    @api.depends("estimated_cost", "building_id.approval_threshold")
    def _compute_requires_approval(self):
        for ticket in self:
            limit = ticket.building_id.approval_threshold
            ticket.requires_approval = bool(limit) and ticket.estimated_cost > limit

    @api.constrains("estimated_cost", "actual_cost")
    def _check_cost(self):
        for ticket in self.sudo():
            if ticket.estimated_cost < 0 or ticket.actual_cost < 0:
                raise ValidationError(_("The cost cannot be below zero. Please enter 0 or more."))

    @api.constrains("unit_id", "building_id", "charged_to", "lease_id")
    def _check_place_and_payer(self):
        for ticket in self:
            if ticket.unit_id and ticket.unit_id.building_id != ticket.building_id:
                raise ValidationError(_("The unit is not in that building. Please check the building."))
            if ticket.charged_to == "tenant" and not ticket.lease_id:
                raise ValidationError(_("To charge the tenant, pick the tenant's lease first."))

    @api.onchange("lease_id")
    def _onchange_lease_id(self):
        if self.lease_id:
            self.unit_id = self.lease_id.unit_id

    @api.onchange("unit_id")
    def _onchange_unit_id(self):
        if self.unit_id:
            self.building_id = self.unit_id.building_id
        elif self.category == "lighting":
            self.charged_to = "pool"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("unit_id") and vals.get("lease_id"):
                vals["unit_id"] = self.env["pm.lease"].sudo().browse(vals["lease_id"]).unit_id.id
            if vals.get("unit_id"):
                vals["building_id"] = self.env["pm.unit"].sudo().browse(vals["unit_id"]).building_id.id
        return super().create(vals_list)

    def write(self, vals):
        if "estimated_cost" in vals:
            for ticket in self.filtered("approved"):
                if ticket.state in OPEN_STATES:
                    ticket.sudo().write({"approved": False, "approved_by": False, "approved_on": False})
                    ticket.message_post(body=_("The cost changed, so the approval was cleared. It needs approving again."))
        return super().write(vals)

    # ----- the flow -----
    def action_assign(self):
        for ticket in self:
            if ticket.state != "new":
                raise UserError(_("Only a new problem can be assigned."))
            if not (ticket.assigned_user_id or (ticket.vendor_name or "").strip()):
                raise UserError(_("Choose who will deal with it: a staff member, or an outside worker."))
            ticket.state = "assigned"
        return True

    def action_start(self):
        for ticket in self:
            if ticket.state not in ("new", "assigned", "waiting_approval"):
                raise UserError(_("Work can only start on a problem that is new or assigned."))
            if ticket.requires_approval and not ticket.approved:
                ticket.state = "waiting_approval"
                ticket.message_post(
                    body=_("The estimated cost is above the approval limit. Waiting for the manager's approval before work starts.")
                )
            else:
                ticket.state = "in_progress"
        return True

    def action_approve(self):
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only a manager can approve a repair."))
        for ticket in self:
            if ticket.state != "waiting_approval":
                raise UserError(_("This repair is not waiting for approval."))
            ticket.write({"approved": True, "approved_by": self.env.user.id, "approved_on": fields.Datetime.now(), "state": "in_progress"})
        return True

    def action_done(self):
        for ticket in self:
            if ticket.state != "in_progress":
                raise UserError(_("Only work in progress can be marked done."))
            ticket.state = "done"
        return True

    def action_charge_tenant(self):
        """Make a draft invoice to the tenant for the real cost of a repair they caused."""
        if not self.env.user.has_group("pm_property.group_pm_accountant"):
            raise AccessError(_("Only the office can charge a tenant."))
        product = self.env.ref("pm_maintenance.product_repair_charge", raise_if_not_found=False)
        if not product:
            raise UserError(_("The 'Repair charge' product is missing. Please upgrade the module."))
        for ticket in self.sudo():
            if ticket.charged_to != "tenant" or not ticket.lease_id:
                raise UserError(_("Set 'Who pays' to the tenant, and pick the tenant's lease, first."))
            if ticket.state not in ("done", "closed"):
                raise UserError(_("Charge the tenant after the work is done, so the real cost is known."))
            if ticket.actual_cost <= 0:
                raise UserError(_("Type what it really cost first."))
            if ticket.repair_invoice_id:
                raise UserError(_("This tenant was already charged: invoice %s.") % ticket.repair_invoice_id.name)
            today = fields.Date.context_today(self)
            invoice = self.env["account.move"].create(
                {
                    "move_type": "out_invoice",
                    "partner_id": ticket.lease_id.tenant_id.id,
                    "pm_lease_id": ticket.lease_id.id,
                    "pm_kind": "other",
                    "invoice_date": today,
                    "invoice_date_due": today + timedelta(days=param_int(self.env, "pm_maintenance.repair_charge_days", 7)),
                    "invoice_origin": _("Repair: %s") % ticket.name,
                    "invoice_line_ids": [
                        (
                            0,
                            0,
                            {
                                "product_id": product.id,
                                "name": _("Repair: %s") % ticket.name,
                                "quantity": 1,
                                "price_unit": ticket.actual_cost,
                                "tax_ids": [(6, 0, [])],
                            },
                        )
                    ],
                }
            )
            ticket.repair_invoice_id = invoice
            ticket.message_post(body=_("Charged to the tenant on draft invoice %s. Look it over, then post it.") % invoice.name)
        return True

    def action_close(self):
        for ticket in self:
            if ticket.state != "done":
                raise UserError(_("Mark the work as done first, then close the problem."))
            ticket.state = "closed"
        return True
