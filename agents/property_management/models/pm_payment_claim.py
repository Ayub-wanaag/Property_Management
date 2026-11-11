import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

CODE_RE = re.compile(r"\b[A-Z0-9]{10}\b")
AMOUNT_RE = re.compile(r"(?:KSH|KES)\.?\s*([\d,]+(?:\.\d{1,2})?)")


def parse_mpesa_text(text):
    """Pull the transaction code (10 letters and digits) and the amount out of a pasted M-Pesa message."""
    text = (text or "").upper()
    code = ""
    for token in CODE_RE.findall(text):
        if any(c.isdigit() for c in token) and any(c.isalpha() for c in token):
            code = token
            break
    match = AMOUNT_RE.search(text)
    amount = float(match.group(1).replace(",", "")) if match else 0.0
    return code, amount


class PmPaymentClaim(models.Model):
    """A tenant's claim that they paid. A claim is never proof: staff check it against the real statement."""

    _name = "pm.payment.claim"
    _description = "Payment Claim"
    _inherit = ["mail.thread"]
    _order = "id desc"

    name = fields.Char(compute="_compute_name", store=True)
    lease_id = fields.Many2one("pm.lease", string="Lease", required=True, ondelete="restrict", tracking=True)
    tenant_id = fields.Many2one(related="lease_id.tenant_id", store=True, string="Tenant")
    building_id = fields.Many2one(related="lease_id.building_id", store=True, string="Building")
    kind = fields.Selection(
        [("rent", "Rent"), ("service_charge", "Service charge")],
        string="Paying for",
        required=True,
        default="rent",
        help="Rent or service charge. They are checked separately.",
    )
    amount = fields.Float(string="Amount claimed", required=True, tracking=True, help="What the tenant says they paid. Example: 18,000.")
    method = fields.Selection(
        [("mpesa", "M-Pesa"), ("bank", "Bank"), ("other", "Other")],
        string="How they paid",
        default="mpesa",
    )
    transaction_code = fields.Char(
        string="Transaction code",
        copy=False,
        tracking=True,
        help="The 10-character M-Pesa code, for example SHL7K2M9QX. Filled in from the pasted message. Every code can be used only once.",
    )
    sms_text = fields.Text(string="Pasted message")
    parsed_amount = fields.Float(string="Amount in the message", readonly=True)
    amount_mismatch = fields.Boolean(string="Amounts differ", compute="_compute_amount_mismatch")
    attachment_ids = fields.Many2many("ir.attachment", string="Proof files (photo or PDF)")
    state = fields.Selection(
        [("submitted", "Waiting to be checked"), ("verified", "Verified"), ("rejected", "Rejected")],
        string="Status",
        default="submitted",
        required=True,
        tracking=True,
        copy=False,
    )
    verified_by = fields.Many2one("res.users", string="Checked by", readonly=True, copy=False)
    verified_on = fields.Datetime(string="Checked on", readonly=True, copy=False)
    rejection_reason = fields.Text(string="Why rejected", help="Write the reason, then press Reject. The tenant will see it.")
    invoice_id = fields.Many2one("account.move", string="Invoice paid", readonly=True, copy=False)
    payment_id = fields.Many2one("account.payment", string="Payment", readonly=True, copy=False)
    receipt_id = fields.Many2one("pm.receipt", string="Receipt", readonly=True, copy=False)

    @api.depends("lease_id.name", "transaction_code")
    def _compute_name(self):
        for claim in self:
            claim.name = ("%s %s" % (claim.lease_id.name or "", claim.transaction_code or "")).strip()

    @api.depends("amount", "parsed_amount")
    def _compute_amount_mismatch(self):
        for claim in self:
            claim.amount_mismatch = bool(claim.parsed_amount) and abs(claim.parsed_amount - claim.amount) > 0.009

    # ----- reading the pasted message -----
    @api.model
    def _prepare_vals(self, vals, record=None):
        if vals.get("sms_text"):
            code, amount = parse_mpesa_text(vals["sms_text"])
            vals.setdefault("parsed_amount", amount)
            have_code = vals.get("transaction_code") or (record and record.transaction_code)
            if code and not have_code:
                vals["transaction_code"] = code
        if vals.get("transaction_code"):
            vals["transaction_code"] = vals["transaction_code"].strip().upper()
        return vals

    @api.onchange("sms_text")
    def _onchange_sms_text(self):
        code, amount = parse_mpesa_text(self.sms_text)
        self.parsed_amount = amount
        if code and not self.transaction_code:
            self.transaction_code = code

    def _check_code_free(self, code, exclude=None):
        """Friendly message for a code that was already used (the database rule is the safety net)."""
        if not code:
            return
        domain = [("transaction_code", "=", code)]
        if exclude:
            domain.append(("id", "not in", exclude.ids))
        if self.sudo().search(domain, limit=1):
            raise ValidationError(
                _("The transaction code %s was already submitted. A code can only be used once.") % code
            )

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        seen = set()
        for vals in vals_list:
            vals = self._prepare_vals(dict(vals))
            code = vals.get("transaction_code")
            self._check_code_free(code)
            if code in seen:
                raise ValidationError(
                    _("The transaction code %s was already submitted. A code can only be used once.") % code
                )
            seen.add(code)
            prepared.append(vals)
        return super().create(prepared)

    def write(self, vals):
        if "sms_text" in vals or "transaction_code" in vals:
            vals = self._prepare_vals(dict(vals), self if len(self) == 1 else None)
            self._check_code_free(vals.get("transaction_code"), exclude=self)
        return super().write(vals)

    @api.constrains("transaction_code")
    def _check_code_unique(self):
        for claim in self.filtered("transaction_code"):
            other = self.sudo().search(
                [("transaction_code", "=", claim.transaction_code), ("id", "!=", claim.id)], limit=1
            )
            if other:
                raise ValidationError(
                    _("The transaction code %s was already submitted. A code can only be used once.")
                    % claim.transaction_code
                )

    def init(self):
        # one code, one claim, whatever the capital letters
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS pm_payment_claim_code_uniq
                ON pm_payment_claim (UPPER(transaction_code))
             WHERE transaction_code IS NOT NULL AND transaction_code <> ''
            """
        )

    # ----- staff actions -----
    def action_verify(self):
        self.ensure_one()
        if self.state != "submitted":
            raise UserError(_("This claim was already checked."))
        wizard = self.env["pm.payment.wizard"].with_context(default_claim_id=self.id).create({})
        return wizard._reopen()

    def action_reject(self):
        for claim in self:
            if claim.state != "submitted":
                raise UserError(_("This claim was already checked."))
            if not (claim.rejection_reason or "").strip():
                raise UserError(_("Please write why you are rejecting it in the 'Why rejected' box, then press Reject."))
            claim.write({"state": "rejected", "verified_by": self.env.user.id, "verified_on": fields.Datetime.now()})
        return True
