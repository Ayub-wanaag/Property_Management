import base64
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class PmInspection(models.Model):
    """Room-by-room photos of a unit: the baseline at move-in, the comparison at move-out."""

    _name = "pm.inspection"
    _description = "Inspection"
    _inherit = ["mail.thread"]
    _order = "id desc"

    lease_id = fields.Many2one("pm.lease", string="Lease", required=True, ondelete="restrict", tracking=True)
    unit_id = fields.Many2one(related="lease_id.unit_id", store=True, string="Unit")
    building_id = fields.Many2one(related="lease_id.building_id", store=True, string="Building")
    tenant_id = fields.Many2one(related="lease_id.tenant_id", store=True, string="Tenant")
    unit_type_id = fields.Many2one(related="lease_id.unit_id.unit_type_id", string="Unit type")
    kind = fields.Selection(
        [("move_in", "Move-in"), ("move_out", "Move-out")], string="Type", required=True, default="move_in"
    )
    state = fields.Selection(
        [("draft", "Taking photos"), ("done", "Finished")], string="Status", default="draft", required=True, tracking=True, copy=False
    )
    date = fields.Date(string="Date", default=fields.Date.context_today)
    photo_ids = fields.One2many("pm.inspection.photo", "inspection_id", string="Photos")
    missing_rooms = fields.Char(string="Rooms with no photo yet", compute="_compute_missing_rooms")
    notes = fields.Text(string="Notes", help="Anything worth remembering. Example: Small crack in the bathroom tile already there.")

    _sql_constraints = [
        ("lease_kind_uniq", "unique(lease_id, kind)", "This lease already has an inspection of that type. Open it instead."),
    ]

    @api.depends("photo_ids.room_name", "unit_type_id.room_ids")
    def _compute_missing_rooms(self):
        for inspection in self:
            rooms = inspection.unit_type_id.room_ids.mapped("name")
            taken = set(inspection.photo_ids.mapped("room_name"))
            inspection.missing_rooms = ", ".join(room for room in rooms if room not in taken)

    @api.depends("lease_id.name", "kind")
    def _compute_display_name(self):
        for inspection in self:
            kind = dict(inspection._fields["kind"].selection).get(inspection.kind, "")
            inspection.display_name = "%s: %s" % (kind, inspection.lease_id.name or "")

    def action_done(self):
        for inspection in self:
            if inspection.state == "done":
                raise UserError(_("This inspection is already finished."))
            if inspection.missing_rooms:
                raise UserError(
                    _("Every room needs at least one photo. Still missing: %s.") % inspection.missing_rooms
                )
            inspection.state = "done"
        return True

    def action_reopen(self):
        self.write({"state": "draft"})
        return True

    @api.model
    def _make_for_lease(self, lease, kind):
        """The inspection of this kind for the lease, made if it does not exist yet."""
        found = self.sudo().search([("lease_id", "=", lease.id), ("kind", "=", kind)], limit=1)
        if found:
            return found
        lease.unit_id.unit_type_id.sudo()._ensure_rooms()
        return self.sudo().create({"lease_id": lease.id, "kind": kind})


class PmInspectionPhoto(models.Model):
    _name = "pm.inspection.photo"
    _description = "Inspection Photo"
    _order = "inspection_id, room_id, id"

    inspection_id = fields.Many2one("pm.inspection", string="Inspection", required=True, ondelete="cascade")
    unit_type_id = fields.Many2one(related="inspection_id.unit_type_id")
    room_id = fields.Many2one(
        "pm.unit.type.room",
        string="Room",
        required=True,
        help="Which room the photo shows.",
    )
    room_name = fields.Char(string="Room name", related="room_id.name", store=True)
    image = fields.Binary(string="Photo", attachment=True, required=True)
    note = fields.Char(string="Note", help="Example: Scratch on the door.")

    @api.constrains("image")
    def _check_size(self):
        for photo in self:
            limit = (photo.inspection_id.building_id.max_photo_mb or 5) * 1024 * 1024
            if photo.image and len(photo.image) * 3 // 4 > limit:
                raise ValidationError(
                    _("This photo is too big. Photos can be up to %s MB. Please take a smaller photo.")
                    % (photo.inspection_id.building_id.max_photo_mb or 5)
                )

    @api.depends("room_name", "note")
    def _compute_display_name(self):
        for photo in self:
            photo.display_name = "%s %s" % (photo.room_name or "", photo.note or "")

    def write(self, vals):
        if any(photo.inspection_id.state == "done" for photo in self):
            raise UserError(_("This inspection is finished. Re-open it first to change the photos."))
        return super().write(vals)

    def unlink(self):
        if any(photo.inspection_id.state == "done" for photo in self):
            raise UserError(_("This inspection is finished. Re-open it first to remove photos."))
        return super().unlink()


class PmMoveOutRequest(models.Model):
    """A tenant asks to move out. Flow: requested, acknowledged, photos uploaded, reviewed, completed."""

    _name = "pm.move.out.request"
    _description = "Move-out Request"
    _inherit = ["mail.thread"]
    _order = "id desc"

    lease_id = fields.Many2one("pm.lease", string="Lease", required=True, ondelete="restrict", tracking=True)
    unit_id = fields.Many2one(related="lease_id.unit_id", store=True, string="Unit")
    building_id = fields.Many2one(related="lease_id.building_id", store=True, string="Building")
    tenant_id = fields.Many2one(related="lease_id.tenant_id", store=True, string="Tenant")
    requested_date = fields.Date(
        string="Moving out on", required=True, tracking=True, help="The day the tenant wants to leave. Example: 31/12/2026."
    )
    short_notice = fields.Boolean(
        string="Less notice than the lease asks", compute="_compute_short_notice", help="Ticked if the date is sooner than the lease's notice days."
    )
    state = fields.Selection(
        [
            ("requested", "Waiting to be acknowledged"),
            ("acknowledged", "Waiting for the tenant's photos"),
            ("photos_uploaded", "Photos in, to be reviewed"),
            ("reviewed", "Reviewed"),
            ("completed", "Moved out"),
        ],
        string="Status",
        default="requested",
        required=True,
        tracking=True,
        copy=False,
    )
    tenant_note = fields.Text(string="Tenant's note")
    inspection_id = fields.Many2one("pm.inspection", string="Move-out photos", readonly=True, copy=False)
    move_in_inspection_id = fields.Many2one(
        "pm.inspection", string="Move-in photos", compute="_compute_move_in", readonly=True
    )
    deposit_id = fields.Many2one("pm.deposit", string="Deposit", compute="_compute_deposit")
    comparison_html = fields.Html(string="Move-in and move-out photos", compute="_compute_comparison", sanitize=False)

    @api.depends("requested_date", "create_date", "lease_id.notice_days")
    def _compute_short_notice(self):
        for request in self:
            created = (request.create_date or fields.Datetime.now()).date()
            earliest = created + timedelta(days=request.lease_id.notice_days or 0)
            request.short_notice = bool(request.requested_date) and request.requested_date < earliest

    @api.depends("lease_id")
    def _compute_move_in(self):
        for request in self:
            request.move_in_inspection_id = self.env["pm.inspection"].search(
                [("lease_id", "=", request.lease_id.id), ("kind", "=", "move_in")], limit=1
            )

    @api.depends("lease_id")
    def _compute_deposit(self):
        for request in self:
            request.deposit_id = self.env["pm.deposit"].search([("lease_id", "=", request.lease_id.id)], limit=1)

    @api.depends("inspection_id.photo_ids", "move_in_inspection_id.photo_ids", "lease_id")
    def _compute_comparison(self):
        for request in self:
            rooms = request.lease_id.unit_id.unit_type_id.room_ids
            rows = []
            for room in rooms:
                cells = []
                for inspection in (request.move_in_inspection_id, request.inspection_id):
                    photos = inspection.photo_ids.filtered(lambda p, r=room: p.room_id == r)
                    images = Markup("").join(
                        Markup('<a href="/web/image/pm.inspection.photo/%s/image" target="_blank">'
                               '<img src="/web/image/pm.inspection.photo/%s/image/240x180" '
                               'style="max-width:240px;margin:2px;border:1px solid #ccc"/></a>')
                        % (photo.id, photo.id)
                        for photo in photos
                    )
                    cells.append(images or Markup('<em class="text-muted">No photo</em>'))
                rows.append(
                    Markup("<tr><th style='vertical-align:top;padding:4px'>%s</th>"
                           "<td style='vertical-align:top;padding:4px'>%s</td>"
                           "<td style='vertical-align:top;padding:4px'>%s</td></tr>")
                    % (escape(room.name), cells[0], cells[1])
                )
            request.comparison_html = (
                Markup("<table class='table table-sm'><thead><tr><th>Room</th><th>Move-in</th><th>Move-out</th></tr></thead><tbody>")
                + Markup("").join(rows)
                + Markup("</tbody></table>")
                if rows
                else Markup("<em>This unit type has no rooms yet.</em>")
            )

    @api.depends("lease_id.name")
    def _compute_display_name(self):
        for request in self:
            request.display_name = _("Move-out: %s") % (request.lease_id.name or "")

    @api.constrains("lease_id", "state")
    def _check_one_open(self):
        for request in self.filtered(lambda r: r.state != "completed"):
            other = self.search(
                [("lease_id", "=", request.lease_id.id), ("state", "!=", "completed"), ("id", "!=", request.id)], limit=1
            )
            if other:
                raise ValidationError(_("This lease already has a move-out request in progress."))

    @api.model_create_multi
    def create(self, vals_list):
        requests = super().create(vals_list)
        for request in requests:
            lease = request.lease_id.sudo()
            if lease.state == "active":
                lease.action_give_notice()  # the unit now shows 'Notice given'
        return requests

    # ----- the steps -----
    def action_acknowledge(self):
        for request in self:
            if request.state != "requested":
                raise UserError(_("This request was already acknowledged."))
            request.state = "acknowledged"
            request.message_post(body=_("Acknowledged. The tenant can now upload photos of every room."))
        return True

    def action_review(self):
        for request in self:
            if request.state != "photos_uploaded":
                raise UserError(_("The tenant has not uploaded photos yet."))
            request.inspection_id.action_done()
            request.state = "reviewed"
        return True

    def action_open_deposit(self):
        self.ensure_one()
        if not self.deposit_id:
            raise UserError(_("This lease has no deposit on file."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Deposit"),
            "res_model": "pm.deposit",
            "res_id": self.deposit_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
        }

    def action_complete(self):
        """Finish the move-out: end the lease (unit becomes vacant, tenant login off). The refund must be prepared first."""
        for request in self:
            if request.state != "reviewed":
                raise UserError(_("Review the photos first, then complete the move-out."))
            deposit = request.deposit_id.sudo()
            if deposit.state == "held":
                raise UserError(
                    _(
                        "Prepare the deposit refund first: open the deposit, add the deductions, then press 'Prepare the refund'. The tenant can then see the deductions and dispute them."
                    )
                )
            if deposit.state == "disputed":
                raise UserError(_("The tenant disputed a deduction. Decide on the dispute first (Moving in and out, then Deposit disputes)."))
            lease = request.lease_id.sudo()
            if lease.state in ("active", "notice"):
                lease.action_end()
            request.state = "completed"
        return True

    @api.model
    def _tenant_upload_photos(self, request, uploads):
        """uploads: list of (room, filename, bytes). Used by the tenant portal. Replaces any earlier upload."""
        inspection = self.env["pm.inspection"]._make_for_lease(request.lease_id, "move_out")
        inspection.photo_ids.unlink()
        for room, _name, content in uploads:
            self.env["pm.inspection.photo"].sudo().create(
                {"inspection_id": inspection.id, "room_id": room.id, "image": base64.b64encode(content)}
            )
        request.sudo().write({"inspection_id": inspection.id, "state": "photos_uploaded"})
        request.sudo().message_post(body=_("The tenant uploaded the move-out photos."))
