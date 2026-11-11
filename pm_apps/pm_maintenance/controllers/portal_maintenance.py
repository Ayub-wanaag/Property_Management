import base64

from odoo import _, http
from odoo.addons.pm_property.controllers.portal import TenantPortal
from odoo.http import request

TICKET_BADGES = {
    "new": ("Received", "text-bg-info"),
    "assigned": ("Someone is assigned", "text-bg-info"),
    "in_progress": ("Being fixed", "text-bg-warning"),
    "waiting_approval": ("Waiting for approval", "text-bg-warning"),
    "done": ("Fixed", "text-bg-success"),
    "closed": ("Closed", "text-bg-secondary"),
}
MAX_TICKET_PHOTOS = 5


class TenantPortalMaintenance(TenantPortal):
    """Problems the signed-in tenant reports and follows."""

    @http.route("/my/tenant/problems", type="http", auth="public", website=True, sitemap=False)
    def tenant_problems(self, **kw):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        if not lease:
            return request.render("pm_property.tenant_no_lease", {})
        tickets = request.env["pm.ticket"].sudo().search([("lease_id", "=", lease.id)], order="id desc")
        return request.render(
            "pm_maintenance.tenant_problems_page",
            {"lease": lease, "tickets": tickets, "badges": TICKET_BADGES, "contacts": self._contacts(lease), "done": kw.get("done")},
        )

    @http.route("/my/tenant/problems/new", type="http", auth="public", website=True, methods=["GET", "POST"], sitemap=False)
    def tenant_problem_new(self, **post):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        if not lease:
            return request.render("pm_property.tenant_no_lease", {})
        values = {"lease": lease, "contacts": self._contacts(lease), "post": post, "error": None, "max_mb": lease.building_id.max_photo_mb or 5}
        if request.httprequest.method == "POST":
            error = self._submit_problem(lease, post)
            if error is None:
                return request.redirect("/my/tenant/problems?done=%s" % ("urgent" if post.get("priority") == "urgent" else "1"))
            values["error"] = error
        return request.render("pm_maintenance.tenant_problem_new_page", values)

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
            return request.render("pm_property.tenant_no_lease", {})
        ticket = request.env["pm.ticket"].sudo().search([("id", "=", ticket_id), ("lease_id", "=", lease.id)], limit=1)
        if not ticket:
            return request.redirect("/my/tenant/problems")
        return request.render(
            "pm_maintenance.tenant_problem_detail_page",
            {"lease": lease, "ticket": ticket, "badges": TICKET_BADGES, "contacts": self._contacts(lease)},
        )
