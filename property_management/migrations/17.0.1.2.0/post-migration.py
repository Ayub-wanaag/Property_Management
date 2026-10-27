from odoo import SUPERUSER_ID, api

from odoo.addons.property_management.hooks import apply_website_branding


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    # The daily rent-invoice job was loaded as 'noupdate', so Odoo would not remove it by itself.
    cron = env.ref("property_management.cron_generate_rent_invoices", raise_if_not_found=False)
    if cron:
        cron.unlink()
    apply_website_branding(env)
