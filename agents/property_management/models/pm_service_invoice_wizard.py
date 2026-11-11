from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

OCCUPYING = ("active", "notice")


class PmServiceInvoiceWizard(models.TransientModel):
    """Make the month's service charge invoices for a whole building, as drafts to look over and post.

    Each invoice has three kinds of lines, kept apart:
      the fixed items for the unit type, the water line (from the meter readings), and the generator line.
    """

    _name = "pm.service.invoice.wizard"
    _description = "Create Service Charge Invoices"

    building_id = fields.Many2one(
        "pm.building",
        string="Building",
        required=True,
        default=lambda self: self.env["pm.unit"]._default_building(),
        help="The building to invoice. Default: your only building.",
    )
    month = fields.Date(
        string="Month to bill",
        required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1),
        help="Pick any day of the month. Example: 05/10/2026 bills October 2026. Default: this month.",
    )
    state = fields.Selection([("draft", "Draft"), ("done", "Done")], default="draft")
    summary = fields.Text(string="What happened", readonly=True)
    invoice_ids = fields.Many2many("account.move", string="Invoices made", readonly=True)

    # ----- the lines -----
    @api.model
    def _tax_free(self, vals):
        vals["tax_ids"] = [(6, 0, [])]
        vals.setdefault("quantity", 1)
        return vals

    def _fixed_lines(self, lease):
        return [
            self._tax_free({"product_id": charge.product_id.id, "name": charge.product_id.name, "price_unit": charge.amount})
            for charge in lease.unit_id.unit_type_id.charge_ids
        ]

    def _water_lines(self, lease, cycle, month_end):
        category = lease.unit_id._water_category()
        if not cycle and not category:
            return []
        product = self.env["product.product"].sudo().search([("pm_is_water", "=", True)], limit=1)
        if not product:
            raise UserError(_("There is no water product yet. Tick 'Water charge' on a product first."))
        readings = self.env["pm.meter.reading"].search(
            [("unit_id", "=", lease.unit_id.id), ("billed", "=", False), ("date", "<=", month_end)], order="date, id"
        )
        lines = []
        for reading in readings:
            note = ""
            if reading.kind == "estimated":
                note = _(" (estimated, corrected at the next real reading)")
            elif reading.previous != reading.last_real:
                note = _(" (includes the correction of an earlier estimate)")
            if category:
                category._check_ready()
                water, sewerage = category._charge(reading.units)
                price = (water + sewerage) / reading.units if reading.units else 0.0
                label = _("Water and sewerage, meter %(meter)s (%(category)s)") % {
                    "meter": reading.meter_id.serial,
                    "category": category.name,
                }
            else:
                price = cycle.rate
                label = _("Water, meter %s") % reading.meter_id.serial
            lines.append(
                self._tax_free(
                    {
                        "product_id": product.id,
                        "name": label + note,
                        "quantity": reading.units,
                        "price_unit": price,
                        "pm_reading_id": reading.id,
                        "pm_meter_serial": reading.meter_id.serial,
                        "pm_prev_reading": reading.previous,
                        "pm_curr_reading": reading.current,
                        "pm_is_estimated": reading.kind == "estimated",
                    }
                )
            )
        return lines

    def _generator_lines(self, lease, start):
        subs = self.env["pm.generator.subscription"].sudo().search([("lease_id", "=", lease.id), ("on_plan", "=", True)])
        return [sub._invoice_line_vals(start) for sub in subs]

    # ----- the button -----
    def action_create(self):
        self.ensure_one()
        building = self.building_id
        month = self.month.replace(day=1)
        month_end = month + relativedelta(months=1, days=-1)
        has_water = building.uses_ncwsc or building.uses_borehole
        cycle = self.env["pm.water.cycle"].search([("building_id", "=", building.id), ("month", "=", month)], limit=1)
        if has_water and not cycle and not building.water_category_id:
            raise UserError(
                _(
                    "Type this month's water costs first (Water > Water costs). The water line needs the rate for %s.",
                    month.strftime("%m/%Y"),
                )
            )
        if cycle:
            cycle.invalidate_recordset()
        if not has_water:
            cycle = self.env["pm.water.cycle"]  # no water source: no water line at all
        leases = self.env["pm.lease"].search([("building_id", "=", building.id), ("state", "in", OCCUPYING)])
        made, skipped = self.env["account.move"], []
        for lease in leases.sorted(key=lambda rec: rec.unit_id.id):
            start = month.replace(day=lease.rent_due_day)
            existing = self.env["account.move"].search(
                [
                    ("pm_lease_id", "=", lease.id),
                    ("move_type", "=", "out_invoice"),
                    ("pm_kind", "=", "service_charge"),
                    ("pm_period_start", "=", start),
                    ("state", "!=", "cancel"),
                ],
                limit=1,
            )
            if existing:
                skipped.append(_("%s: already invoiced") % lease.unit_id.number)
                continue
            lines = self._fixed_lines(lease) + self._water_lines(lease, cycle, month_end) + self._generator_lines(lease, start)
            if not lines:
                skipped.append(_("%s: nothing to bill") % lease.unit_id.number)
                continue
            made |= self.env["account.move"].create(
                {
                    "move_type": "out_invoice",
                    "partner_id": lease.tenant_id.id,
                    "pm_lease_id": lease.id,
                    "pm_kind": "service_charge",
                    "pm_period_start": start,
                    "pm_period_end": start + relativedelta(months=1, days=-1),
                    "invoice_date": fields.Date.context_today(self),
                    "invoice_date_due": start,
                    "invoice_payment_term_id": False,
                    "invoice_origin": _("Service charge %s") % month.strftime("%m/%Y"),
                    "invoice_line_ids": [(0, 0, vals) for vals in lines],
                }
            )
        if made and cycle:
            cycle.invoiced = True
        text = _("%s draft invoice(s) made for %s.") % (len(made), month.strftime("%m/%Y"))
        if skipped:
            text += "\n" + _("Left out: %s.") % ", ".join(skipped)
        text += "\n" + _("Look them over, then post them.")
        self.write({"state": "done", "summary": text, "invoice_ids": [(6, 0, made.ids)]})
        return self._reopen()

    def _reopen(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Create service charge invoices"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
        }

    def action_open_invoices(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Service charge invoices"),
            "res_model": "account.move",
            "view_mode": "tree,form",
            "views": [(False, "tree"), (False, "form")],
            "domain": [("id", "in", self.invoice_ids.ids)],
        }
