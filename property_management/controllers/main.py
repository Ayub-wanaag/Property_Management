from odoo import _, http
from odoo.exceptions import AccessDenied
from odoo.http import request


class TenantLogin(http.Controller):
    @http.route("/tenant/login", type="http", auth="public", website=True, sitemap=False)
    def tenant_login(self, **post):
        values = {
            "error": False,
            "tenant_name": post.get("tenant_name", ""),
            "building": post.get("building", ""),
        }
        if request.httprequest.method == "POST":
            login = request.env["pm.lease"].sudo()._tenant_login_for(
                post.get("tenant_name"), post.get("building")
            )
            if login and self._sign_in(login, post.get("password") or ""):
                return request.redirect("/my/tenant")
            values["error"] = _("Your name, building or password is not right. Please check them and try again.")
        return request.render("property_management.tenant_login_page", values)

    def _sign_in(self, login, password):
        try:
            try:
                request.session.authenticate(request.db, login, password)
            except TypeError:
                request.session.authenticate(
                    request.db, {"login": login, "password": password, "type": "password"}
                )
        except AccessDenied:
            return False
        return True
