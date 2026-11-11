from odoo import _, api, fields, models, tools


class PmOverdueInvoice(models.Model):
    """Every unpaid invoice that is past its due date. Read-only, worked out fresh each time.

    In grace  = past due, within the building's grace days  -> the caretaker's chase list.
    In arrears = past the grace days                         -> the manager's arrears view.
    Built with SQL so caretakers can see it without being able to open invoices.
    """

    _name = "pm.overdue.invoice"
    _description = "Overdue Invoice"
    _rec_name = "invoice_number"
    _auto = False
    _order = "days_past_due desc, id"

    invoice_id = fields.Many2one("account.move", string="Invoice", readonly=True)
    invoice_number = fields.Char(string="Invoice number", readonly=True)
    lease_id = fields.Many2one("pm.lease", string="Lease", readonly=True)
    unit_id = fields.Many2one("pm.unit", string="Unit", readonly=True)
    building_id = fields.Many2one("pm.building", string="Building", readonly=True)
    tenant_id = fields.Many2one("res.partner", string="Tenant", readonly=True)
    kind = fields.Selection(
        [("rent", "Rent"), ("service_charge", "Service charge"), ("other", "Other")], string="For", readonly=True
    )
    due_date = fields.Date(string="Due date", readonly=True)
    days_past_due = fields.Integer(string="Days past due", readonly=True)
    amount_due = fields.Float(string="Still to pay", readonly=True)
    stage = fields.Selection(
        [("grace", "In grace period"), ("arrears", "In arrears")], string="Stage", readonly=True
    )
    age_bucket = fields.Selection(
        [
            ("grace", "Within grace period"),
            ("1_30", "1 to 30 days past due"),
            ("31_60", "31 to 60 days past due"),
            ("61_plus", "61 days and over"),
        ],
        string="Age",
        readonly=True,
    )
    last_contact = fields.Date(string="Last contact", readonly=True)
    contact_count = fields.Integer(string="Contacts", readonly=True)
    promised_date = fields.Date(string="Promised to pay by", readonly=True)

    def _flush_sources(self):
        """This model reads straight from the database, so write pending changes there first."""
        self.env["account.move"].flush_model(
            ["name", "state", "move_type", "payment_state", "amount_residual", "invoice_date_due", "pm_lease_id", "pm_kind"]
        )
        self.env["pm.lease"].flush_model(["unit_id", "building_id", "tenant_id"])
        self.env["pm.building"].flush_model(["grace_days"])
        self.env["pm.chase.log"].flush_model()
        # the rows change as time passes and as invoices are paid, so never trust remembered values
        self.invalidate_model()

    @api.model
    def _search(self, *args, **kwargs):
        self._flush_sources()
        return super()._search(*args, **kwargs)

    @api.model
    def _read_group(self, *args, **kwargs):
        self._flush_sources()
        return super()._read_group(*args, **kwargs)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            """
            CREATE OR REPLACE VIEW pm_overdue_invoice AS (
                SELECT m.id AS id,
                       m.id AS invoice_id,
                       m.name AS invoice_number,
                       l.id AS lease_id,
                       l.unit_id AS unit_id,
                       l.building_id AS building_id,
                       l.tenant_id AS tenant_id,
                       m.pm_kind AS kind,
                       m.invoice_date_due AS due_date,
                       (t.today - m.invoice_date_due) AS days_past_due,
                       m.amount_residual AS amount_due,
                       CASE WHEN (t.today - m.invoice_date_due) <= b.grace_days
                            THEN 'grace' ELSE 'arrears' END AS stage,
                       CASE WHEN (t.today - m.invoice_date_due) <= b.grace_days THEN 'grace'
                            WHEN (t.today - m.invoice_date_due) <= 30 THEN '1_30'
                            WHEN (t.today - m.invoice_date_due) <= 60 THEN '31_60'
                            ELSE '61_plus' END AS age_bucket,
                       (SELECT MAX(c.date)::date FROM pm_chase_log c WHERE c.invoice_id = m.id) AS last_contact,
                       (SELECT COUNT(*) FROM pm_chase_log c WHERE c.invoice_id = m.id) AS contact_count,
                       (SELECT c.promised_date FROM pm_chase_log c WHERE c.invoice_id = m.id
                         ORDER BY c.date DESC, c.id DESC LIMIT 1) AS promised_date
                  FROM account_move m
                  JOIN pm_lease l ON l.id = m.pm_lease_id
                  JOIN pm_building b ON b.id = l.building_id
                 CROSS JOIN (SELECT (NOW() AT TIME ZONE 'Africa/Nairobi')::date AS today) t
                 WHERE m.move_type = 'out_invoice'
                   AND m.state = 'posted'
                   AND m.payment_state IN ('not_paid', 'partial')
                   AND m.invoice_date_due < t.today
            )
            """
        )

    def action_log_contact(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Log contact"),
            "res_model": "pm.chase.log",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_invoice_id": self.invoice_id.id},
        }

    def action_record_payment(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Record a payment"),
            "res_model": "pm.payment.wizard",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_invoice_id": self.invoice_id.id},
        }

    def action_open_invoice(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.invoice_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
        }
