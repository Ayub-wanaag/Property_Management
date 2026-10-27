from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class PmBuilding(models.Model):
    _name = "pm.building"
    _description = "Building"
    _inherit = ["mail.thread"]

    name = fields.Char(
        string="Building name",
        required=True,
        tracking=True,
        help="The name people know the building by. Example: Sunrise Apartments.",
    )
    address = fields.Char(
        help="Where the building is. Example: Kilimani, Nairobi.",
    )
    owner_id = fields.Many2one(
        "pm.owner",
        string="Owner",
        required=True,
        tracking=True,
        default=lambda self: self.env["pm.owner"].search([("is_default", "=", True)], limit=1),
        help="Who owns this building. Default: the default owner.",
    )
    uses_ncwsc = fields.Boolean(
        string="Uses Nairobi Water (NCWSC)",
        help="Tick if the building gets water from the NCWSC main line. Default: No.",
    )
    uses_borehole = fields.Boolean(
        string="Uses a borehole",
        help="Tick if the building has its own borehole. Default: No.",
    )
    water_category_id = fields.Many2one(
        "pm.water.category",
        string="Water category",
        help="How tenants are billed for water here: by the price tiers of this category (Domestic, Commercial, ...). Leave empty to share the real water cost over the units used instead. A single unit can have its own category.",
    )
    grace_days = fields.Integer(
        string="Grace days",
        default=5,
        tracking=True,
        help="How many days after the due date before an unpaid invoice counts as arrears. Default 5.",
    )
    leak_threshold_pct = fields.Integer(
        string="Water leak limit %",
        default=10,
        help="Water lost between the main meter and unit meters, up to this percent, is shared by everyone. Default 10.",
    )
    prorate_first_month = fields.Boolean(
        string="Charge first month by days",
        default=True,
        help="If a tenant moves in mid-month, charge only for the days they stay. Default: Yes.",
    )
    deposit_refund_days = fields.Integer(
        string="Deposit refund days",
        default=14,
        help="Days allowed to return a deposit after the tenant moves out. Default 14.",
    )
    invoice_lead_days = fields.Integer(
        string="Invoice days before rent day",
        default=5,
        help="How many days before rent day the invoice is created. Example: 5 means an invoice on the 27th for rent due on the 1st. Default 5.",
    )
    approval_threshold = fields.Float(
        string="Ask approval above (KES)",
        groups="property_management.group_pm_accountant",
        help="Repairs and expenses above this amount wait for approval. Example: 20,000. Leave at 0 for no approval needed.",
    )
    max_photo_mb = fields.Integer(
        string="Biggest photo (MB)",
        default=5,
        help="The biggest photo or file a tenant or staff member can upload, in megabytes. Photos are made smaller on the phone first. Default 5.",
    )
    payment_instructions = fields.Text(
        string="How tenants pay",
        help="Shown to tenants on their invoices page. Example: M-Pesa Paybill 123456, account number is your house number.",
    )
    generator_product_id = fields.Many2one(
        "product.product",
        string="Generator plan product",
        domain="[('pm_subscription', '=', True)]",
        help="The monthly plan tenants here can switch on. Its price is the monthly generator price. Left empty it uses the shared Generator plan product.",
    )
    generator_price = fields.Float(
        string="Generator price per month (KES)",
        compute="_compute_generator_price",
        inverse="_inverse_generator_price",
        help="What a tenant pays each month for the generator. Change it here. It applies to everyone on this plan from the next invoice.",
    )
    generator_ids = fields.One2many(
        "pm.generator.subscription",
        "building_id",
        string="Tenants and the generator plan",
        groups="property_management.group_pm_manager,property_management.group_pm_accountant",
    )
    generator_count = fields.Integer(string="Tenants on the plan", compute="_compute_generator_count")
    charge_line_ids = fields.Many2many(
        "pm.unit.type.charge",
        string="Fixed monthly charges",
        compute="_compute_charge_lines",
        groups="property_management.group_pm_manager,property_management.group_pm_accountant,property_management.group_pm_caretaker",
        help="The fixed items every unit pays each month, by unit type. Change them on the unit type.",
    )
    provider_ids = fields.One2many("pm.payment.provider", "building_id", string="Payment methods")
    help_contact_ids = fields.One2many("pm.help.contact", "building_id", string="Help contacts")
    active = fields.Boolean(default=True)

    @api.constrains("grace_days", "leak_threshold_pct", "deposit_refund_days", "invoice_lead_days", "max_photo_mb")
    def _check_not_negative(self):
        for building in self:
            values = (
                building.grace_days,
                building.leak_threshold_pct,
                building.deposit_refund_days,
                building.invoice_lead_days,
                building.max_photo_mb,
            )
            if min(values) < 0:
                raise ValidationError(
                    _("Days and percentages cannot be below zero. Please enter 0 or more.")
                )

    def _generator_product(self):
        """The building's own generator product, otherwise the shared Generator plan product."""
        self.ensure_one()
        return self.generator_product_id or self.env["pm.generator.subscription"]._default_product()

    @api.depends("generator_product_id.list_price")
    def _compute_generator_price(self):
        for building in self:
            building.generator_price = building._generator_product().sudo().list_price

    def _inverse_generator_price(self):
        for building in self:
            product = building._generator_product()
            if product:
                # The product may belong to a group the office user cannot edit, so the price is written with sudo.
                product.sudo().list_price = building.generator_price

    @api.model_create_multi
    def create(self, vals_list):
        default = self.env["pm.generator.subscription"]._default_product()
        for vals in vals_list:
            if default and not vals.get("generator_product_id"):
                vals["generator_product_id"] = default.id
        return super().create(vals_list)

    @api.model
    def _fill_generator_products(self):
        """Run after an upgrade: buildings made before this field existed get the shared Generator plan product."""
        default = self.env["pm.generator.subscription"]._default_product()
        if default:
            self.sudo().with_context(pm_fill_generator=True).search([("generator_product_id", "=", False)]).write(
                {"generator_product_id": default.id}
            )

    def _compute_generator_count(self):
        counts = {
            group["building_id"][0]: group["building_id_count"]
            for group in self.env["pm.generator.subscription"].sudo().read_group(
                [("building_id", "in", self.ids), ("on_plan", "=", True)], ["building_id"], ["building_id"]
            )
        }
        for building in self:
            building.generator_count = counts.get(building.id, 0)

    def _compute_charge_lines(self):
        for building in self:
            types = self.env["pm.unit"].search([("building_id", "=", building.id)]).mapped("unit_type_id")
            building.charge_line_ids = self.env["pm.unit.type.charge"].search([("unit_type_id", "in", types.ids)])

    def write(self, vals):
        result = super().write(vals)
        if vals.get("generator_product_id") and not self.env.context.get("pm_fill_generator"):
            # Tenants already on the plan move to the new product, so the next invoice uses it.
            self.env["pm.generator.subscription"].sudo().search([("building_id", "in", self.ids)]).write(
                {"product_id": vals["generator_product_id"]}
            )
        return result

