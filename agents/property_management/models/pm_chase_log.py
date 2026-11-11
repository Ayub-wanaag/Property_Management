from odoo import _, api, fields, models


class PmChaseLog(models.Model):
    _name = "pm.chase.log"
    _description = "Contact Log"

    @api.depends("invoice_number", "channel")
    def _compute_display_name(self):
        for log in self:
            how = dict(log._fields["channel"].selection).get(log.channel, "")
            log.display_name = _("Contact about %(invoice)s (%(how)s)", invoice=log.invoice_number or "", how=how)
    _order = "date desc, id desc"

    invoice_id = fields.Many2one("account.move", string="Invoice", required=True, ondelete="cascade", index=True)
    invoice_number = fields.Char(string="Invoice number", readonly=True)
    lease_id = fields.Many2one("pm.lease", string="Lease", readonly=True)
    user_id = fields.Many2one(
        "res.users", string="Contacted by", default=lambda self: self.env.user, readonly=True
    )
    date = fields.Datetime(string="When", default=fields.Datetime.now, readonly=True)
    channel = fields.Selection(
        [("call", "Phone call"), ("whatsapp", "WhatsApp"), ("visit", "Visit")],
        string="How",
        required=True,
        default="call",
        help="How you contacted the tenant. Default: phone call.",
    )
    note = fields.Text(
        string="What was said",
        help="A short note. Example: Said she will pay on Friday.",
    )
    promised_date = fields.Date(
        string="Promised to pay by",
        help="The date the tenant promised to pay. Example: 20/10/2026. Leave empty if no date was given.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        # Caretakers cannot open invoices, so the invoice number and lease are copied here with sudo.
        moves = self.env["account.move"].sudo()
        for vals in vals_list:
            invoice_id = vals.get("invoice_id") or self.env.context.get("default_invoice_id")
            if invoice_id:
                invoice = moves.browse(invoice_id)
                vals.setdefault("invoice_number", invoice.name)
                vals.setdefault("lease_id", invoice.pm_lease_id.id)
        return super().create(vals_list)
