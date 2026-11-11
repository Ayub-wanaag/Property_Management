from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAgent(TransactionCase):
    def test_a_new_agent_is_plumbing_until_you_pick_another_type(self):
        agent = self.env["pm.agent"].create({"name": "Joseph"})
        self.assertEqual(agent.agent_type, "plumbing")
        self.assertTrue(agent.active)

    def test_other_agent_keeps_what_kind_of_work_it_does(self):
        agent = self.env["pm.agent"].create({"name": "G4S", "agent_type": "other", "type_other": "Security"})
        self.assertEqual(agent.agent_type, "other")
        self.assertEqual(agent.type_other, "Security")

    def test_an_archived_agent_is_hidden_from_the_list(self):
        agent = self.env["pm.agent"].create({"name": "Old electrician", "agent_type": "electrical"})
        agent.active = False
        self.assertNotIn(agent, self.env["pm.agent"].search([]))
        self.assertIn(agent, self.env["pm.agent"].with_context(active_test=False).search([]))
