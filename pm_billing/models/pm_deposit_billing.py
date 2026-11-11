from odoo import _, models
from odoo.exceptions import UserError


class PmDepositBilling(models.Model):
    """What Billing adds to a deposit: take the last water bill off it."""

    _inherit = "pm.deposit"

    def action_add_final_water(self):
        """Take the water used since the last invoice off the deposit, at the building's latest water rate."""
        self.ensure_one()
        self._check_open()
        if self.deduction_ids.filtered(lambda d: d.reason == "final_water"):
            raise UserError(_("The final water bill is already on the list. Edit that line if the amount is wrong."))
        readings = self.env["pm.meter.reading"].search(
            [("unit_id", "=", self.unit_id.id), ("billed", "=", False)]
        )
        cycle = self.env["pm.water.cycle"].search(
            [("building_id", "=", self.building_id.id)], order="month desc", limit=1
        )
        category = self.unit_id._water_category()
        if not readings or (not cycle and not category):
            raise UserError(
                _(
                    "There is nothing to bill: either no new meter reading was entered for this unit, or no water costs were entered for the building. Enter the final reading first (Water > Enter meter readings)."
                )
            )
        units = sum(readings.mapped("units"))
        if category:
            category._check_ready()
            water, sewerage = category._charge(units)
            description = _("Water and sewerage: %(units)s units (%(category)s)") % {
                "units": units,
                "category": category.name,
            }
            amount = water + sewerage
        else:
            cycle.invalidate_recordset()
            description = _("Water: %(units)s units at %(rate)s") % {"units": units, "rate": cycle.rate}
            amount = units * cycle.rate
        self.env["pm.deposit.deduction"].create(
            {
                "deposit_id": self.id,
                "reason": "final_water",
                "description": description,
                "amount": max(amount, 0.0),
            }
        )
        return True
