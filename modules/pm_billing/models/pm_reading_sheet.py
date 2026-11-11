from odoo import api, fields, models, _
from odoo.exceptions import UserError

LIVED_IN = ("occupied", "notice_given")


class PmReadingSheet(models.TransientModel):
    """One list of every unit: type the number on the meter, press Save.

    - A unit that has no meter yet is listed too. Type its meter number and the number it shows now:
      that number becomes the starting number (no water is billed this month, billing starts next month).
    - Odd numbers are marked but can still be saved. Only a number lower than last time is refused.
    """

    _name = "pm.reading.sheet"
    _description = "Enter Meter Readings"

    building_id = fields.Many2one(
        "pm.building",
        string="Building",
        required=True,
        default=lambda self: self.env["pm.unit"]._default_building(),
        help="The building you are reading meters in. Default: your only building.",
    )
    date = fields.Date(
        string="Read on",
        required=True,
        default=fields.Date.context_today,
        help="The day you read the meters. Default: today.",
    )
    line_ids = fields.One2many("pm.reading.sheet.line", "sheet_id", string="Units")

    # ----- filling the list -----
    @api.model
    def _line_values(self, building, date):
        """One row per unit that is lived in or already has a meter, skipping units already read this month."""
        if not building or not date:
            return []
        month = date.replace(day=1)
        units = self.env["pm.unit"].search([("building_id", "=", building.id)])
        meters = self.env["pm.meter"].search([("unit_id", "in", units.ids), ("state", "=", "active")])
        by_unit = {meter.unit_id.id: meter for meter in meters}
        done = (
            self.env["pm.meter.reading"]
            .search([("meter_id", "in", meters.ids), ("month", "=", month), ("kind", "=", "actual")])
            .mapped("meter_id")
        )
        rows = []
        for unit in units.sorted(key=lambda rec: (rec.floor, rec.id)):
            meter = by_unit.get(unit.id)
            if meter and meter in done:
                continue
            if not meter and unit.status not in LIVED_IN:
                continue
            rows.append({"unit_id": unit.id, "meter_id": meter.id if meter else False, "serial": meter.serial if meter else ""})
        return rows

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "line_ids" in fields_list and res.get("building_id"):
            building = self.env["pm.building"].browse(res["building_id"])
            res["line_ids"] = [(0, 0, vals) for vals in self._line_values(building, res.get("date") or fields.Date.context_today(self))]
        return res

    @api.onchange("building_id", "date")
    def _onchange_fill_lines(self):
        self.line_ids = [(5, 0, 0)] + [(0, 0, vals) for vals in self._line_values(self.building_id, self.date)]

    # ----- saving -----
    def _entered_lines(self):
        return self.line_ids.filtered(lambda line: line.current > 0)

    def _check_lines(self, lines):
        too_low = lines.filtered(lambda line: line.meter_id and line.current < line.previous)
        if too_low:
            raise UserError(
                _(
                    "Nothing was saved. These readings are lower than last time: %s. Please check the numbers. If a meter was changed, use 'Replace meter' on that unit's meter first.",
                    ", ".join(
                        "%s (typed %s, last time %s)" % (line.unit_id.number, line.current, line.previous) for line in too_low
                    ),
                )
            )

    def _save_entered(self):
        """Save what was typed. Returns (readings made, meters started)."""
        lines = self._entered_lines()
        self._check_lines(lines)
        readings = self.env["pm.meter.reading"]
        started = self.env["pm.meter"]
        for line in lines:
            if line.meter_id:
                readings |= self.env["pm.meter.reading"].create(
                    {"meter_id": line.meter_id.id, "date": self.date, "kind": "actual", "current": line.current}
                )
            else:
                serial = (line.serial or "").strip() or "M-%s" % line.unit_id.number
                started |= self.env["pm.meter"].create(
                    {"unit_id": line.unit_id.id, "serial": serial, "start_reading": line.current, "start_date": self.date}
                )
        return readings, started

    def _done_action(self, readings, started):
        suffix = _(" (%s new meters started)") % len(started) if started else ""
        if readings:
            return {
                "type": "ir.actions.act_window",
                "name": _("Readings saved") + suffix,
                "res_model": "pm.meter.reading",
                "view_mode": "tree,form",
                "views": [(False, "tree"), (False, "form")],
                "domain": [("id", "in", readings.ids)],
                "target": "current",
            }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Meters started"),
                "message": _(
                    "%s meter(s) started. The number you typed is each meter's starting number. Water is billed from the next reading."
                )
                % len(started),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_save(self):
        self.ensure_one()
        if not self._entered_lines():
            raise UserError(_("No readings typed yet. Type the number shown on each meter, then press Save."))
        readings, started = self._save_entered()
        return self._done_action(readings, started)

    def action_estimate_missing(self):
        """Save what was typed, then guess the units that were left empty from their last 3 real readings."""
        self.ensure_one()
        readings, started = self._save_entered() if self._entered_lines() else (self.env["pm.meter.reading"], self.env["pm.meter"])
        estimates = self.env["pm.meter.reading"]
        for line in self.line_ids.filtered(lambda rec: rec.current <= 0 and rec.meter_id):
            estimates |= line.meter_id._make_estimate(self.date)
        if not (readings or estimates or started):
            raise UserError(
                _("Nothing to save. Type the readings you have. Units with no past readings cannot be estimated.")
            )
        return self._done_action(readings | estimates, started)


class PmReadingSheetLine(models.TransientModel):
    _name = "pm.reading.sheet.line"
    _description = "Meter Reading Sheet Line"

    sheet_id = fields.Many2one("pm.reading.sheet", required=True, ondelete="cascade")
    unit_id = fields.Many2one("pm.unit", string="Unit", required=True, readonly=True)
    meter_id = fields.Many2one("pm.meter", string="Meter", readonly=True)
    serial = fields.Char(
        string="Meter number",
        help="The number printed on the meter. For a unit with no meter yet, type it here. It cannot change for a unit that has one.",
    )
    previous = fields.Float(string="Previous reading", compute="_compute_previous")
    current = fields.Float(string="New reading", help="Type the number on the meter. Leave empty if you could not read it.")
    units = fields.Float(string="Units used", compute="_compute_units")
    warning = fields.Char(string="Check", compute="_compute_units")

    @api.depends("meter_id")
    def _compute_previous(self):
        for line in self:
            line.previous = line.meter_id._last_value(real_only=True) if line.meter_id else 0.0

    @api.depends("current", "meter_id")
    def _compute_units(self):
        for line in self:
            line.units = 0.0
            line.warning = False
            if line.current <= 0:
                continue
            if not line.meter_id:
                line.warning = _("First reading: it becomes the starting number. No water is billed this month.")
                continue
            base = line.meter_id._last_value(real_only=True)
            line.units = line.current - base
            if line.current < base:
                line.warning = _("Lower than last time. Please check.")
                continue
            average = line.meter_id._average_real_units()
            if average and line.units > 3 * average:
                line.warning = _("Much higher than usual (about %s units). Check, then save if it is right.") % round(average)
