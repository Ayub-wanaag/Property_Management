import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class MpesaCallback(http.Controller):
    """Safaricom calls this address after the tenant answers the prompt on their phone."""

    @http.route("/pm_billing/stk/callback/<string:secret>", type="http", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def stk_callback(self, secret, **kw):
        expected = request.env["pm.mpesa.api"].sudo()._callback_secret()
        if not hmac.compare_digest(secret, expected):
            return request.make_json_response({"ResultCode": 1, "ResultDesc": "Rejected"}, status=403)
        try:
            payload = json.loads(request.httprequest.data or b"{}")
        except ValueError:
            return request.make_json_response({"ResultCode": 1, "ResultDesc": "Unreadable"}, status=400)
        try:
            request.env["pm.mpesa.transaction"].sudo().handle_callback(payload)
        except Exception:  # never let Safaricom see an error page; we keep the payload in the log to sort out by hand
            _logger.exception("M-Pesa callback could not be processed: %s", payload)
        return request.make_json_response({"ResultCode": 0, "ResultDesc": "Accepted"})
