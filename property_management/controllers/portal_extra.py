import base64

from odoo import _, fields, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

from .portal import TenantPortal

TICKET_BADGES = {
    "new": ("Received", "text-bg-info"),
    "assigned": ("Someone is assigned", "text-bg-info"),
    "in_progress": ("Being fixed", "text-bg-warning"),
    "waiting_approval": ("Waiting for approval", "text-bg-warning"),
    "done": ("Fixed", "text-bg-success"),
    "closed": ("Closed", "text-bg-secondary"),
}
MOVE_OUT_STEPS = {
    "requested": "Waiting for the office to acknowledge your request.",
    "acknowledged": "Acknowledged. Please upload a photo of every room.",
    "photos_uploaded": "Photos received. The office is checking them.",
    "reviewed": "Photos checked. The office is preparing your deposit refund.",
    "completed": "Moved out.",
}
MAX_TICKET_PHOTOS = 5


class TenantPortalExtra(TenantPortal):
    """Problems, documents, move-out and help for the signed-in tenant. Every page starts with the same guard."""

    # ----- helpers -----
    def _max_bytes(self, lease):
        return (lease.building_id.max_photo_mb or 5) * 1024 * 1024

    def _read_photos(self, uploads, max_bytes):
        """Return (list of (filename, bytes), error text). Images only."""
        photos = []
        for upload in uploads:
            if not upload or not upload.filename:
                continue
            content = upload.read()
            if len(content) > max_bytes:
                return [], _("The photo %(name)s is too big. Photos can be up to %(mb)s MB.", name=upload.filename, mb=max_bytes // (1024 * 1024))
            if not (upload.mimetype or "").startswith("image/"):
                return [], _("%s is not a photo. Please choose pictures only.") % upload.filename
            photos.append((upload.filename, content))
        return photos, None

    def _contacts(self, lease):
        return lease.building_id.help_contact_ids

    # ----- problems -----
    @http.route("/my/tenant/problems", type="http", auth="public", website=True, sitemap=False)
    def tenant_problems(self, **kw):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        if not lease:
            return request.render("property_management.tenant_no_lease", {})
        tickets = request.env["pm.ticket"].sudo().search([("lease_id", "=", lease.id)], order="id desc")
        return request.render(
            "property_management.tenant_problems_page",
            {"lease": lease, "tickets": tickets, "badges": TICKET_BADGES, "contacts": self._contacts(lease), "done": kw.get("done")},
        )

    @http.route("/my/tenant/problems/new", type="http", auth="public", website=True, methods=["GET", "POST"], sitemap=False)
    def tenant_problem_new(self, **post):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        if not lease:
            return request.render("property_management.tenant_no_lease", {})
        values = {"lease": lease, "contacts": self._contacts(lease), "post": post, "error": None, "max_mb": lease.building_id.max_photo_mb or 5}
        if request.httprequest.method == "POST":
            error = self._submit_problem(lease, post)
            if error is None:
                return request.redirect("/my/tenant/problems?done=%s" % ("urgent" if post.get("priority") == "urgent" else "1"))
            values["error"] = error
        return request.render("property_management.tenant_problem_new_page", values)

    def _submit_problem(self, lease, post):
        title = (post.get("name") or "").strip()
        if not title:
            return _("Please write what is wrong, in a few words.")
        priority = post.get("priority") if post.get("priority") in ("urgent", "normal", "low") else "normal"
        photos, error = self._read_photos(request.httprequest.files.getlist("photos")[:MAX_TICKET_PHOTOS + 1], self._max_bytes(lease))
        if error:
            return error
        if len(photos) > MAX_TICKET_PHOTOS:
            return _("Please send at most %s photos.") % MAX_TICKET_PHOTOS
        ticket = request.env["pm.ticket"].sudo().create(
            {
                "name": title,
                "lease_id": lease.id,
                "unit_id": lease.unit_id.id,
                "description": (post.get("description") or "").strip(),
                "priority": priority,
                "source": "tenant",
            }
        )
        for filename, content in photos:
            attachment = request.env["ir.attachment"].sudo().create(
                {"name": filename, "datas": base64.b64encode(content), "res_model": "pm.ticket", "res_id": ticket.id}
            )
            ticket.write({"attachment_ids": [(4, attachment.id)]})
        return None

    @http.route("/my/tenant/problems/<int:ticket_id>", type="http", auth="public", website=True, sitemap=False)
    def tenant_problem_detail(self, ticket_id, **kw):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        if not lease:
            return request.render("property_management.tenant_no_lease", {})
        ticket = request.env["pm.ticket"].sudo().search([("id", "=", ticket_id), ("lease_id", "=", lease.id)], limit=1)
        if not ticket:
            return request.redirect("/my/tenant/problems")
        return request.render(
            "property_management.tenant_problem_detail_page",
            {"lease": lease, "ticket": ticket, "badges": TICKET_BADGES, "contacts": self._contacts(lease)},
        )

    # ----- documents -----
    @http.route("/my/tenant/documents", type="http", auth="public", website=True, sitemap=False)
    def tenant_documents(self, **kw):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        if not lease:
            return request.render("property_management.tenant_no_lease", {})
        documents = request.env["pm.document"].sudo().search([("lease_id", "=", lease.id), ("visibility", "=", "tenant")])
        return request.render("property_management.tenant_documents_page", {"lease": lease, "documents": documents})

    @http.route("/my/tenant/documents/<int:document_id>/download", type="http", auth="public", website=True, sitemap=False)
    def tenant_document_download(self, document_id, **kw):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        document = request.env["pm.document"].sudo().search(
            [("id", "=", document_id), ("lease_id", "=", lease.id), ("visibility", "=", "tenant")], limit=1
        ) if lease else None
        if not document:
            return request.redirect("/my/tenant/documents")
        return request.env["ir.binary"]._get_stream_from(document, "file", "file_name").get_response(as_attachment=True)

    # ----- help -----
    @http.route("/my/tenant/help", type="http", auth="public", website=True, sitemap=False)
    def tenant_help(self, **kw):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        if not lease:
            return request.render("property_management.tenant_no_lease", {})
        return request.render("property_management.tenant_help_page", {"lease": lease, "contacts": self._contacts(lease)})

    # ----- move out -----
    def _move_out_request(self, lease):
        return request.env["pm.move.out.request"].sudo().search([("lease_id", "=", lease.id)], order="id desc", limit=1)

    @http.route("/my/tenant/move-out", type="http", auth="public", website=True, methods=["GET", "POST"], sitemap=False)
    def tenant_move_out(self, **post):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        if not lease:
            return request.render("property_management.tenant_no_lease", {})
        error = None
        if request.httprequest.method == "POST":
            error = self._submit_move_out(lease, post)
            if error is None:
                return request.redirect("/my/tenant/move-out?done=1")
        move_out = self._move_out_request(lease)
        deposit = request.env["pm.deposit"].sudo().search([("lease_id", "=", lease.id)], limit=1)
        return request.render(
            "property_management.tenant_move_out_page",
            {
                "lease": lease,
                "move_out": move_out,
                "steps": MOVE_OUT_STEPS,
                "deposit": deposit,
                "error": error,
                "post": post,
                "done": post.get("done"),
                "today": fields.Date.context_today(request.env["pm.lease"]),
            },
        )

    def _submit_move_out(self, lease, post):
        if self._move_out_request(lease):
            return _("You already asked to move out.")
        try:
            moving = fields.Date.to_date(post.get("requested_date"))
        except (ValueError, TypeError):
            moving = None
        if not moving:
            return _("Please choose the day you will move out.")
        if moving < fields.Date.context_today(request.env["pm.lease"]):
            return _("The move-out day cannot be in the past.")
        try:
            with request.env.cr.savepoint():
                request.env["pm.move.out.request"].sudo().create(
                    {"lease_id": lease.id, "requested_date": moving, "tenant_note": (post.get("note") or "").strip()}
                )
        except (ValidationError, UserError) as error:
            return error.args[0]
        return None

    @http.route("/my/tenant/move-out/photos", type="http", auth="public", website=True, methods=["GET", "POST"], sitemap=False)
    def tenant_move_out_photos(self, **post):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        if not lease:
            return request.render("property_management.tenant_no_lease", {})
        move_out = self._move_out_request(lease)
        if not move_out or move_out.state not in ("acknowledged", "photos_uploaded"):
            return request.redirect("/my/tenant/move-out")
        rooms = lease.unit_id.unit_type_id.room_ids
        error = None
        if request.httprequest.method == "POST" and move_out.state == "acknowledged":
            error = self._submit_move_out_photos(lease, move_out, rooms)
            if error is None:
                return request.redirect("/my/tenant/move-out?done=photos")
        return request.render(
            "property_management.tenant_move_out_photos_page",
            {"lease": lease, "move_out": move_out, "rooms": rooms, "error": error, "max_mb": lease.building_id.max_photo_mb or 5},
        )

    def _submit_move_out_photos(self, lease, move_out, rooms):
        max_bytes = self._max_bytes(lease)
        uploads, missing = [], []
        for room in rooms:
            photos, error = self._read_photos(request.httprequest.files.getlist("room_%s" % room.id), max_bytes)
            if error:
                return error
            if not photos:
                missing.append(room.name)
            uploads += [(room, name, content) for name, content in photos]
        if missing:
            return _("Please add at least one photo for: %s.") % ", ".join(missing)
        request.env["pm.move.out.request"]._tenant_upload_photos(move_out, uploads)
        return None

    @http.route("/my/tenant/deposit/dispute", type="http", auth="public", website=True, methods=["POST"], sitemap=False)
    def tenant_deposit_dispute(self, **post):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        if not lease:
            return request.render("property_management.tenant_no_lease", {})
        deposit = request.env["pm.deposit"].sudo().search([("lease_id", "=", lease.id)], limit=1)
        explanation = (post.get("explanation") or "").strip()
        if not deposit:
            return request.redirect("/my/tenant/move-out")
        if not explanation:
            return request.redirect("/my/tenant/move-out?done=dispute_empty")
        try:
            with request.env.cr.savepoint():
                deposit._tenant_dispute(explanation)
        except (ValidationError, UserError):
            return request.redirect("/my/tenant/move-out?done=dispute_failed")
        return request.redirect("/my/tenant/move-out?done=dispute")
