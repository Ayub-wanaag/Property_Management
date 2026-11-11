from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

READING_KINDS = [
    ("actual", "Read from the meter"),
    ("estimated", "Estimated (missed)"),
    ("final", "Last reading of an old meter"),
]


class PmMeterReading(models.Model):
    """One reading of one unit's meter.

    previous  = what we billed up to (the last reading of any kind, estimates included)
    last_real = what the dial really showed last time (estimates skipped)
    A real reading after an estimate is the true-up: units = current - previous, which is the
    real use since the last real reading minus what the estimate already billed. It can be below zero (a credit).
    """

    _name = "pm.meter.reading"
    _description = "Meter Reading"
    _order = "date desc, id desc"
    _rec_name = "display_name"

    meter_id = fields.Many2one(
        "pm.meter",
        string="Meter",
        required=True,
        ondelete="restrict",
        index=True,
        help="The meter that was read.",
    )
    unit_id = fields.Many2one(related="meter_id.unit_id", store=True, string="Unit", index=True)
    building_id = fields.Many2one(related="meter_id.building_id", store=True, string="Building")
    date = fields.Date(
        string="Read on",
        required=True,
        default=fields.Date.context_today,
        help="The day the meter was read. Default: today.",
    )
    month = fields.Date(string="Month", compute="_compute_month", store=True)
    kind = fields.Selection(READING_KINDS, string="Type", default="actual", required=True)
    previous = fields.Float(string="Previous reading", readonly=True, copy=False, help="What we billed up to last time.")
    last_real = fields.Float(string="Last real reading", readonly=True, copy=False)
    current = fields.Float(string="New reading", required=True, help="The number on the meter. Example: 1,252.")
    units = fields.Float(string="Units billed", compute="_compute_units", store=True, help="New reading minus previous reading.")
    real_units = fields.Float(string="Real use", compute="_compute_units", store=True)
    entered_by = fields.Many2one("res.users", string="Entered by", default=lambda self: self.env.user, readonly=True)
    notes = fields.Char(string="Note")
    line_ids = fields.One2many("account.move.line", "pm_reading_id", string="Invoice lines")
    billed = fields.Boolean(string="On an invoice", compute="_compute_billed", store=True)
    warning = fields.Char(string="Check", compute="_compute_warning")

    @api.depends("date")
    def _compute_month(self):
        for reading in self:
            reading.month = reading.date.replace(day=1) if reading.date else False

    @api.depends("current", "previous", "last_real", "kind")
    def _compute_units(self):
        for reading in self:
            reading.units = reading.current - reading.previous
            reading.real_units = reading.current - reading.last_real if reading.kind in ("actual", "final") else 0.0

    @api.depends("line_ids")
    def _compute_billed(self):
        for reading in self:
            reading.billed = bool(reading.line_ids)

    @api.depends("meter_id", "current", "kind", "last_real")
    def _compute_warning(self):
        for reading in self:
            reading.warning = False
            if reading.kind != "actual" or not reading.meter_id:
                continue
            base = reading.last_real if reading.id else reading.meter_id._last_value(real_only=True)
            if reading.current and reading.current < base:
                reading.warning = _("Lower than last time (%s)") % base
                continue
            average = reading.meter_id._average_real_units()
            if average and (reading.current - base) > 3 * average:
                reading.warning = _("Much higher than usual (about 3 times)")

    @api.depends("meter_id.serial", "date", "current")
    def _compute_display_name(self):
        for reading in self:
            reading.display_name = "%s / %s" % (reading.meter_id.serial or "", reading.current)

    @api.constrains("current", "last_real", "kind")
    def _check_not_lower(self):
        for reading in self:
            if reading.kind in ("actual", "final") and reading.current < reading.last_real:
                raise ValidationError(
                    _(
                        "The reading %(current)s for unit %(unit)s is lower than last time (%(last)s). Please check the number. If the meter was changed, use 'Replace meter' on that unit's meter first.",
                        current=reading.current,
                        unit=reading.unit_id.number,
                        last=reading.last_real,
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            meter = self.env["pm.meter"].browse(vals.get("meter_id"))
            if meter.state == "replaced" and vals.get("kind", "actual") != "final":
                raise ValidationError(
                    _(
                        "Meter %(serial)s was replaced. Please enter the reading on the unit's new meter.",
                        serial=meter.serial,
                    )
                )
            vals.setdefault("previous", meter._last_value())
            vals.setdefault("last_real", meter._last_value(real_only=True))
        return super().create(vals_list)

    def write(self, vals):
        if any(self.mapped("billed")) and {"current", "meter_id", "date", "kind"} & set(vals):
            raise ValidationError(
                _("This reading is already on an invoice, so it cannot be changed. Fix it with a credit note instead.")
            )
        return super().write(vals)

    def unlink(self):
        if any(self.mapped("billed")):
            raise ValidationError(_("This reading is already on an invoice, so it cannot be deleted."))
        return super().unlink()
