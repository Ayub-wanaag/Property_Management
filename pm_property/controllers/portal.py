from odoo import _, http
from odoo.http import request


class TenantPortal(http.Controller):
    """The signed-in tenant's own pages. Other apps add their pages by extending this class."""

    def _lease(self):
        """The signed-in tenant's own lease. Nobody else's."""
        return (
            request.env["pm.lease"]
            .sudo()
            .search(
                [("tenant_user_id", "=", request.env.user.id), ("state", "in", ("active", "notice"))],
                limit=1,
            )
        )

    def _guard(self):
        """Returns (redirect, lease). Signed-out visitors go to the tenant sign-in page."""
        if request.env.user._is_public():
            return request.redirect("/tenant/login"), None
        return None, self._lease()

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

    # ----- home -----
    @http.route("/my/tenant", type="http", auth="public", website=True, sitemap=False)
    def tenant_home(self, **kw):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        if not lease:
            return request.render("pm_property.tenant_no_lease", {})
        return request.render("pm_property.tenant_home_page", self._home_values(lease, kw))

    def _home_values(self, lease, kw):
        """What the home page shows. Billing and Maintenance add their own values by extending this."""
        return {"lease": lease, "currency": request.env.company.currency_id}
