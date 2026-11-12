from odoo import fields, models


class PmHelpContact(models.Model):
    _name = "pm.help.contact"
    _description = "Help Contact"
    _rec_name = "label"
    _order = "building_id, sequence, id"

    sequence = fields.Integer(default=10)
    building_id = fields.Many2one("pm.building", string="Building", required=True, ondelete="cascade")
    label = fields.Char(string="Who", required=True, help="Example: Caretaker.")
    phone = fields.Char(string="Phone", help="Example: 0712 345 678.")
    hours = fields.Char(string="Hours", help="Example: 8am to 6pm, every day.")
    when_to_call = fields.Char(string="When to call", help="Example: Water, power or door problems.")
