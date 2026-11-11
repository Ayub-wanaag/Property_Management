import base64

from psycopg2 import IntegrityError

from odoo import _, fields, http
from odoo.addons.pm_property.controllers.portal import TenantPortal
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

MAX_FILES = 3
MAX_BYTES = 5 * 1024 * 1024
UNPAID = ("not_paid", "partial")

# Word and colour together, never colour alone.
CLAIM_BADGES = {
    "submitted": ("Waiting to be checked", "text-bg-info"),
    "verified": ("Verified", "text-bg-success"),
    "rejected": ("Rejected", "text-bg-danger"),
}
REQUEST_BADGES = {
    "new": ("Waiting for a decision", "text-bg-info"),
    "approved": ("Approved", "text-bg-success"),
    "declined": ("Declined", "text-bg-danger"),
}


class TenantPortalBilling(TenantPortal):
    """Rent, proof of payment, more time and the generator plan for the signed-in tenant."""

    def _unpaid_invoices(self, lease):
        return request.env["account.move"].sudo().search(
            [
                ("pm_lease_id", "=", lease.id),
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("payment_state", "in", UNPAID),
            ],
            order="invoice_date_due, id",
        )

    def _home_values(self, lease, kw):
        values = super()._home_values(lease, kw)
        env = request.env
        subscriptions = env["pm.generator.subscription"].sudo()
        generator_product = subscriptions._default_product(lease.building_id)
        values.update(
            {
                "generator_product": generator_product,
                "generator": subscriptions.search([("lease_id", "=", lease.id)], limit=1) if generator_product else subscriptions,
                "gen_message": kw.get("gen"),
                "providers": lease.building_id.provider_ids.filtered(lambda p: p.type != "card"),
                "claims": env["pm.payment.claim"].sudo().search([("lease_id", "=", lease.id)], limit=5),
                "claim_badges": CLAIM_BADGES,
                "requests": env["pm.late.request"].sudo().search([("lease_id", "=", lease.id)], limit=3),
                "request_badges": REQUEST_BADGES,
            }
        )
        return values

    @http.route("/my/tenant/generator", type="http", auth="public", website=True, methods=["POST"], sitemap=False)
    def tenant_generator(self, **post):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        if not lease:
            return request.render("pm_property.tenant_no_lease", {})
        subscriptions = request.env["pm.generator.subscription"].sudo()
        product = subscriptions._default_product(lease.building_id)
        if not product:
            return request.redirect("/my/tenant")
        sub = subscriptions.search([("lease_id", "=", lease.id)], limit=1)
        if not sub:
            sub = subscriptions.create({"lease_id": lease.id, "on_plan": False, "product_id": product.id})
        wants_on = post.get("on") == "1"
        if sub.on_plan == wants_on:
            return request.redirect("/my/tenant")
        try:
            sub.action_set_plan(wants_on)
        except UserError:
            return request.redirect("/my/tenant?gen=locked")
        return request.redirect("/my/tenant?gen=saved")

    @http.route("/my/tenant/pay", type="http", auth="public", website=True, methods=["GET", "POST"], sitemap=False)
    def tenant_pay(self, **post):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        if not lease:
            return request.render("pm_property.tenant_no_lease", {})
        error = False
        if request.httprequest.method == "POST":
            error = self._submit_claim(lease, post)
            if not error:
                return request.render("pm_billing.tenant_pay_done", {})
        return request.render("pm_billing.tenant_pay_page", {"lease": lease, "error": error, "post": post})

    def _submit_claim(self, lease, post):
        """Returns an error message for the tenant, or None when the claim was saved."""
        try:
            amount = float((post.get("amount") or "").replace(",", "").strip())
        except ValueError:
            return _("Please type the amount as a number. Example: 18000.")
        if amount <= 0:
            return _("The amount must be more than zero.")
        if post.get("kind") not in ("rent", "service_charge"):
            return _("Please choose what you are paying for.")
        method = post.get("method") if post.get("method") in ("mpesa", "bank", "other") else "mpesa"
        text = (post.get("sms_text") or "").strip()
        files = [f for f in request.httprequest.files.getlist("proof") if f and f.filename]
        if not text and not files:
            return _("Please paste the M-Pesa message, or add a photo or PDF of your payment.")
        if len(files) > MAX_FILES:
            return _("You can add up to %s files.") % MAX_FILES
        uploads = []
        for upload in files:
            content = upload.read()
            if len(content) > MAX_BYTES:
                return _("A file is too big. Each file can be up to 5 MB.")
            if not (upload.mimetype.startswith("image/") or upload.mimetype == "application/pdf"):
                return _("Only photos and PDF files can be added.")
            uploads.append((upload.filename, content))
        Claim = request.env["pm.payment.claim"].sudo()
        try:
            with request.env.cr.savepoint():
                claim = Claim.create(
                    {
                        "lease_id": lease.id,
                        "kind": post["kind"],
                        "amount": amount,
                        "method": method,
                        "sms_text": text,
                    }
                )
        except ValidationError as error:
            return error.args[0]
        except IntegrityError:
            return _("This transaction code was already submitted. A code can only be used once.")
        for name, content in uploads:
            attachment = request.env["ir.attachment"].sudo().create(
                {
                    "name": name,
                    "datas": base64.b64encode(content),
                    "res_model": "pm.payment.claim",
                    "res_id": claim.id,
                }
            )
            claim.attachment_ids = [(4, attachment.id)]
        return None

    @http.route("/my/tenant/more-time", type="http", auth="public", website=True, methods=["GET", "POST"], sitemap=False)
    def tenant_more_time(self, **post):
        redirect, lease = self._guard()
        if redirect:
            return redirect
        if not lease:
            return request.render("pm_property.tenant_no_lease", {})
        waiting = request.env["pm.late.request"].sudo().search([("lease_id", "=", lease.id), ("state", "=", "new")])
        invoices = self._unpaid_invoices(lease).filtered(lambda move: move not in waiting.mapped("invoice_id"))
        error = False
        if request.httprequest.method == "POST":
            error = self._submit_request(lease, invoices, post)
            if not error:
                return request.render("pm_billing.tenant_more_time_done", {})
        return request.render(
            "pm_billing.tenant_more_time_page",
            {"lease": lease, "invoices": invoices, "error": error, "post": post, "currency": request.env.company.currency_id},
        )

    def _submit_request(self, lease, invoices, post):
        invoice = invoices.filtered(lambda move: str(move.id) == post.get("invoice_id"))
        if not invoice:
            return _("Please choose which invoice you need more time for.")
        try:
            proposed = fields.Date.to_date(post.get("proposed_date"))
        except ValueError:
            proposed = None
        if not proposed:
            return _("Please pick the date you can pay by.")
        reason = (post.get("reason") or "").strip()
        if not reason:
            return _("Please tell us why you need more time.")
        try:
            with request.env.cr.savepoint():
                request.env["pm.late.request"].sudo().create(
                    {"invoice_id": invoice.id, "lease_id": lease.id, "proposed_date": proposed, "reason": reason}
                )
        except ValidationError as error:
            return error.args[0]
        return None
