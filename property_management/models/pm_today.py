from datetime import date

from odoo import _, api, fields, models

CARETAKER = "property_management.group_pm_caretaker"
ACCOUNTANT = "property_management.group_pm_accountant"
MANAGER = "property_management.group_pm_manager"
OWNER = "property_management.group_pm_owner"


class PmToday(models.TransientModel):
    """The landing page: a few cards, each with a count and a click-through. Worked out fresh every time."""

    _name = "pm.today"
    _description = "Today's overview"

    @api.depends_context("uid")
    def _compute_display_name(self):
        """Without this the breadcrumb shows the technical name, pm.today,12."""
        for card in self:
            card.display_name = _("Today's overview")

    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id)
    payments_count = fields.Integer(string="Payments to check", compute="_compute_counts")
    late_requests_count = fields.Integer(string="Late-payment requests", compute="_compute_counts")
    grace_count = fields.Integer(string="Unpaid, in the grace period", compute="_compute_counts")
    arrears_count = fields.Integer(string="In arrears", compute="_compute_counts")
    vacant_count = fields.Integer(string="Vacant units", compute="_compute_counts")
    occupied_count = fields.Integer(string="Occupied units", compute="_compute_counts")
    unit_count = fields.Integer(string="Units", compute="_compute_counts")
    collected_month = fields.Monetary(string="Collected this month", compute="_compute_counts")
    owner_arrears_count = fields.Integer(string="In arrears (your buildings)", compute="_compute_counts")
    readings_missing_count = fields.Integer(string="Meter readings missing", compute="_compute_counts")
    missed_twice_count = fields.Integer(string="Meters missed twice in a row", compute="_compute_counts")
    leak_count = fields.Integer(string="Water lost above the limit", compute="_compute_counts")
    approvals_count = fields.Integer(string="Repairs waiting for approval", compute="_compute_counts")
    urgent_count = fields.Integer(string="Urgent problems", compute="_compute_counts")
    moveouts_count = fields.Integer(string="Move-outs waiting", compute="_compute_counts")
    my_tickets_count = fields.Integer(string="Problems assigned to me", compute="_compute_counts")

    @api.depends_context("uid")
    def _compute_counts(self):
        user = self.env.user
        is_caretaker, is_accountant = user.has_group(CARETAKER), user.has_group(ACCOUNTANT)
        is_manager, is_owner = user.has_group(MANAGER), user.has_group(OWNER)
        self.env.cr.execute("SELECT date_trunc('month', NOW() AT TIME ZONE 'Africa/Nairobi')::date")
        month_start = self.env.cr.fetchone()[0]
        for today in self:
            overdue = self.env["pm.overdue.invoice"]
            today.payments_count = (
                self.env["pm.payment.claim"].search_count([("state", "=", "submitted")]) if is_accountant else 0
            )
            today.late_requests_count = (
                self.env["pm.late.request"].search_count([("state", "=", "new")]) if is_manager else 0
            )
            today.grace_count = overdue.search_count([("stage", "=", "grace")]) if is_caretaker else 0
            today.arrears_count = overdue.search_count([("stage", "=", "arrears")]) if is_accountant else 0
            today.vacant_count = self.env["pm.unit"].search_count([("status", "=", "vacant")]) if is_manager else 0
            today.readings_missing_count = len(self._meters_without_reading(month_start)) if is_caretaker else 0
            today.missed_twice_count = len(self._meters_missed_twice()) if is_manager else 0
            today.leak_count = len(self._leaky_cycles()) if is_manager else 0
            tickets = self.env["pm.ticket"]
            open_states = ("new", "assigned", "in_progress", "waiting_approval")
            today.approvals_count = tickets.search_count([("state", "=", "waiting_approval")]) if is_manager else 0
            today.urgent_count = (
                tickets.search_count([("priority", "=", "urgent"), ("state", "in", open_states)]) if is_manager else 0
            )
            today.moveouts_count = (
                self.env["pm.move.out.request"].search_count(
                    [("state", "in", ("requested", "photos_uploaded", "reviewed"))]
                )
                if is_manager
                else 0
            )
            today.my_tickets_count = (
                tickets.search_count([("assigned_user_id", "=", user.id), ("state", "in", open_states)])
                if is_caretaker
                else 0
            )
            if is_owner:
                units = self.env["pm.unit"].search([])
                today.unit_count = len(units)
                today.occupied_count = len(units.filtered(lambda unit: unit.status in ("occupied", "notice_given")))
                receipts = self.env["pm.receipt"].search([("date", ">=", month_start)])
                today.collected_month = sum(receipts.mapped("amount"))
                today.owner_arrears_count = overdue.sudo().search_count(
                    [("stage", "=", "arrears"), ("building_id.owner_id.partner_id", "=", user.partner_id.id)]
                )
            else:
                today.unit_count = today.occupied_count = 0
                today.collected_month = 0.0
                today.owner_arrears_count = 0

    # ----- water -----
    @api.model
    def _active_meters(self):
        return self.env["pm.meter"].search(
            [("state", "=", "active"), ("unit_id.status", "in", ("occupied", "notice_given"))]
        )

    @api.model
    def _meters_without_reading(self, month_start):
        """Meters of lived-in units with no real reading yet this month."""
        read = self.env["pm.meter.reading"].search([("month", "=", month_start), ("kind", "=", "actual")]).mapped("meter_id")
        return self._active_meters() - read

    @api.model
    def _meters_missed_twice(self):
        """Meters whose last two readings were both guesses."""
        meters = self.env["pm.meter"]
        for meter in self._active_meters():
            last_two = self.env["pm.meter.reading"].search([("meter_id", "=", meter.id)], order="date desc, id desc", limit=2)
            if len(last_two) == 2 and set(last_two.mapped("kind")) == {"estimated"}:
                meters |= meter
        return meters

    @api.model
    def _leaky_cycles(self):
        """Recent months where the water lost was above the building's limit."""
        recent = self.env["pm.water.cycle"].search([], order="month desc, id desc", limit=6)
        return recent.filtered(lambda cycle: cycle.flag == "owner")

    # ----- opening the page -----
    @api.model
    def action_open_today(self):
        return self.create({})._reopen()

    def _reopen(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Today's overview"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "main",
        }

    def action_refresh(self):
        return self._reopen()

    # ----- the click-throughs -----
    def _open(self, xmlid, domain=None):
        action = self.env["ir.actions.act_window"]._for_xml_id(xmlid)
        if domain is not None:
            action["domain"] = repr(domain)
        return action

    def action_open_payments(self):
        return self._open("property_management.pm_payment_claim_to_check_action")

    def action_open_late_requests(self):
        return self._open("property_management.pm_late_request_action")

    def action_open_grace(self):
        return self._open("property_management.pm_chase_list_action")

    def action_open_arrears(self):
        return self._open("property_management.pm_arrears_action")

    def action_open_vacant(self):
        return self._open("property_management.pm_unit_action", [("status", "=", "vacant")])

    def action_open_units(self):
        return self._open("property_management.pm_unit_action")

    def action_open_readings_missing(self):
        return self._open("property_management.pm_reading_sheet_action")

    def action_open_missed_twice(self):
        return self._open("property_management.pm_meter_action", [("id", "in", self._meters_missed_twice().ids)])

    def action_open_leaks(self):
        return self._open("property_management.pm_water_cycle_action", [("id", "in", self._leaky_cycles().ids)])

    def action_open_approvals(self):
        return self._open("property_management.pm_ticket_approval_action")

    def action_open_urgent(self):
        return self._open("property_management.pm_ticket_urgent_action")

    def action_open_moveouts(self):
        return self._open("property_management.pm_move_out_waiting_action")

    def action_open_my_tickets(self):
        return self._open("property_management.pm_ticket_mine_action")
