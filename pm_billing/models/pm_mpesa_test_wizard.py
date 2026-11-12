from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .pm_mpesa import normalize_phone


class PmMpesaTestWizard(models.TransientModel):
    """A small pop-up that sends a real KES 1 M-Pesa prompt, so you can see STK Push working."""

    _name = "pm.mpesa.test.wizard"
    _description = "Send a test M-Pesa prompt"

    @api.depends_context("uid")
    def _compute_display_name(self):
        for wizard in self:
            wizard.display_name = _("Send a test prompt")

    phone = fields.Char(
        string="Phone number to test",
        required=True,
        help="A Safaricom number, for example 0712345678. It is used once and never saved.",
    )

    def action_send(self):
        self.ensure_one()
        phone = normalize_phone(self.phone)
        if not phone:
            raise UserError(_("Type a Safaricom number like 0712345678."))
        api_model = self.env["pm.mpesa.api"]
        creds = api_model._credentials()  # the details saved in Billing settings
        api_model._check_credentials(creds)
        data = api_model.stk_push(creds, phone, 1, "TEST", _("Test"))
        message = _("Safaricom accepted the request for KES 1 to %(phone)s. %(note)s") % {
            "phone": phone,
            "note": data.get("CustomerMessage") or "",
        }
        if creds["env"] == "sandbox":
            message += " " + _("In the sandbox the prompt shows in the Daraja simulator, not on a real phone.")
        else:
            message += " " + _("Check the phone and enter the PIN. This is real money (KES 1).")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("M-Pesa test prompt sent"),
                "message": message,
                "type": "success",
                "sticky": True,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
