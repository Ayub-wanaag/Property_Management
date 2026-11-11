from odoo import http
from odoo.addons.web.controllers.session import Session
from odoo.http import request


class TenantAwareSession(Session):
    """A tenant who logs out goes back to the tenant sign-in page, not the page that asks Staff or Tenant."""

    @http.route("/web/session/logout", type="http", auth="none")
    def logout(self, redirect="/web"):
        uid = request.session.uid
        # The header's Logout link sends everyone to "/". Only portal users (tenants) are sent elsewhere.
        if uid and redirect in ("/", "") and request.env["res.users"].sudo().browse(uid).share:
            redirect = "/tenant/login"
        return super().logout(redirect=redirect)
