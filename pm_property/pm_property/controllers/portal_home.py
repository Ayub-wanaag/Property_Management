from odoo import http
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.http import request


class TenantAwareHome(CustomerPortal):
    """A tenant has one page that matters, so /my goes straight to "My house" instead of a page of cards."""

    @http.route()
    def home(self, **kw):
        user = request.env.user
        if not user._is_public() and user.share:
            lease = (
                request.env["pm.lease"]
                .sudo()
                .search([("tenant_user_id", "=", user.id), ("state", "in", ("active", "notice"))], limit=1)
            )
            if lease:
                return request.redirect("/my/tenant")
        return super().home(**kw)
