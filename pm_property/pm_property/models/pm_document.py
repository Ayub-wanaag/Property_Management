from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

MAX_DOC_MB = 10

DOC_CATEGORIES = [
    ("lease", "Lease agreement"),
    ("id", "ID or contract copy"),
    ("receipt", "Receipt"),
    ("inspection", "Inspection report"),
    ("building", "Building paper (permit, insurance)"),
    ("other", "Other"),
]


class PmDocument(models.Model):
    """The depository: one place for the papers of a building or a tenant."""

    _name = "pm.document"
    _description = "Document"
    _inherit = ["mail.thread"]
    _order = "id desc"

    name = fields.Char(string="Name", required=True, help="Example: Lease agreement A12.")
    category = fields.Selection(DOC_CATEGORIES, string="Kind", default="other", required=True)
    file = fields.Binary(string="File", attachment=True, required=True)
    file_name = fields.Char(string="File name")
    building_id = fields.Many2one(
        "pm.building",
        string="Building",
        required=True,
        default=lambda self: self.env["pm.unit"]._default_building(),
        help="The building this paper belongs to. Default: your only building.",
    )
    lease_id = fields.Many2one(
        "pm.lease", string="Lease", domain="[('building_id', '=', building_id)]", help="Fill this if the paper belongs to one tenant."
    )
    tenant_id = fields.Many2one(related="lease_id.tenant_id", store=True, string="Tenant")
    visibility = fields.Selection(
        [
            ("managers", "Managers only"),
            ("staff", "All staff"),
            ("tenant", "All staff and the tenant of the lease"),
        ],
        string="Who can see it",
        default="staff",
        required=True,
        help="Managers only: private. All staff: caretakers and accountants too. The tenant option needs a lease. Default: All staff.",
    )
    visible_to_owner = fields.Boolean(
        string="The owner can see it", help="Tick if the owner of the building may read it. Default: No."
    )
    active = fields.Boolean(default=True)

    @api.constrains("visibility", "lease_id")
    def _check_tenant_visibility(self):
        for document in self:
            if document.visibility == "tenant" and not document.lease_id:
                raise ValidationError(_("To show a paper to a tenant, pick the tenant's lease first."))

    @api.constrains("file")
    def _check_size(self):
        for document in self:
            if document.file and len(document.file) * 3 // 4 > MAX_DOC_MB * 1024 * 1024:
                raise ValidationError(_("This file is too big. Files can be up to %s MB.") % MAX_DOC_MB)


class PmHelpGuide(models.Model):
    """The short step-by-step guides in the Help menu."""

    _name = "pm.help.guide"
    _description = "Help Guide"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    name = fields.Char(string="Job", required=True, help="Example: Record a payment.")
    summary = fields.Char(string="In one line", help="What the guide helps you do.")
    steps = fields.Html(string="Steps", sanitize=False)
