from odoo import fields, models, _


class PmLeaseMaintenance(models.Model):
    """What Maintenance adds to a lease: the list of problems reported for it."""

    _inherit = "pm.lease"

    ticket_count = fields.Integer(string="Problems", compute="_compute_ticket_count")

    def _compute_ticket_count(self):
        for lease in self:
            lease.ticket_count = self.env["pm.ticket"].sudo().search_count([("lease_id", "=", lease.id)])

    def action_open_tickets(self):
        return self._open_related("pm.ticket", _("Problems"))
