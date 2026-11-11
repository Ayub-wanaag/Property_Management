from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSetupGuide(TransactionCase):
    def test_menu_opens_the_unfinished_guide_then_a_new_one(self):
        Setup = self.env["pm.setup"]
        Setup.search([]).unlink()
        first = Setup.browse(Setup.action_open_setup()["res_id"])
        again = Setup.browse(Setup.action_open_setup()["res_id"])
        self.assertEqual(first, again)  # still in progress: continue it
        first.write({"state": "done"})
        fresh = Setup.browse(Setup.action_open_setup()["res_id"])
        self.assertNotEqual(first, fresh)  # finished: opening again starts a new one
        self.assertEqual(fresh.state, "draft")

    def test_start_new_cancels_the_unfinished_guide(self):
        Setup = self.env["pm.setup"]
        current = Setup.create({})
        new = Setup.browse(current.action_start_new()["res_id"])
        self.assertEqual(current.state, "cancelled")
        self.assertEqual(new.state, "draft")

    def test_finished_guide_can_start_a_new_one(self):
        Setup = self.env["pm.setup"]
        done = Setup.create({"state": "done"})
        new = Setup.browse(done.action_start_new()["res_id"])
        self.assertEqual(done.state, "done")  # history kept
        self.assertEqual(new.state, "draft")

    def test_each_guide_makes_its_own_building(self):
        Setup = self.env["pm.setup"]
        owner = self.env["pm.owner"].search([("is_default", "=", True)], limit=1)
        buildings = self.env["pm.building"]
        for name in ("Guide One", "Guide Two"):
            guide = Setup.create({"building_name": name, "owner_id": owner.id})
            guide.action_next()
            buildings |= guide.building_id
        self.assertEqual(buildings.mapped("name"), ["Guide One", "Guide Two"])

    def test_app_opens_on_today(self):
        self.assertFalse(self.env.ref("property_management.menu_pm_home", raise_if_not_found=False))
        root = self.env.ref("property_management.menu_pm_root")
        first = root.child_id.sorted(lambda m: (m.sequence, m.id))[:1]
        self.assertEqual(first.name, "Today")
