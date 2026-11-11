import math

from odoo import api, fields, models, _
from odoo.exceptions import UserError

STEPS = [
    ("building", "Building"),
    ("unit_types", "Unit types and prices"),
    ("units", "Create units"),
    ("water", "Water sources"),
    ("service", "Service charges"),
    ("payment", "Payment methods"),
    ("help", "Help contacts"),
    ("tenant", "First tenant"),
]
STEP_KEYS = [key for key, label in STEPS]
STEP_LABELS = dict(STEPS)
MAX_UNITS = 500
PROVIDER_TYPES = [
    ("mpesa_paybill", "M-Pesa Paybill"),
    ("mpesa_till", "M-Pesa Till"),
    ("bank", "Bank"),
    ("cash", "Cash"),
]


class PmSetup(models.Model):
    _name = "pm.setup"
    _description = "Setup Guide"

    name = fields.Char(default="Setup guide")
    step = fields.Selection(STEPS, string="Step", default="building", required=True)
    state = fields.Selection(
        [("draft", "In progress"), ("done", "Finished"), ("cancelled", "Cancelled")],
        default="draft",
        required=True,
    )
    skipped_steps = fields.Char(
        string="Steps you skipped",
        help="The steps you skipped. You can come back to them later.",
    )

    # 1. building
    building_id = fields.Many2one("pm.building", string="Building", readonly=True)
    building_name = fields.Char(
        string="Building name",
        help="The name people know the building by. Example: Sunrise Apartments.",
    )
    building_address = fields.Char(string="Address", help="Where the building is. Example: Kilimani, Nairobi.")
    owner_id = fields.Many2one(
        "pm.owner",
        string="Owner",
        default=lambda self: self.env["pm.owner"].search([("is_default", "=", True)], limit=1),
        help="Who owns this building. Default: the default owner.",
    )

    # 2. unit types
    type_line_ids = fields.One2many("pm.setup.type.line", "setup_id", string="Unit types")

    # 3. units
    units_prefix = fields.Char(string="Starts with", default="A", help="The letter before the number. Example: A gives A1, A2, A3.")
    units_first = fields.Integer(string="First number", default=1, help="Example: 1.")
    units_last = fields.Integer(string="Last number", help="Example: 24 makes units A1 to A24.")
    units_floors = fields.Integer(string="Number of floors", default=1, help="The units are spread evenly across the floors. Example: 4.")
    units_type_id = fields.Many2one(
        "pm.unit.type",
        string="Unit type for all of them",
        help="You can change a single unit's type later.",
    )

    # 4. water
    water_choice = fields.Selection(
        [
            ("ncwsc", "Nairobi Water (NCWSC) only"),
            ("borehole", "Borehole only"),
            ("both", "Both"),
            ("neither", "Neither (no water line on invoices)"),
        ],
        string="Where does the water come from?",
        default="neither",
    )

    # 5. service charge items
    charge_line_ids = fields.One2many("pm.setup.charge.line", "setup_id", string="Service charge items")

    # 6. payment methods
    provider_line_ids = fields.One2many("pm.setup.provider.line", "setup_id", string="Payment methods")

    # 7. help contacts
    help_line_ids = fields.One2many("pm.setup.help.line", "setup_id", string="Help contacts")

    # 8. first tenant
    tenant_name = fields.Char(string="Tenant name", help="Example: Amina Otieno.")
    tenant_phone = fields.Char(string="Phone", help="Example: 0712 345 678.")
    tenant_unit_id = fields.Many2one(
        "pm.unit",
        string="Unit",
        domain="[('building_id', '=', building_id), ('status', 'in', ('vacant', 'reserved'))]",
        help="The unit the tenant moves into.",
    )
    tenant_start_date = fields.Date(string="Moves in on", default=fields.Date.context_today)
    tenant_lease_id = fields.Many2one("pm.lease", string="Lease", readonly=True)

    # ----- opening the guide -----
    @api.model
    def action_open_setup(self):
        """Setup guide menu: continue the unfinished guide, or start a fresh one."""
        setup = self.search([("state", "=", "draft")], order="id desc", limit=1) or self.create({})
        return setup._reopen()

    def action_start_new(self):
        """Start a new setup (for example for another building). An unfinished one is cancelled."""
        self.ensure_one()
        if self.state == "draft":
            self.state = "cancelled"
        return self.create({})._reopen()

    def _reopen(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Setup guide"),
            "res_model": "pm.setup",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "main",
        }

    # ----- moving between steps -----
    def action_next(self):
        self.ensure_one()
        saver = getattr(self, "_save_%s" % self.step, None)
        if saver:
            saver()
        self._forget_skipped(self.step)
        self._advance()
        return self._reopen()

    def action_skip(self):
        self.ensure_one()
        self._remember_skipped(self.step)
        self._advance()
        return self._reopen()

    def action_back(self):
        self.ensure_one()
        index = STEP_KEYS.index(self.step)
        if index > 0:
            self.step = STEP_KEYS[index - 1]
        return self._reopen()

    def action_open_buildings(self):
        return self.env["ir.actions.act_window"]._for_xml_id("property_management.pm_building_action")

    # ----- helpers -----
    def _advance(self):
        index = STEP_KEYS.index(self.step)
        if index + 1 < len(STEP_KEYS):
            self.step = STEP_KEYS[index + 1]
        else:
            self.state = "done"

    def _remember_skipped(self, step):
        labels = [x for x in (self.skipped_steps or "").split(", ") if x]
        if STEP_LABELS[step] not in labels:
            labels.append(STEP_LABELS[step])
        self.skipped_steps = ", ".join(labels)

    def _forget_skipped(self, step):
        labels = [x for x in (self.skipped_steps or "").split(", ") if x]
        if STEP_LABELS[step] in labels:
            labels.remove(STEP_LABELS[step])
        self.skipped_steps = ", ".join(labels)

    def _need_building(self):
        if not self.building_id:
            raise UserError(_("This step needs a building. Go back and finish the Building step, or press Skip this step."))
        return self.building_id

    # ----- saving each step (safe to run twice: nothing is created twice) -----
    def _save_building(self):
        if not self.building_name:
            raise UserError(_("Please type the building name, or press Skip this step."))
        if not self.owner_id:
            raise UserError(_("Please choose the owner of the building."))
        values = {"name": self.building_name, "address": self.building_address, "owner_id": self.owner_id.id}
        if self.building_id:
            self.building_id.write(values)
        else:
            self.building_id = self.env["pm.building"].create(values)

    def _save_unit_types(self):
        Type = self.env["pm.unit.type"]
        for line in self.type_line_ids.filtered(lambda rec: (rec.name or "").strip()):
            name = line.name.strip()
            if not Type.with_context(active_test=False).search([("name", "=", name)], limit=1):
                Type.create({"name": name, "default_rent": line.default_rent})

    def _save_units(self):
        building = self._need_building()
        if self.units_last < self.units_first:
            raise UserError(_("The last number must be at least the first number. Or press Skip this step."))
        total = self.units_last - self.units_first + 1
        if total > MAX_UNITS:
            raise UserError(_("That is more than %s units at once. Please create them in smaller groups.") % MAX_UNITS)
        unit_type = self.units_type_id or self.env["pm.unit.type"].search([], limit=1)
        if not unit_type:
            raise UserError(_("Please choose a unit type. Add unit types in the previous step first."))
        floors = max(self.units_floors, 1)
        per_floor = math.ceil(total / floors)
        prefix = (self.units_prefix or "").strip()
        existing = set(self.env["pm.unit"].with_context(active_test=False).search([("building_id", "=", building.id)]).mapped("number"))
        for index, number in enumerate(range(self.units_first, self.units_last + 1)):
            label = "%s%s" % (prefix, number)
            if label in existing:
                continue
            self.env["pm.unit"].create(
                {
                    "building_id": building.id,
                    "number": label,
                    "floor": index // per_floor + 1,
                    "unit_type_id": unit_type.id,
                }
            )

    def _save_water(self):
        building = self._need_building()
        building.write(
            {
                "uses_ncwsc": self.water_choice in ("ncwsc", "both"),
                "uses_borehole": self.water_choice in ("borehole", "both"),
            }
        )

    def _save_service(self):
        Product = self.env["product.product"].sudo()
        for line in self.charge_line_ids.filtered(lambda rec: rec.unit_type_id and (rec.item or "").strip()):
            name = _("Service charge - %s") % line.item.strip()
            product = Product.search([("name", "=", name), ("detailed_type", "=", "service")], limit=1) or Product.create(
                {"name": name, "detailed_type": "service", "sale_ok": True, "purchase_ok": False}
            )
            charge = self.env["pm.unit.type.charge"].search(
                [("unit_type_id", "=", line.unit_type_id.id), ("product_id", "=", product.id)], limit=1
            )
            if charge:
                charge.amount = line.amount
            else:
                self.env["pm.unit.type.charge"].create(
                    {"unit_type_id": line.unit_type_id.id, "product_id": product.id, "amount": line.amount}
                )

    def _save_payment(self):
        building = self._need_building()
        for line in self.provider_line_ids.filtered(lambda rec: (rec.name or "").strip()):
            if not building.provider_ids.filtered(lambda p: p.name == line.name.strip()):
                self.env["pm.payment.provider"].create(
                    {"building_id": building.id, "type": line.type, "name": line.name.strip(), "details": line.details}
                )

    def _save_help(self):
        building = self._need_building()
        for line in self.help_line_ids.filtered(lambda rec: (rec.label or "").strip()):
            if not building.help_contact_ids.filtered(lambda c: c.label == line.label.strip()):
                self.env["pm.help.contact"].create(
                    {
                        "building_id": building.id,
                        "label": line.label.strip(),
                        "phone": line.phone,
                        "hours": line.hours,
                        "when_to_call": line.when_to_call,
                    }
                )

    def _save_tenant(self):
        if not (self.tenant_name or "").strip() or self.tenant_lease_id:
            return
        self._need_building()
        if not self.tenant_unit_id:
            raise UserError(_("Please choose the unit the tenant moves into, or press Skip this step."))
        partner = self.env["res.partner"].create({"name": self.tenant_name.strip(), "phone": self.tenant_phone})
        lease = self.env["pm.lease"].create(
            {"unit_id": self.tenant_unit_id.id, "tenant_id": partner.id, "start_date": self.tenant_start_date}
        )
        lease.action_start()
        self.tenant_lease_id = lease


class PmSetupTypeLine(models.Model):
    _name = "pm.setup.type.line"
    _description = "Setup: unit type"

    setup_id = fields.Many2one("pm.setup", required=True, ondelete="cascade")
    name = fields.Char(string="Type", help="Example: Bedsitter, 1 bedroom, 2 bedroom.")
    default_rent = fields.Float(string="Rent per month (KES)", help="Example: 18,000.")


class PmSetupChargeLine(models.Model):
    _name = "pm.setup.charge.line"
    _description = "Setup: service charge item"
    _rec_name = "item"

    setup_id = fields.Many2one("pm.setup", required=True, ondelete="cascade")
    unit_type_id = fields.Many2one("pm.unit.type", string="Unit type")
    item = fields.Char(string="Item", help="What it pays for. Example: Security.")
    amount = fields.Float(string="Per month (KES)", help="Example: 500.")


class PmSetupProviderLine(models.Model):
    _name = "pm.setup.provider.line"
    _description = "Setup: payment method"

    setup_id = fields.Many2one("pm.setup", required=True, ondelete="cascade")
    type = fields.Selection(PROVIDER_TYPES, string="Type", default="mpesa_paybill")
    name = fields.Char(string="Name shown to tenants", help="Example: M-Pesa Paybill.")
    details = fields.Char(string="Details", help="Example: Paybill 123456, account number is your house number.")


class PmSetupHelpLine(models.Model):
    _name = "pm.setup.help.line"
    _description = "Setup: help contact"
    _rec_name = "label"

    setup_id = fields.Many2one("pm.setup", required=True, ondelete="cascade")
    label = fields.Char(string="Who", help="Example: Caretaker.")
    phone = fields.Char(string="Phone")
    hours = fields.Char(string="Hours", help="Example: 8am to 6pm.")
    when_to_call = fields.Char(string="When to call", help="Example: Water or power problems.")
