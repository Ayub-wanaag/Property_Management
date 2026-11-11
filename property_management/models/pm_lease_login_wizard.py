import secrets

from odoo import api, fields, models, _
from odoo.exceptions import UserError

PASSWORD_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alike characters


class PmLeaseLoginWizard(models.TransientModel):
    _name = "pm.lease.login.wizard"
    _description = "Tenant Login"

    @api.depends("house")
    def _compute_display_name(self):
        for wizard in self:
            wizard.display_name = _("Tenant login for %s") % (wizard.house or "")

    lease_id = fields.Many2one("pm.lease", required=True, readonly=True)
    tenant_name = fields.Char(related="lease_id.tenant_id.name", string="Tenant")
    house = fields.Char(compute="_compute_house", string="House")
    login_url = fields.Char(compute="_compute_login_url", string="Sign-in page")
    password = fields.Char(
        default=lambda self: self._random_password(),
        help="The password the tenant will use. At least 6 characters. Example: k7m2xp. We suggest one for you.",
    )
    state = fields.Selection([("ask", "Ask"), ("done", "Done")], default="ask")

    @api.model
    def _random_password(self):
        return "".join(secrets.choice(PASSWORD_LETTERS) for i in range(6))

    @api.depends("lease_id")
    def _compute_house(self):
        for wizard in self:
            unit = wizard.lease_id.unit_id
            wizard.house = "%s - %s" % (unit.building_id.name, unit.number) if unit else ""

    def _compute_login_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for wizard in self:
            wizard.login_url = "%s/tenant/login" % base_url

    def _reopen(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Tenant login"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
        }

    def action_create(self):
        self.ensure_one()
        if len(self.password or "") < 6:
            raise UserError(_("The password must have at least 6 characters. Please type a longer one."))
        self.lease_id._create_or_reset_tenant_user(self.password)
        self.state = "done"
        return self._reopen()
