import re

from odoo.tests import TransactionCase, tagged

TECHNICAL = re.compile(r"^pm\.[a-z_.]+,\d+$")


@tagged("post_install", "-at_install")
class TestDisplayNames(TransactionCase):
    """No screen should ever show a technical name such as pm.today,12."""

    def test_no_record_shows_its_technical_name(self):
        for model_name, model in self.env.registry.items():
            if not model_name.startswith("pm.") or model._abstract:
                continue
            records = self.env[model_name].search([], limit=5)
            if model._transient and not records:
                continue
            for record in records:
                self.assertFalse(
                    TECHNICAL.match(record.display_name or ""),
                    "%s shows a technical name: %s" % (model_name, record.display_name),
                )

    def test_today_page_has_a_friendly_name(self):
        card = self.env["pm.today"].create({})
        self.assertEqual(card.display_name, "Today's overview")
        self.assertEqual(card.action_open_today()["name"], "Today's overview")

    def test_wizards_have_friendly_names(self):
        self.assertEqual(self.env["pm.payment.wizard"].new({}).display_name, "Record a payment")
