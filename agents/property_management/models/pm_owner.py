from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class PmOwner(models.Model):
    _name = "pm.owner"
    _description = "Property Owner"
    _inherit = ["mail.thread"]

    partner_id = fields.Many2one(
        "res.partner",
        string="Owner",
        required=True,
        tracking=True,
        help="The person or company that owns the building. Example: Mary Wanjiku.",
    )
    name = fields.Char(related="partner_id.name", store=True)
    commission_rate = fields.Float(
        string="Commission %",
        default=0.0,
        tracking=True,
        groups="property_management.group_pm_accountant,property_management.group_pm_owner",
        help="Share of the rent kept by the management company, in percent. Example: 10. Default 0.",
    )
    payout_notes = fields.Text(
        string="How to pay this owner",
        groups="property_management.group_pm_accountant,property_management.group_pm_owner",
        help="Bank or M-Pesa details for payouts. Example: Equity Bank, account name Mary Wanjiku.",
    )
    is_default = fields.Boolean(
        string="Default owner",
        default=False,
        help="The owner used automatically for new buildings. Default: No.",
    )
    active = fields.Boolean(default=True)
    building_ids = fields.One2many("pm.building", "owner_id", string="Buildings")
    show_commission = fields.Boolean(compute="_compute_show_commission")

    def _compute_show_commission(self):
        # Commission only matters once there is more than one owner.
        several_owners = self.sudo().search_count([]) > 1
        for owner in self:
            owner.show_commission = several_owners

    @api.constrains("commission_rate")
    def _check_commission_rate(self):
        for owner in self:
            if not 0 <= owner.commission_rate <= 100:
                raise ValidationError(
                    _("Commission must be between 0 and 100. Please enter a number like 10.")
                )
