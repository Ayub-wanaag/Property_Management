import re

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class PmUnitType(models.Model):
    _name = "pm.unit.type"
    _description = "Unit Type"
    _inherit = ["mail.thread"]
    _order = "default_rent, name"

    building_id = fields.Many2one(
        "pm.building",
        string="Building",
        ondelete="restrict",
        tracking=True,
        default=lambda self: self._default_building(),
        help="The building this type belongs to. Example: Sunrise Apartments. Leave empty if every building uses this type.",
    )
    name = fields.Char(
        string="Type name",
        required=True,
        tracking=True,
        help="What kind of unit this is. Example: 2 bedroom.",
    )
    default_rent = fields.Float(
        string="Default rent (KES)",
        tracking=True,
        help="Monthly rent for this kind of unit. Example: 18,000. Default 0.",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Rent product",
        readonly=True,
        copy=False,
        help="Created automatically. It is used later to put the rent on invoices.",
    )
    unit_ids = fields.One2many("pm.unit", "unit_type_id", string="Units")
    charge_ids = fields.One2many("pm.unit.type.charge", "unit_type_id", string="Service charge items")
    room_ids = fields.One2many(
        "pm.unit.type.room",
        "unit_type_id",
        string="Rooms",
        help="The rooms photographed at move-in and move-out. Every room needs at least one photo. Change the list if this type has other rooms.",
    )
    unit_count = fields.Integer(string="Number of units", compute="_compute_unit_count")
    active = fields.Boolean(default=True)

    @api.model
    def _default_building(self):
        """With only one building, pick it for you."""
        buildings = self.env["pm.building"].search([], limit=2)
        return buildings if len(buildings) == 1 else False

    def init(self):
        """The old rule allowed each name only once in the whole system. Now it is once per building."""
        self.env.cr.execute("ALTER TABLE pm_unit_type DROP CONSTRAINT IF EXISTS pm_unit_type_name_uniq")

    @api.constrains("name", "building_id")
    def _check_name_per_building(self):
        for unit_type in self:
            clash = self.with_context(active_test=False).search_count(
                [
                    ("id", "!=", unit_type.id),
                    ("name", "=", unit_type.name),
                    ("building_id", "=", unit_type.building_id.id or False),
                ]
            )
            if clash:
                raise ValidationError(
                    _("This building already has a unit type called %s (it may be archived). Please use a different name.")
                    % unit_type.name
                )

    @api.depends("name", "building_id.name")
    def _compute_display_name(self):
        for unit_type in self:
            if unit_type.building_id:
                unit_type.display_name = "%s (%s)" % (unit_type.name, unit_type.building_id.name)
            else:
                unit_type.display_name = unit_type.name or ""

    @api.depends("unit_ids")
    def _compute_unit_count(self):
        for unit_type in self:
            unit_type.unit_count = len(unit_type.unit_ids)

    def _default_room_names(self):
        """Rooms to photograph, guessed from the type name: '2 bedroom' gives two bedrooms."""
        self.ensure_one()
        name = (self.name or "").lower()
        if "bedsit" in name:
            return ["Main room", "Kitchen", "Bathroom"]
        found = re.search(r"(\d+)\s*(?:br|bed)", name)
        bedrooms = int(found.group(1)) if found else 1
        beds = ["Bedroom %s" % number for number in range(1, bedrooms + 1)] if bedrooms > 1 else ["Bedroom"]
        return ["Living area", "Kitchen", "Bathroom"] + beds

    def _ensure_rooms(self):
        """Give a type that has no rooms the guessed list (older types, or types made without one)."""
        for unit_type in self.filtered(lambda rec: not rec.room_ids):
            unit_type.write(
                {"room_ids": [(0, 0, {"name": room, "sequence": index}) for index, room in enumerate(unit_type._default_room_names())]}
            )

    def _product_name(self):
        self.ensure_one()
        if self.building_id:
            return _("Rent - %(type)s (%(building)s)") % {"type": self.name, "building": self.building_id.name}
        return _("Rent - %s") % self.name

    @api.model_create_multi
    def create(self, vals_list):
        unit_types = super().create(vals_list)
        for unit_type in unit_types:
            unit_type.product_id = (
                self.env["product.product"]
                .sudo()
                .create(
                    {
                        "name": unit_type._product_name(),
                        "detailed_type": "service",
                        "list_price": unit_type.default_rent,
                        "sale_ok": True,
                        "purchase_ok": False,
                    }
                )
            )
        unit_types._ensure_rooms()
        return unit_types

    def write(self, vals):
        result = super().write(vals)
        if "name" in vals or "default_rent" in vals or "building_id" in vals:
            for unit_type in self.filtered("product_id"):
                unit_type.product_id.sudo().write(
                    {"name": unit_type._product_name(), "list_price": unit_type.default_rent}
                )
        return result


class PmUnitTypeRoom(models.Model):
    """One room of a unit type. Inspections ask for at least one photo of each."""

    _name = "pm.unit.type.room"
    _description = "Room of a Unit Type"
    _order = "unit_type_id, sequence, id"

    unit_type_id = fields.Many2one("pm.unit.type", string="Unit type", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Room", required=True, help="Example: Kitchen.")
