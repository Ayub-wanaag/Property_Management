from odoo import fields, models

AGENT_TYPES = [
    ("plumbing", "Plumbing"),
    ("electrical", "Electrical"),
    ("carpentry", "Carpentry"),
    ("other", "Other"),
]


class PmAgent(models.Model):
    """An outside worker or company the building pays or calls: a plumber, an electrician, G4S."""

    _name = "pm.agent"
    _description = "Agent"
    _order = "name, id"

    name = fields.Char(string="Name", required=True, help="Example: Joseph, or G4S.")
    agent_type = fields.Selection(
        AGENT_TYPES,
        string="Type",
        required=True,
        default="plumbing",
        help="The kind of work this agent does. Pick Other for anything not in the list, like security (G4S). Default: Plumbing.",
    )
    type_other = fields.Char(string="Kind of work", help="Only for Other. Example: Security.")
    phone = fields.Char(string="Phone", help="Example: 0712 345 678.")
    notes = fields.Text(string="Notes", help="Anything useful. Example: Works weekends only.")
    active = fields.Boolean(default=True, help="Untick (archive) to hide an agent you no longer use. Default: ticked.")
