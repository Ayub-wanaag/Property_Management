from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

DEPOSIT_STATES = [
    ("pending", "Waiting to be received"),
    ("held", "Held"),
    ("refund_pending", "Refund to pay"),
    ("refunded", "Refunded"),
    ("disputed", "Disputed"),
]
DEDUCTION_REASONS = [
    ("damage", "Damage"),
    ("unpaid_rent", "Unpaid rent"),
    ("unpaid_service_charge", "Unpaid service charge"),
    ("final_water", "Final water bill"),
    ("other", "Other"),
]
UNPAID = ("not_paid", "partial")


class PmDeposit(models.Model):
    """The deposit a tenant paid when moving in.

    A deposit is money we hold for the tenant (a liability), NOT income. It is never put on an invoice.
    Refund = deposit - deductions. If the deductions are bigger, the tenant still owes the difference.
    """

    _name = "pm.deposit"
    _description = "Deposit"
    _inherit = ["mail.thread"]
    _order = "id desc"

    lease_id = fields.Many2one(
        "pm.lease",
        string="Lease",
        required=True,
        ondelete="restrict",
        tracking=True,
        help="The lease this deposit belongs to. Example: A12 - Amina Otieno.",
    )
    unit_id = fields.Many2one(related="lease_id.unit_id", store=True, string="Unit")
    building_id = fields.Many2one(related="lease_id.building_id", store=True, string="Building")
    tenant_id = fields.Many2one(related="lease_id.tenant_id", store=True, string="Tenant")
    amount = fields.Float(
        string="Deposit paid (KES)",
        required=True,
        tracking=True,
        help="What the tenant paid as deposit. Example: 25,000. It comes from the lease and can be changed here.",
    )
    state = fields.Selection(DEPOSIT_STATES, string="Status", default="pending", required=True, tracking=True, copy=False)
    received_on = fields.Date(string="Received on", readonly=True, copy=False)
    refund_due_date = fields.Date(
        string="Refund due by",
        readonly=True,
        copy=False,
        help="The last day to give the money back: the day the refund was prepared plus the building's refund days.",
    )
    refunded_on = fields.Date(string="Refunded on", readonly=True, copy=False)
    deduction_ids = fields.One2many("pm.deposit.deduction", "deposit_id", string="Deductions")
    dispute_ids = fields.One2many("pm.deposit.dispute", "deposit_id", string="Disputes")
    total_deductions = fields.Float(string="Total deductions (KES)", compute="_compute_amounts")
    refund_amount = fields.Float(string="To refund (KES)", compute="_compute_amounts")
    tenant_owes = fields.Float(string="Tenant still owes (KES)", compute="_compute_amounts")
    open_dispute_count = fields.Integer(string="Open disputes", compute="_compute_amounts")

    _sql_constraints = [
        ("lease_uniq", "unique(lease_id)", "This lease already has a deposit. Open that one instead."),
    ]

    @api.depends("amount", "deduction_ids.amount", "dispute_ids.state")
    def _compute_amounts(self):
        for deposit in self:
            total = sum(deposit.deduction_ids.mapped("amount"))
            left = deposit.amount - total
            deposit.total_deductions = total
            deposit.refund_amount = max(left, 0.0)
            deposit.tenant_owes = max(-left, 0.0)
            deposit.open_dispute_count = len(deposit.dispute_ids.filtered(lambda d: d.state == "open"))

    @api.constrains("amount")
    def _check_amount(self):
        for deposit in self:
            if deposit.amount < 0:
                raise ValidationError(_("The deposit cannot be below zero. Please enter 0 or more."))

    @api.depends("lease_id.name")
    def _compute_display_name(self):
        for deposit in self:
            deposit.display_name = _("Deposit %s") % (deposit.lease_id.name or "")

    # ----- buttons -----
    def action_receive(self):
        for deposit in self:
            if deposit.state != "pending":
                raise UserError(_("This deposit was already received."))
            deposit.write({"state": "held", "received_on": fields.Date.context_today(self)})
        return True

    def action_add_unpaid_balances(self):
        """Take what the tenant still owes (rent, service charge) off the deposit. Each invoice is added once."""
        self.ensure_one()
        self._check_open()
        invoices = (
            self.env["account.move"]
            .sudo()
            .search(
                [
                    ("pm_lease_id", "=", self.lease_id.id),
                    ("move_type", "=", "out_invoice"),
                    ("state", "=", "posted"),
                    ("payment_state", "in", UNPAID),
                ]
            )
        )
        already = self.deduction_ids.mapped("invoice_id")
        added = 0
        for invoice in invoices - already:
            self.env["pm.deposit.deduction"].create(
                {
                    "deposit_id": self.id,
                    "reason": "unpaid_rent" if invoice.pm_kind == "rent" else "unpaid_service_charge",
                    "description": _("Unpaid invoice %s") % invoice.name,
                    "amount": invoice.amount_residual,
                    "invoice_id": invoice.id,
                }
            )
            added += 1
        if not added:
            raise UserError(_("Nothing to add: there are no unpaid invoices that are not already taken off."))
        return True

    def _check_open(self):
        if self.state in ("refunded", "pending"):
            raise UserError(
                _("Deductions can only be added while the deposit is held or waiting to be refunded.")
            )

    def action_prepare_refund(self):
        for deposit in self:
            if deposit.state != "held":
                raise UserError(_("Only a deposit that is held can be prepared for a refund."))
            deposit.write(
                {
                    "state": "refund_pending",
                    "refund_due_date": fields.Date.context_today(self)
                    + relativedelta(days=deposit.building_id.deposit_refund_days),
                }
            )
        return True

    def action_mark_refunded(self):
        for deposit in self:
            if deposit.state != "refund_pending":
                raise UserError(_("Prepare the refund first, then mark it as refunded."))
            if deposit.open_dispute_count:
                raise UserError(_("The tenant disputed a deduction. Decide on the dispute first."))
            deposit.write({"state": "refunded", "refunded_on": fields.Date.context_today(self)})
        return True

    def _tenant_dispute(self, explanation, deduction=None):
        """Used by the tenant portal. A tenant must write why they disagree."""
        self.ensure_one()
        if self.state != "refund_pending":
            raise UserError(_("A deduction can only be disputed while the refund is being prepared."))
        dispute = self.env["pm.deposit.dispute"].create(
            {"deposit_id": self.id, "deduction_id": deduction.id if deduction else False, "explanation": explanation}
        )
        self.state = "disputed"
        self.message_post(body=_("The tenant disputed a deduction: %s") % explanation)
        return dispute


class PmDepositDeduction(models.Model):
    _name = "pm.deposit.deduction"
    _description = "Deposit Deduction"
    _order = "deposit_id, id"

    deposit_id = fields.Many2one("pm.deposit", string="Deposit", required=True, ondelete="cascade")
    reason = fields.Selection(DEDUCTION_REASONS, string="Why", required=True, default="damage")
    description = fields.Char(
        string="What for", required=True, help="A short note the tenant can read. Example: Broken kitchen window."
    )
    amount = fields.Float(string="Amount (KES)", required=True, help="Example: 3,500.")
    invoice_id = fields.Many2one("account.move", string="Invoice", readonly=True, copy=False)
    attachment_ids = fields.Many2many("ir.attachment", string="Photos", help="Photos that show the damage.")

    @api.constrains("amount")
    def _check_amount(self):
        for line in self:
            if line.amount <= 0:
                raise ValidationError(_("A deduction must be more than zero. Please enter the amount."))

    @api.depends("description", "amount")
    def _compute_display_name(self):
        for line in self:
            line.display_name = "%s (%s)" % (line.description or "", line.amount)

    def _check_editable(self):
        for line in self:
            if line.deposit_id.state in ("refunded", "pending"):
                raise UserError(_("Deductions can only change while the deposit is held or waiting to be refunded."))

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._check_editable()
        return lines

    def write(self, vals):
        self._check_editable()
        return super().write(vals)

    def unlink(self):
        self._check_editable()
        return super().unlink()


class PmDepositDispute(models.Model):
    """A tenant says a deduction is wrong. They must write why."""

    _name = "pm.deposit.dispute"
    _description = "Deposit Dispute"
    _inherit = ["mail.thread"]
    _order = "id desc"

    deposit_id = fields.Many2one("pm.deposit", string="Deposit", required=True, ondelete="cascade")
    deduction_id = fields.Many2one("pm.deposit.deduction", string="Deduction disputed", ondelete="set null")
    tenant_id = fields.Many2one(related="deposit_id.tenant_id", store=True, string="Tenant")
    explanation = fields.Text(string="Why the tenant disagrees", required=True, help="The tenant must explain.")
    state = fields.Selection([("open", "Waiting for a decision"), ("resolved", "Decided")], default="open", required=True, tracking=True)
    resolution_note = fields.Text(string="Decision", help="What was decided and why. The tenant will see it.")
    resolved_by = fields.Many2one("res.users", string="Decided by", readonly=True)
    resolved_on = fields.Datetime(string="Decided on", readonly=True)

    @api.depends("deposit_id.lease_id.name")
    def _compute_display_name(self):
        for dispute in self:
            dispute.display_name = _("Dispute: %s") % (dispute.deposit_id.lease_id.name or "")

    @api.constrains("explanation")
    def _check_explanation(self):
        for dispute in self:
            if not (dispute.explanation or "").strip():
                raise ValidationError(_("Please write why you disagree. A dispute cannot be sent without a reason."))

    def action_resolve(self):
        for dispute in self:
            if dispute.state != "open":
                raise UserError(_("This dispute was already decided."))
            if not (dispute.resolution_note or "").strip():
                raise UserError(_("Please write the decision in the 'Decision' box, then press Decide."))
            dispute.write({"state": "resolved", "resolved_by": self.env.user.id, "resolved_on": fields.Datetime.now()})
            deposit = dispute.deposit_id
            if deposit.state == "disputed" and not deposit.open_dispute_count:
                deposit.state = "refund_pending"
        return True
