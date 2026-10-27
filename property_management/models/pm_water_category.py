from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

OPEN_END = 999999


class PmWaterCategory(models.Model):
    """A water billing category (Domestic, Commercial, ...). Each has its own price tiers for water and for sewerage."""

    _name = "pm.water.category"
    _description = "Water Billing Category"
    _order = "name"

    name = fields.Char(string="Category", required=True, help="Example: Domestic/Residential.")
    active = fields.Boolean(default=True)
    is_borehole = fields.Boolean(
        string="Borehole category",
        help="Tick for borehole customers. Water is not charged, and sewerage is charged on all the units used. Default: No.",
    )
    sewerage_percent = fields.Float(
        string="Sewerage on % of units",
        default=75.0,
        help="Sewerage is charged on this percent of the water units used. Default 75. Not used for borehole categories.",
    )
    tier_ids = fields.One2many("pm.water.category.tier", "category_id", string="Price tiers")
    water_tier_ids = fields.One2many(
        "pm.water.category.tier", "category_id", string="Water tiers", domain=[("tier_type", "=", "water")]
    )
    sewerage_tier_ids = fields.One2many(
        "pm.water.category.tier", "category_id", string="Sewerage tiers", domain=[("tier_type", "=", "sewerage")]
    )

    _sql_constraints = [
        ("name_uniq", "unique(name)", "There is already a water category with this name."),
    ]

    # ----- the sums -----
    @staticmethod
    def _tiers_total(tiers, units):
        """Charge `units` through the tiers, lowest first. Each tier takes only the units that fit in it."""
        total = 0.0
        remaining = float(units)
        for tier in tiers.sorted("from_unit"):
            if remaining <= 0:
                break
            capacity = tier.to_unit - tier.from_unit + 1
            in_tier = min(remaining, capacity)
            total += in_tier * tier.price_per_unit
            remaining -= in_tier
        return total

    def _charge(self, units):
        """Return (water, sewerage) in KES for `units` of water used."""
        self.ensure_one()
        if units <= 0:
            return 0.0, 0.0
        if self.is_borehole:
            return 0.0, self._tiers_total(self.sewerage_tier_ids, units)
        water = self._tiers_total(self.water_tier_ids, units)
        sewerage = self._tiers_total(self.sewerage_tier_ids, units * (self.sewerage_percent or 0.0) / 100.0)
        return water, sewerage

    def _check_ready(self):
        """Stop with a clear message if the tiers are missing, so an invoice never goes out at zero."""
        for category in self:
            needed = category.sewerage_tier_ids if category.is_borehole else category.water_tier_ids
            if not needed:
                raise ValidationError(
                    _(
                        "The water category '%(name)s' has no %(kind)s prices yet. Open Water > Water categories and add them.",
                        name=category.name,
                        kind=_("sewerage") if category.is_borehole else _("water"),
                    )
                )


class PmWaterCategoryTier(models.Model):
    _name = "pm.water.category.tier"
    _description = "Water Category Price Tier"
    _order = "category_id, tier_type, from_unit"

    category_id = fields.Many2one("pm.water.category", string="Category", required=True, ondelete="cascade")
    tier_type = fields.Selection(
        [("water", "Water"), ("sewerage", "Sewerage")], string="Type", required=True, default="water"
    )
    from_unit = fields.Integer(string="From unit", required=True, help="First unit of this tier. Example: 7.")
    to_unit = fields.Integer(
        string="To unit", required=True, default=OPEN_END, help="Last unit of this tier. Use 999999 for no upper limit."
    )
    price_per_unit = fields.Float(string="Price per unit (KES)", required=True, digits=(16, 2))

    @api.constrains("from_unit", "to_unit")
    def _check_range(self):
        for tier in self:
            if tier.from_unit < 0 or tier.to_unit < tier.from_unit:
                raise ValidationError(_("A tier must end on or after the unit it starts at, for example 1 to 6."))
