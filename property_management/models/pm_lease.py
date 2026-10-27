from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

LEASE_STATES = [
    ("draft", "Draft"),
    ("active", "Active"),
    ("notice", "Notice given"),
    ("ended", "Ended"),
]
OCCUPYING_STATES = ("active", "notice")


class PmLease(models.Model):
    _name = "pm.lease"
    _description = "Lease"
    _inherit = ["mail.thread"]
    _order = "start_date desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    unit_id = fields.Many2one(
        "pm.unit",
        string="Unit",
        required=True,
        tracking=True,
        ondelete="restrict",
        help="The unit being rented. Example: A12.",
    )
    building_id = fields.Many2one(related="unit_id.building_id", store=True, string="Building")
    tenant_id = fields.Many2one(
        "res.partner",
        string="Tenant",
        required=True,
        tracking=True,
        ondelete="restrict",
        help="The person renting the unit. Example: Amina Otieno.",
    )
    start_date = fields.Date(
        string="Start date",
        required=True,
        tracking=True,
        default=fields.Date.context_today,
        help="The day the tenant moves in. Default: today.",
    )
    end_date = fields.Date(
        string="End date",
        tracking=True,
        help="The day the lease ends. Example: 31/12/2026. Leave empty if the lease has no end date.",
    )
    rent_due_day = fields.Integer(
        string="Rent due day",
        default=1,
        help="The day of the month rent is due, from 1 to 28. Example: 5. Default 1.",
    )
    rent_amount = fields.Float(
        string="Monthly rent (KES)",
        tracking=True,
        help="Rent per month for this tenant. It starts as the unit's rent. Example: 18,000.",
    )
    deposit_amount = fields.Float(
        string="Deposit (KES)",
        groups="property_management.group_pm_accountant",
        help="Deposit the tenant pays when moving in. Example: 18,000. Default 0.",
    )
    notice_days = fields.Integer(
        string="Notice days",
        default=30,
        help="How many days of notice the tenant must give before leaving. Default 30.",
    )
    state = fields.Selection(
        LEASE_STATES,
        string="Status",
        default="draft",
        required=True,
        tracking=True,
        copy=False,
    )
    tenant_user_id = fields.Many2one(
        "res.users",
        string="Tenant login",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    tenant_login = fields.Char(string="Login name", related="tenant_user_id.login")
    active = fields.Boolean(default=True)

    # ----- checks -----
    @api.depends("unit_id.number", "tenant_id.name")
    def _compute_name(self):
        for lease in self:
            lease.name = "%s - %s" % (lease.unit_id.number or "", lease.tenant_id.name or "")

    @api.constrains("start_date", "end_date")
    def _check_dates(self):
        for lease in self:
            if lease.end_date and lease.start_date and lease.end_date < lease.start_date:
                raise ValidationError(
                    _("The lease ends before it starts. Please pick an end date after the start date.")
                )

    @api.constrains("rent_due_day", "notice_days", "rent_amount")
    def _check_numbers(self):
        for lease in self:
            if not 1 <= lease.rent_due_day <= 28:
                raise ValidationError(_("Rent due day must be between 1 and 28. Please pick a day in that range."))
            if lease.notice_days < 0 or lease.rent_amount < 0:
                raise ValidationError(_("Notice days and rent cannot be below zero. Please enter 0 or more."))

    @api.constrains("unit_id", "state")
    def _check_one_active_lease(self):
        for lease in self.filtered(lambda rec: rec.state in OCCUPYING_STATES):
            other = self.search(
                [
                    ("unit_id", "=", lease.unit_id.id),
                    ("state", "in", OCCUPYING_STATES),
                    ("id", "!=", lease.id),
                ],
                limit=1,
            )
            if other:
                raise ValidationError(
                    _(
                        "%(unit)s already has a lease with %(tenant)s. End that lease first, then start the new one.",
                        unit=other.unit_id.display_name,
                        tenant=other.tenant_id.name,
                    )
                )

    # ----- defaults -----
    @api.onchange("unit_id")
    def _onchange_unit_id(self):
        if self.unit_id:
            self.rent_amount = self.unit_id.rent_amount

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "rent_amount" not in vals and vals.get("unit_id"):
                vals["rent_amount"] = self.env["pm.unit"].browse(vals["unit_id"]).rent_amount
        return super().create(vals_list)

    def write(self, vals):
        result = super().write(vals)
        if "tenant_id" in vals:
            for lease in self.filtered("tenant_user_id"):
                if lease.tenant_user_id.partner_id != lease.tenant_id:
                    lease._retire_tenant_user()
        return result

    # ----- buttons -----
    def action_start(self):
        status_names = dict(self.env["pm.unit"]._fields["status"].selection)
        for lease in self:
            if lease.state != "draft":
                raise UserError(_("Only a draft lease can be started."))
            unit = lease.unit_id
            if unit.status in ("renovation", "not_for_rent"):
                raise UserError(
                    _(
                        "%(unit)s is marked '%(status)s'. Change the unit's status first, then start the lease.",
                        unit=unit.display_name,
                        status=status_names[unit.status],
                    )
                )
            lease.state = "active"
            unit.status = "occupied"
            lease._after_start()
        return True

    def _after_start(self):
        """When a lease starts: note the deposit (if any) and open the move-in photo list."""
        self.ensure_one()
        lease = self.sudo()
        Deposit = self.env["pm.deposit"].sudo()
        if lease.deposit_amount > 0 and not Deposit.search_count([("lease_id", "=", lease.id)]):
            Deposit.create({"lease_id": lease.id, "amount": lease.deposit_amount})
        self.env["pm.inspection"]._make_for_lease(lease, "move_in")

    def action_give_notice(self):
        for lease in self:
            if lease.state != "active":
                raise UserError(_("Notice can only be given on an active lease."))
            lease.state = "notice"
            lease.unit_id.status = "notice_given"
        return True

    def action_end(self):
        for lease in self:
            if lease.state not in OCCUPYING_STATES:
                raise UserError(_("Only an active lease can be ended."))
            lease.state = "ended"
            if lease.unit_id.status in ("occupied", "notice_given"):
                lease.unit_id.status = "vacant"
            if lease.tenant_user_id:
                lease.tenant_user_id.sudo().active = False
        return True

    def action_open_login_wizard(self):
        self.ensure_one()
        if self.state not in OCCUPYING_STATES:
            raise UserError(_("Start the lease first, then create the tenant login."))
        wizard = self.env["pm.lease.login.wizard"].create({"lease_id": self.id})
        return wizard._reopen()

    # ----- links to the move-in, deposit and problem screens -----
    deposit_count = fields.Integer(string="Deposits", compute="_compute_phase2_counts")
    inspection_count = fields.Integer(string="Inspections", compute="_compute_phase2_counts")
    ticket_count = fields.Integer(string="Problems", compute="_compute_phase2_counts")

    def _compute_phase2_counts(self):
        for lease in self:
            lease.deposit_count = self.env["pm.deposit"].sudo().search_count([("lease_id", "=", lease.id)])
            lease.inspection_count = self.env["pm.inspection"].sudo().search_count([("lease_id", "=", lease.id)])
            lease.ticket_count = self.env["pm.ticket"].sudo().search_count([("lease_id", "=", lease.id)])

    def _open_related(self, model, name):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "view_mode": "tree,form",
            "views": [(False, "tree"), (False, "form")],
            "domain": [("lease_id", "=", self.id)],
            "context": {"default_lease_id": self.id},
        }

    def action_open_deposits(self):
        return self._open_related("pm.deposit", _("Deposit"))

    def action_open_inspections(self):
        return self._open_related("pm.inspection", _("Inspections"))

    def action_open_tickets(self):
        return self._open_related("pm.ticket", _("Problems"))

    # ----- tenant login -----
    def _tenant_login_name(self):
        """Technical login: building, unit number and lease id. Tenants never type this."""
        self.ensure_one()
        unit = self.unit_id
        return ("%s-%s-%s" % (unit.building_id.id, unit.number, self.id)).replace(" ", "").lower()

    def _retire_tenant_user(self):
        """Turn off the old tenant's login and free its name, so a new login can be made."""
        self.ensure_one()
        user = self.tenant_user_id.sudo()
        if user:
            user.write({"active": False, "login": "%s-retired-%s" % (user.login, user.id)})
            self.message_post(
                body=_("The tenant was changed, so the old tenant's login was turned off. Create a new login for the new tenant.")
            )
        self.tenant_user_id = False

    def _create_or_reset_tenant_user(self, password):
        self.ensure_one()
        user = self.tenant_user_id.sudo()
        if user and user.partner_id != self.tenant_id:
            self._retire_tenant_user()
            user = self.env["res.users"]
        if user:
            user.write({"password": password, "active": True})
        else:
            user = (
                self.env["res.users"]
                .sudo()
                .with_context(no_reset_password=True)
                .create(
                    {
                        "partner_id": self.tenant_id.id,
                        "login": self._tenant_login_name(),
                        "password": password,
                        "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
                    }
                )
            )
            self.tenant_user_id = user
        return user

    @api.model
    def _tenant_login_for(self, tenant_name, building):
        """Match the name and building typed on the tenant sign-in page to a login name.

        The building can be typed as the building name (Sunrise Apartments) or the unit number (A12).
        """
        tenant_name = (tenant_name or "").strip().lower()
        building = (building or "").strip().lower()
        if not tenant_name or not building:
            return False
        leases = self.search([("state", "in", OCCUPYING_STATES), ("tenant_user_id", "!=", False)])
        for lease in leases:
            same_name = (lease.tenant_id.name or "").strip().lower() == tenant_name
            same_building = building in (
                (lease.building_id.name or "").strip().lower(),
                (lease.unit_id.number or "").strip().lower(),
            )
            if same_name and same_building:
                return lease.tenant_user_id.login
        return False
