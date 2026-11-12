from odoo import api, fields, models, _

UNIT_STATUSES = [
    ("vacant", "Vacant"),
    ("reserved", "Reserved"),
    ("occupied", "Occupied"),
    ("notice_given", "Notice given"),
    ("renovation", "Under renovation"),
    ("not_for_rent", "Not for rent"),
]

STAFF_VIEW_GROUPS = "pm_property.group_pm_caretaker,pm_property.group_pm_accountant"


class PmUnit(models.Model):
    _name = "pm.unit"
    _description = "Unit"
    _inherit = ["mail.thread"]
    _rec_name = "number"
    _order = "building_id, floor, id"

    building_id = fields.Many2one(
        "pm.building",
        string="Building",
        required=True,
        tracking=True,
        ondelete="restrict",
        default=lambda self: self._default_building(),
        help="The building this unit is in. Default: your only building.",
    )
    floor = fields.Integer(
        help="Which floor the unit is on. Example: 2. Use 0 for the ground floor. Default 0.",
    )
    number = fields.Char(
        string="Unit number",
        required=True,
        tracking=True,
        help="The name on the door. Example: A12. It must be different for every unit in the building.",
    )
    unit_type_id = fields.Many2one(
        "pm.unit.type",
        string="Unit type",
        required=True,
        tracking=True,
        help="What kind of unit this is. Example: 2 bedroom. The type sets the default rent.",
    )
    rent_override = fields.Float(
        string="Special rent (KES)",
        tracking=True,
        help="Fill this only if this unit has a different rent from its type. Example: 19,500. Leave at 0 to use the type's rent.",
    )
    rent_amount = fields.Float(
        string="Rent (KES)",
        compute="_compute_rent_amount",
        store=True,
        help="The rent for this unit: the special rent if there is one, otherwise the type's rent.",
    )
    status = fields.Selection(
        UNIT_STATUSES,
        string="Status",
        default="vacant",
        required=True,
        tracking=True,
        help="Where the unit stands today. Default: Vacant.",
    )
    lease_ids = fields.One2many("pm.lease", "unit_id", string="Leases")
    current_lease_id = fields.Many2one(
        "pm.lease",
        string="Current lease",
        compute="_compute_current_lease",
        groups=STAFF_VIEW_GROUPS,
    )
    status_log_ids = fields.One2many(
        "pm.unit.status.log",
        "unit_id",
        string="Status history",
        groups=STAFF_VIEW_GROUPS,
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "number_per_building_uniq",
            "unique(building_id, number)",
            "This building already has a unit with that number. Please use a different number.",
        ),
    ]

    @api.model
    def _default_building(self):
        buildings = self.env["pm.building"].search([], limit=2)
        return buildings if len(buildings) == 1 else False

    @api.depends("rent_override", "unit_type_id.default_rent")
    def _compute_rent_amount(self):
        for unit in self:
            unit.rent_amount = unit.rent_override or unit.unit_type_id.default_rent

    @api.depends("lease_ids.state")
    def _compute_current_lease(self):
        for unit in self:
            unit.current_lease_id = unit.lease_ids.filtered(
                lambda lease: lease.state in ("active", "notice")
            )[:1]

    @api.depends("number", "building_id.name")
    def _compute_display_name(self):
        for unit in self:
            if unit.building_id:
                unit.display_name = "%s / %s" % (unit.building_id.name, unit.number)
            else:
                unit.display_name = unit.number or ""

    @api.model_create_multi
    def create(self, vals_list):
        units = super().create(vals_list)
        user_id = self.env.user.id
        log = self.env["pm.unit.status.log"].sudo()
        for unit in units:
            log.create({"unit_id": unit.id, "status": unit.status, "changed_by": user_id})
        return units

    def write(self, vals):
        if "status" not in vals:
            return super().write(vals)
        changed = self.filtered(lambda unit: unit.status != vals["status"])
        result = super().write(vals)
        now = fields.Datetime.now()
        user_id = self.env.user.id
        log = self.env["pm.unit.status.log"].sudo()
        for unit in changed:
            log.search([("unit_id", "=", unit.id), ("date_to", "=", False)]).write({"date_to": now})
            log.create(
                {"unit_id": unit.id, "status": unit.status, "date_from": now, "changed_by": user_id}
            )
        return result


class PmUnitStatusLog(models.Model):
    _name = "pm.unit.status.log"
    _description = "Unit Status History"

    @api.depends("unit_id", "status")
    def _compute_display_name(self):
        for log in self:
            status = dict(log._fields["status"].selection).get(log.status, "")
            log.display_name = "%s: %s" % (log.unit_id.display_name or "", status)
    _order = "date_from desc, id desc"

    unit_id = fields.Many2one("pm.unit", string="Unit", required=True, ondelete="cascade", index=True)
    status = fields.Selection(UNIT_STATUSES, string="Status", required=True)
    date_from = fields.Datetime(string="From", required=True, default=fields.Datetime.now)
    date_to = fields.Datetime(string="To", help="Empty means this is the current status.")
    changed_by = fields.Many2one("res.users", string="Changed by")
