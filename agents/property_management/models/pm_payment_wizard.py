from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .pm_receipt import PAYMENT_METHODS

UNPAID = ("not_paid", "partial")


class PmPaymentWizard(models.TransientModel):
    _name = "pm.payment.wizard"
    _description = "Record a Payment"

    @api.depends_context("uid")
    def _compute_display_name(self):
        for wizard in self:
            wizard.display_name = _("Record a payment")

    claim_id = fields.Many2one("pm.payment.claim", string="Tenant's proof", readonly=True)
    invoice_id = fields.Many2one(
        "account.move",
        string="Invoice",
        required=True,
        domain="[('pm_lease_id', '!=', False), ('move_type', '=', 'out_invoice'), ('state', '=', 'posted'), ('payment_state', 'in', ('not_paid', 'partial'))]",
        help="The invoice being paid. Only unpaid invoices are listed.",
    )
    amount_due = fields.Monetary(related="invoice_id.amount_residual", string="Still to pay")
    currency_id = fields.Many2one(related="invoice_id.currency_id")
    amount = fields.Float(string="Amount received", required=True, help="What you actually received. Example: 18,000.")
    method = fields.Selection(PAYMENT_METHODS, string="How it was paid", required=True, default="cash")
    transaction_code = fields.Char(string="Transaction code", help="For M-Pesa or bank. Example: SHL7K2M9QX.")
    journal_id = fields.Many2one(
        "account.journal",
        string="Paid into",
        compute="_compute_journal_id",
        store=True,
        readonly=False,
        domain="[('type', 'in', ('bank', 'cash'))]",
        help="Where the money went. Cash goes to the cash journal, the rest to the bank. Change it only if needed.",
    )
    checked = fields.Boolean(string="I checked this against the real statement")
    claim_note = fields.Char(compute="_compute_claim_note")

    @api.depends("method", "invoice_id")
    def _compute_journal_id(self):
        journals = self.env["account.journal"].sudo()
        for wizard in self:
            company = (wizard.invoice_id.company_id or self.env.company).id
            kind = "cash" if wizard.method == "cash" else "bank"
            wizard.journal_id = journals.search([("type", "=", kind), ("company_id", "=", company)], limit=1) or journals.search(
                [("type", "in", ("bank", "cash")), ("company_id", "=", company)], limit=1
            )

    @api.depends("claim_id", "amount")
    def _compute_claim_note(self):
        for wizard in self:
            claim = wizard.claim_id
            if claim and claim.parsed_amount and abs(claim.parsed_amount - wizard.amount) > 0.009:
                wizard.claim_note = _(
                    "The message says %(msg)s, the tenant claimed %(claimed)s. Check the real statement and type what you received.",
                    msg="{:,.2f}".format(claim.parsed_amount),
                    claimed="{:,.2f}".format(claim.amount),
                )
            else:
                wizard.claim_note = False

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        claim = self.env["pm.payment.claim"].browse(self.env.context.get("default_claim_id"))
        if claim:
            invoice = self.env["account.move"].sudo().search(
                [
                    ("pm_lease_id", "=", claim.lease_id.id),
                    ("pm_kind", "=", claim.kind),
                    ("move_type", "=", "out_invoice"),
                    ("state", "=", "posted"),
                    ("payment_state", "in", UNPAID),
                ],
                order="invoice_date_due, id",
                limit=1,
            )
            values.update(
                {
                    "invoice_id": invoice.id,
                    "amount": claim.amount,
                    "method": {"mpesa": "mpesa", "bank": "bank"}.get(claim.method, "mpesa"),
                    "transaction_code": claim.transaction_code,
                }
            )
        elif values.get("invoice_id"):
            invoice = self.env["account.move"].sudo().browse(values["invoice_id"])
            values["amount"] = invoice.amount_residual
        return values

    @api.onchange("invoice_id")
    def _onchange_invoice_id(self):
        if self.invoice_id and not self.claim_id:
            self.amount = self.invoice_id.amount_residual

    def _reopen(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Record a payment"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
        }

    def action_confirm(self):
        self.ensure_one()
        invoice = self.invoice_id.sudo()
        if self.amount <= 0:
            raise UserError(_("The amount must be more than zero."))
        if self.claim_id and not self.checked:
            raise UserError(_("Please tick the box to confirm you checked the real statement."))
        if invoice.state != "posted" or invoice.payment_state not in UNPAID:
            raise UserError(_("This invoice is already paid, or it is not confirmed yet."))
        if not self.journal_id:
            raise UserError(_("No bank or cash journal was found. Set up Accounting first."))
        code = (self.transaction_code or "").strip().upper()
        today = fields.Date.context_today(self)
        payment = (
            self.env["account.payment.register"]
            .sudo()
            .with_company(invoice.company_id)
            .with_context(active_model="account.move", active_ids=invoice.ids)
            .create(
                {
                    "amount": self.amount,
                    "journal_id": self.journal_id.id,
                    "payment_date": today,
                    "communication": code or invoice.name,
                }
            )
            ._create_payments()
        )
        receipt = self.env["pm.receipt"].sudo().create(
            {
                "name": self.env["ir.sequence"].sudo().next_by_code("pm.receipt"),
                "date": today,
                "lease_id": invoice.pm_lease_id.id,
                "invoice_id": invoice.id,
                "invoice_number": invoice.name,
                "kind": dict(invoice._fields["pm_kind"].selection).get(invoice.pm_kind, ""),
                "amount": self.amount,
                "method": self.method,
                "transaction_code": code,
                "payment_id": payment.id,
                "claim_id": self.claim_id.id,
                "recorded_by": self.env.user.id,
            }
        )
        if self.claim_id:
            self.claim_id.sudo().write(
                {
                    "state": "verified",
                    "verified_by": self.env.user.id,
                    "verified_on": fields.Datetime.now(),
                    "invoice_id": invoice.id,
                    "payment_id": payment.id,
                    "receipt_id": receipt.id,
                    "transaction_code": self.claim_id.transaction_code or code or False,
                }
            )
        return self.env.ref("property_management.action_report_receipt").report_action(receipt, config=False)
