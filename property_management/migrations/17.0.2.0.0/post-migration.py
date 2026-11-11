from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Unit types made before 2.0 have no room list. Give each the guessed one, so inspections work."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["pm.unit.type"].with_context(active_test=False).search([])._ensure_rooms()
