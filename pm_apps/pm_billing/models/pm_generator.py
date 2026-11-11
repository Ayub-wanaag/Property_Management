from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

PAID_STATES = ("paid", "in_payment")


class PmGeneratorSubscription(models.Model):
    """A tenant's generator plan. The period runs from rent day to rent day and is billed on the service charge invoice."""

    _name = "pm.generator.subscription"
    _description = "Generator Plan"
    _inherit = ["mail.thread"]
    _order = "building_id, unit_id"
    _rec_name = "unit_id"

    lease_id = fields.Many2one(
        "pm.lease",
        string="Lease",
        required=True,
        ondelete="cascade",
        help="The tenant's lease. Example: A12 - Amina Otieno.",
    )
    unit_id = fields.Many2one(related="lease_id.unit_id", store=True, string="Unit")
    building_id = fields.Many2one(related="lease_id.building_id", store=True, string="Building")
    tenant_id = fields.Many2one(related="lease_id.tenant_id", string="Tenant")
    on_plan = fields.Boolean(
        string="On the plan",
        default=True,
        tracking=True,
        help="Tick if the tenant pays for the generator. The invoice shows the last choice by default. It can be changed until this period's invoice is paid.",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Plan product",
        required=True,
        domain="[('pm_subscription', '=', True)]",
        default=lambda self: self._default_product(),
        help="The product billed each month. Its price is the monthly price. Default: the Generator plan product.",
    )
    price = fields.Float(related="product_id.list_price", string="Monthly price (KES)")
    period_start = fields.Date(string="This period starts", compute="_compute_period")
    period_end = fields.Date(string="This period ends", compute="_compute_period")
    status = fields.Selection(
        [
            ("off", "Not on the plan"),
            ("not_invoiced", "On the plan, not invoiced yet"),
            ("unpaid", "On the plan, not paid"),
            ("paid", "On the plan, paid"),
        ],
        string="This period",
        compute="_compute_status",
    )
    locked = fields.Boolean(
        string="Locked",
        compute="_compute_status",
        help="Ticked once this period's invoice is paid. The choice can change again from the next rent day.",
    )

    _sql_constraints = [
        ("lease_uniq", "unique(lease_id)", "This lease already has a generator plan record. Open that one instead."),
    ]

    @api.model
    def _default_product(self, building=None):
        """The building's generator product if it has one, otherwise the shared Generator plan product."""
        if building and building.generator_product_id:
            return building.generator_product_id
        return self.env["product.product"].sudo().search(
            [("pm_subscription", "=", True), ("pm_optional", "=", True)], limit=1
        )

    @api.onchange("lease_id")
    def _onchange_lease_product(self):
        if self.lease_id:
            self.product_id = self._default_product(self.lease_id.building_id)

    # ----- the current period -----
    @api.model
    def _period_for(self, rent_day, today):
        start = date(today.year, today.month, rent_day)
        if today < start:
            start -= relativedelta(months=1)
        return start, start + relativedelta(months=1, days=-1)

    @api.depends("lease_id.rent_due_day")
    def _compute_period(self):
        today = fields.Date.context_today(self)
        for sub in self:
            if sub.lease_id:
                sub.period_start, sub.period_end = self._period_for(sub.lease_id.rent_due_day, today)
            else:
                sub.period_start = sub.period_end = False

    def _service_invoice(self):
        """This period's service charge invoice, if one was made."""
        self.ensure_one()
        start, _end = self._period_for(self.lease_id.rent_due_day, fields.Date.context_today(self))
        return (
            self.env["account.move"]
            .sudo()
            .search(
                [
                    ("pm_lease_id", "=", self.lease_id.id),
                    ("move_type", "=", "out_invoice"),
                    ("pm_kind", "=", "service_charge"),
                    ("pm_period_start", "=", start),
                    ("state", "!=", "cancel"),
                ],
                limit=1,
            )
        )

    @api.depends("on_plan", "lease_id", "lease_id.rent_due_day")
    def _compute_status(self):
        for sub in self:
            invoice = sub._service_invoice() if sub.lease_id else self.env["account.move"]
            paid = bool(invoice) and invoice.payment_state in PAID_STATES
            sub.locked = paid
            if not sub.on_plan:
                sub.status = "off"
            elif not invoice:
                sub.status = "not_invoiced"
            else:
                sub.status = "paid" if paid else "unpaid"

    # ----- switching on and off -----
    def _invoice_line_vals(self, start=None):
        self.ensure_one()
        start = start or self.period_start
        return {
            "product_id": self.product_id.id,
            "name": "%s (%s)" % (self.product_id.name, start.strftime("%d/%m/%Y")),
            "quantity": 1,
            "price_unit": self.product_id.list_price,
            "tax_ids": [(6, 0, [])],
        }

    def action_set_plan(self, on):
        """Switch the plan on or off. Locked once this period's invoice is paid."""
        self.ensure_one()
        invoice = self._service_invoice()
        if invoice and invoice.payment_state in PAID_STATES:
            raise UserError(
                _("This period's invoice is already paid, so the generator choice is locked. It can change from the next rent day.")
            )
        self.on_plan = bool(on)
        if invoice and invoice.state == "draft":
            line = invoice.invoice_line_ids.filtered(lambda rec: rec.product_id == self.product_id)
            if on and not line:
                invoice.write({"invoice_line_ids": [(0, 0, self._invoice_line_vals(invoice.pm_period_start))]})
            elif not on and line:
                line.unlink()
        elif invoice and invoice.state == "posted":
            self.lease_id.message_post(
                body=_(
                    "The tenant switched the generator plan %(choice)s after invoice %(invoice)s was posted. Please update that invoice.",
                    choice=_("on") if on else _("off"),
                    invoice=invoice.name,
                )
            )
        return True
