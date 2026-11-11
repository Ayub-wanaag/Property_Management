from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class PmMeter(models.Model):
    """The water meter of one unit. The serial can be any text printed on the meter."""

    _name = "pm.meter"
    _description = "Water Meter"
    _inherit = ["mail.thread"]
    _order = "building_id, unit_id, start_date desc, id desc"
    _rec_name = "serial"

    unit_id = fields.Many2one(
        "pm.unit",
        string="Unit",
        required=True,
        tracking=True,
        ondelete="restrict",
        help="The unit this meter measures. Example: A12.",
    )
    building_id = fields.Many2one(related="unit_id.building_id", store=True, string="Building")
    serial = fields.Char(
        string="Meter number",
        required=True,
        tracking=True,
        help="The number printed on the meter. Any text works. Example: SM-0042. It must be different for every meter in the building.",
    )
    start_reading = fields.Float(
        string="Reading when fitted",
        help="What the meter showed on the day it was fitted. Example: 0 for a new meter, 1,240 if it was already running. Default 0.",
    )
    start_date = fields.Date(
        string="Fitted on",
        required=True,
        default=fields.Date.context_today,
        help="The day this meter started being used. Default: today.",
    )
    end_date = fields.Date(string="Replaced on", readonly=True)
    final_reading = fields.Float(string="Last reading before replacement", readonly=True)
    state = fields.Selection(
        [("active", "In use"), ("replaced", "Replaced")],
        string="Status",
        default="active",
        required=True,
        tracking=True,
    )
    notes = fields.Text(string="Notes", help="Anything worth remembering. Example: swapped because the dial was stuck.")
    reading_ids = fields.One2many("pm.meter.reading", "meter_id", string="Readings")
    active = fields.Boolean(default=True)

    # ----- checks -----
    @api.constrains("serial", "unit_id")
    def _check_serial(self):
        for meter in self:
            serial = (meter.serial or "").strip()
            if not serial:
                raise ValidationError(_("Please type the meter number printed on the meter."))
            others = self.with_context(active_test=False).search(
                [("building_id", "=", meter.building_id.id), ("id", "!=", meter.id)]
            )
            clash = others.filtered(lambda other: (other.serial or "").strip().lower() == serial.lower())
            if clash:
                raise ValidationError(
                    _(
                        "Meter number %(serial)s is already used by unit %(unit)s. Please check the number on the meter.",
                        serial=serial,
                        unit=clash[:1].unit_id.number,
                    )
                )

    @api.constrains("unit_id", "state")
    def _check_one_active_meter(self):
        for meter in self.filtered(lambda rec: rec.state == "active"):
            other = self.with_context(active_test=False).search(
                [("unit_id", "=", meter.unit_id.id), ("state", "=", "active"), ("id", "!=", meter.id)], limit=1
            )
            if other:
                raise ValidationError(
                    _(
                        "%(unit)s already has a meter in use (%(serial)s). Use 'Replace meter' to change it.",
                        unit=meter.unit_id.display_name,
                        serial=other.serial,
                    )
                )

    # ----- helpers used by readings and estimates -----
    def _last_reading(self, real_only=False):
        self.ensure_one()
        domain = [("meter_id", "=", self.id)]
        if real_only:
            domain.append(("kind", "in", ("actual", "final")))
        return self.env["pm.meter.reading"].search(domain, order="date desc, id desc", limit=1)

    def _last_value(self, real_only=False):
        """What the meter showed last. real_only skips estimates (they are guesses, not the dial)."""
        self.ensure_one()
        last = self._last_reading(real_only)
        return last.current if last else self.start_reading

    def _average_real_units(self):
        """Average of the last 3 real readings, or None when there is no history to go on."""
        self.ensure_one()
        recent = self.env["pm.meter.reading"].search(
            [("meter_id", "=", self.id), ("kind", "=", "actual")], order="date desc, id desc", limit=3
        )
        if not recent:
            return None
        return sum(recent.mapped("real_units")) / len(recent)

    def _make_estimate(self, date):
        """A guess for a missed reading: the usual use of the last 3 real readings. Nothing when there is no history."""
        self.ensure_one()
        average = self._average_real_units()
        if average is None:
            return self.env["pm.meter.reading"]
        return self.env["pm.meter.reading"].create(
            {
                "meter_id": self.id,
                "date": date,
                "kind": "estimated",
                "current": self._last_value() + round(average, 2),
            }
        )

    def action_replace(self):
        self.ensure_one()
        if self.state != "active":
            raise UserError(_("This meter was already replaced."))
        wizard = self.env["pm.meter.swap.wizard"].create(
            {"meter_id": self.id, "final_reading": self._last_value(real_only=True)}
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Replace meter"),
            "res_model": "pm.meter.swap.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
        }


class PmMeterSwapWizard(models.TransientModel):
    """Replace a meter: write down what the old one showed, then fit the new one."""

    _name = "pm.meter.swap.wizard"
    _description = "Replace a Meter"

    meter_id = fields.Many2one("pm.meter", string="Meter being replaced", required=True, readonly=True)
    unit_id = fields.Many2one(related="meter_id.unit_id", string="Unit")
    current_serial = fields.Char(related="meter_id.serial", string="Old meter number")
    last_reading = fields.Float(
        string="Last reading on file", compute="_compute_last_reading", help="The last real reading we have for the old meter."
    )
    final_reading = fields.Float(
        string="Old meter shows now",
        required=True,
        help="Read the old meter before it is taken out. Example: 1,305. It cannot be lower than the last reading on file.",
    )
    new_serial = fields.Char(
        string="New meter number",
        help="The number printed on the new meter. Example: SM-0099. It is required: the button checks it.",
    )
    new_start_reading = fields.Float(
        string="New meter shows now",
        help="What the new meter shows when fitted. Example: 0. Default 0.",
    )

    @api.depends("meter_id")
    def _compute_last_reading(self):
        for wizard in self:
            wizard.last_reading = wizard.meter_id._last_value(real_only=True) if wizard.meter_id else 0.0

    @api.onchange("meter_id")
    def _onchange_meter_id(self):
        if self.meter_id:
            self.final_reading = self.meter_id._last_value(real_only=True)

    def action_confirm(self):
        self.ensure_one()
        meter = self.meter_id
        new_serial = (self.new_serial or "").strip()
        if not new_serial:
            raise UserError(_("Please type the new meter number."))
        if new_serial.lower() == (meter.serial or "").strip().lower():
            raise UserError(_("The new meter number is the same as the old one. Please check the number on the new meter."))
        today = fields.Date.context_today(self)
        # the old meter's last reading is saved first: it bills the water used until the swap
        self.env["pm.meter.reading"].create(
            {"meter_id": meter.id, "date": today, "kind": "final", "current": self.final_reading}
        )
        meter.write({"state": "replaced", "end_date": today, "final_reading": self.final_reading})
        meter.message_post(body=_("Meter replaced. The old meter showed %s at the swap.") % self.final_reading)
        self.env["pm.meter"].create(
            {
                "unit_id": meter.unit_id.id,
                "serial": new_serial,
                "start_reading": self.new_start_reading,
                "start_date": today,
                "notes": _("Replaced meter %s.") % meter.serial,
            }
        )
        return {"type": "ir.actions.act_window_close"}
