from odoo import SUPERUSER_ID, api

from odoo.addons.pm_property.hooks import apply_website_branding


def migrate(cr, version):
    """The teal homepage was only applied on a first install. Apply it on upgrade too, so
    databases that already had the module installed also get it (instead of the plain white page)."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    apply_website_branding(env)
