from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

class PmWaterCycle(models.Model):
    """One month of water for one building: what the water cost, how much was used, and the rate per unit.

    rate = total cost / units drawn (the main meter if typed, otherwise the units billed to the units).
    """

    _name = "pm.water.cycle"
    _description = "Water Costs for a Month"
    _inherit = ["mail.thread"]
    _order = "month desc, id desc"

    building_id = fields.Many2one(
        "pm.building",
        string="Building",
        required=True,
        tracking=True,
        ondelete="restrict",
        default=lambda self: self.env["pm.unit"]._default_building(),
        help="The building these water costs belong to. Default: your only building.",
    )
    month = fields.Date(
        string="Month",
        required=True,
        tracking=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1),
        help="Pick any day of the month. Example: 05/10/2026 means October 2026. Default: this month.",
    )
    uses_ncwsc = fields.Boolean(related="building_id.uses_ncwsc")
    uses_borehole = fields.Boolean(related="building_id.uses_borehole")
    ncwsc_cost = fields.Float(
        string="Nairobi Water (NCWSC) bill (KES)",
        tracking=True,
        help="What the NCWSC water and sewerage bill came to this month. Example: 18,200.",
    )
    borehole_cost = fields.Float(
        string="Borehole cost (KES)",
        tracking=True,
        help="What running the borehole cost this month (power, pump, servicing). Example: 6,000.",
    )
    main_meter_units = fields.Float(
        string="Main meter units",
        tracking=True,
        help="Units used by the whole building, from the main meter. Example: 220. Leave at 0 if there is no main meter: the rate then uses the units billed to the units.",
    )
    total_cost = fields.Float(string="Total water cost (KES)", compute="_compute_totals", store=True)
    billed_units = fields.Float(string="Units billed to the units", compute="_compute_totals")
    drawn_units = fields.Float(string="Units the cost is shared over", compute="_compute_totals")
    rate = fields.Float(string="Rate per unit (KES)", compute="_compute_totals", digits=(16, 2))
    loss_units = fields.Float(string="Units lost", compute="_compute_totals")
    loss_percent = fields.Float(string="Lost %", compute="_compute_totals", digits=(16, 1))
    invoiced = fields.Boolean(
        string="Invoices made",
        readonly=True,
        copy=False,
        help="Ticked once service charge invoices were made with this rate. The costs are then locked.",
    )

    _sql_constraints = [
        (
            "building_month_uniq",
            "unique(building_id, month)",
            "This building already has water costs for that month. Open the existing one instead.",
        ),
    ]

    # ----- the sums -----
    def _month_end(self):
        self.ensure_one()
        return self.month + relativedelta(months=1, days=-1)

    def _readings(self):
        self.ensure_one()
        return self.env["pm.meter.reading"].search(
            [("building_id", "=", self.building_id.id), ("date", ">=", self.month), ("date", "<=", self._month_end())]
        )

    @api.depends(
        "building_id.uses_ncwsc",
        "building_id.uses_borehole",
        "ncwsc_cost",
        "borehole_cost",
        "main_meter_units",
        "month",
    )
    def _compute_totals(self):
        for cycle in self:
            building = cycle.building_id
            cycle.total_cost = (cycle.ncwsc_cost if building.uses_ncwsc else 0.0) + (
                cycle.borehole_cost if building.uses_borehole else 0.0
            )
            billed = sum(cycle._readings().mapped("units")) if cycle.month and building else 0.0
            cycle.billed_units = billed
            drawn = cycle.main_meter_units or billed
            cycle.drawn_units = drawn
            cycle.rate = round(cycle.total_cost / drawn, 2) if drawn else 0.0
            main = cycle.main_meter_units
            loss = max(main - billed, 0.0) if main else 0.0
            cycle.loss_units = loss
            cycle.loss_percent = (loss / main * 100.0) if main else 0.0

    # ----- checks -----
    @api.constrains("building_id")
    def _check_source(self):
        for cycle in self:
            if not (cycle.building_id.uses_ncwsc or cycle.building_id.uses_borehole):
                raise ValidationError(
                    _(
                        "%(building)s has no water source ticked, so there is no water to bill. Tick 'Uses Nairobi Water' or 'Uses a borehole' on the building first.",
                        building=cycle.building_id.name,
                    )
                )

    @api.constrains("ncwsc_cost", "borehole_cost", "main_meter_units")
    def _check_not_negative(self):
        for cycle in self:
            if min(cycle.ncwsc_cost, cycle.borehole_cost, cycle.main_meter_units) < 0:
                raise ValidationError(_("Costs and units cannot be below zero. Please enter 0 or more."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("month"):
                vals["month"] = fields.Date.to_date(vals["month"]).replace(day=1)
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("month"):
            vals["month"] = fields.Date.to_date(vals["month"]).replace(day=1)
        if self.filtered("invoiced") and {"ncwsc_cost", "borehole_cost", "main_meter_units", "month", "building_id"} & set(vals):
            raise UserError(
                _("Invoices were already made with this rate, so the costs are locked. Fix a wrong invoice with a credit note.")
            )
        return super().write(vals)

    @api.depends("building_id.name", "month")
    def _compute_display_name(self):
        for cycle in self:
            cycle.display_name = "%s / %s" % (cycle.building_id.name or "", cycle.month.strftime("%m/%Y") if cycle.month else "")

    def action_open_readings(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Readings for %s") % self.display_name,
            "res_model": "pm.meter.reading",
            "view_mode": "tree,form",
            "views": [(False, "tree"), (False, "form")],
            "domain": [("id", "in", self._readings().ids)],
        }
