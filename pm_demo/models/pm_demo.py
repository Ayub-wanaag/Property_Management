import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class PmDemo(models.AbstractModel):
    _name = "pm.demo"
    _description = "Demo data helpers"

    @api.model
    def create_invoices(self):
        """Sample invoices at different ages, so the chase list and arrears view have something to show."""
        try:
            with self.env.cr.savepoint():
                self._create_invoices()
        except Exception:  # noqa: BLE001 - demo data must never stop an install
            _logger.warning("Demo invoices were not created (is Accounting set up?)", exc_info=True)

    def _create_invoices(self):
        self.env.cr.execute("SELECT (NOW() AT TIME ZONE 'Africa/Nairobi')::date")
        today = self.env.cr.fetchone()[0]
        # (unit, days past due; negative = not due yet, paid?)
        plan = [("A12", 12, True), ("A5", 3, False), ("A8", 2, False), ("A16", 20, False),
                ("A1", 40, False), ("A2", 75, False), ("A10", -5, False)]
        moves = {}
        for unit_number, days_ago, paid in plan:
            lease = self.env["pm.lease"].search(
                [("unit_id.number", "=", unit_number), ("state", "in", ("active", "notice"))], limit=1
            )
            if not lease:
                continue
            due = today - timedelta(days=days_ago)
            move = self.env["account.move"].create(
                {
                    "move_type": "out_invoice",
                    "partner_id": lease.tenant_id.id,
                    "invoice_date": due - timedelta(days=5),
                    "invoice_date_due": due,
                    "invoice_payment_term_id": False,
                    "invoice_line_ids": [
                        (0, 0, {
                            "product_id": lease.unit_id.unit_type_id.product_id.id,
                            "name": "Rent %s" % unit_number,
                            "quantity": 1,
                            "price_unit": lease.rent_amount,
                            "tax_ids": [(6, 0, [])],
                        })
                    ],
                }
            )
            move.action_post()
            moves[unit_number] = move
            if paid:
                self.env["account.payment.register"].with_context(
                    active_model="account.move", active_ids=move.ids
                ).create({})._create_payments()
        if "A5" in moves:
            invoice = moves["A5"]
            self.env["pm.payment.claim"].create(
                {
                    "lease_id": invoice.pm_lease_id.id,
                    "kind": "rent",
                    "amount": invoice.amount_total,
                    "method": "mpesa",
                    "sms_text": "SHL7K2M9QX Confirmed. Ksh{:,.2f} sent to Sunrise Apartments on {}.".format(
                        invoice.amount_total, today.strftime("%d/%m/%y")
                    ),
                }
            )
        if "A8" in moves:
            self.env["pm.late.request"].create(
                {
                    "invoice_id": moves["A8"].id,
                    "proposed_date": today + timedelta(days=7),
                    "reason": "My salary is paid at the end of the month.",
                }
            )
        log = self.env["pm.chase.log"]
        if "A5" in moves:
            log.with_context(default_invoice_id=moves["A5"].id).create(
                {"channel": "call", "note": "Said she will pay in two days.", "promised_date": today + timedelta(days=2)}
            )
        if "A16" in moves:
            log.with_context(default_invoice_id=moves["A16"].id).create(
                {"channel": "whatsapp", "note": "No reply yet. Will visit tomorrow.", "date": today - timedelta(days=3)}
            )

    # ----- water and generator demo: reproduces the pitch numbers -----
    @api.model
    def create_water_demo(self):
        """Meters, one month of readings, the water costs and the generator plan, using the README's figures."""
        try:
            with self.env.cr.savepoint():
                self._create_water_demo()
        except Exception:  # noqa: BLE001 - demo data must never stop an install
            _logger.warning("Water demo data was not created", exc_info=True)

    def _create_water_demo(self):
        building = self.env.ref("pm_demo.pm_building_sunrise")
        leases = self.env["pm.lease"].search([("building_id", "=", building.id), ("state", "in", ("active", "notice"))])
        by_number = {lease.unit_id.number: lease for lease in leases}
        # A12 is Amina: 1,240 to 1,252 = 12 units. The other 18 units add up to 193, so the building bills 205.
        others = sorted((number for number in by_number if number != "A12"), key=lambda n: int(n[1:]))
        usage = {"A12": 12}
        for index, number in enumerate(others):
            usage[number] = 11 if index < 13 else 10
        today = fields.Date.context_today(self)
        for index, number in enumerate(sorted(by_number, key=lambda n: int(n[1:]))):
            start = 1240 if number == "A12" else 100 + 10 * index
            meter = self.env["pm.meter"].create(
                {"unit_id": by_number[number].unit_id.id, "serial": "SM-%s" % number, "start_reading": start,
                 "start_date": today.replace(day=1)}
            )
            self.env["pm.meter.reading"].create(
                {"meter_id": meter.id, "date": today, "kind": "actual", "current": start + usage[number]}
            )
        self.env["pm.water.cycle"].create(
            {"building_id": building.id, "month": today, "ncwsc_cost": 18200, "borehole_cost": 6000, "main_meter_units": 220}
        )
        for number in ("A12", "A1", "A4", "A5", "A7", "A10", "A13", "A18", "A22"):
            if number in by_number:
                self.env["pm.generator.subscription"].create({"lease_id": by_number[number].id, "on_plan": True})

    # ----- Phase 2.4 to 2.7 demo: deposits, photos, move-out, problems, documents -----
    @api.model
    def create_phase2_demo(self):
        """A few deposits, a move-in photo set, move-outs in different steps, problems and a document."""
        try:
            with self.env.cr.savepoint():
                self._create_phase2_demo()
        except Exception:  # noqa: BLE001 - demo data must never stop an install
            _logger.warning("Phase 2 demo data was not created", exc_info=True)

    def _create_phase2_demo(self):
        import base64

        building = self.env.ref("pm_demo.pm_building_sunrise")
        building.approval_threshold = 20000
        leases = self.env["pm.lease"].search([("building_id", "=", building.id), ("state", "in", ("active", "notice"))])
        by_number = {lease.unit_id.number: lease for lease in leases}
        # a 1x1 sample picture, so the photo screens show something
        pixel = base64.b64encode(
            base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        )
        # deposits: one month's rent, held, for four tenants
        deposits = {}
        for number in ("A12", "A8", "A5", "A6"):
            lease = by_number.get(number)
            if lease:
                deposits[number] = self.env["pm.deposit"].create(
                    {"lease_id": lease.id, "amount": lease.rent_amount, "state": "held", "received_on": lease.start_date}
                )
        # A12 has a finished move-in inspection with a photo of every room
        if "A12" in by_number:
            lease = by_number["A12"]
            inspection = self.env["pm.inspection"]._make_for_lease(lease, "move_in")
            for room in lease.unit_id.unit_type_id.room_ids:
                self.env["pm.inspection.photo"].create(
                    {"inspection_id": inspection.id, "room_id": room.id, "image": pixel, "note": "Sample photo"}
                )
            inspection.action_done()
        # A6 has just asked to move out (notice already given in the demo); A17 has been acknowledged
        for number, step in (("A6", "requested"), ("A17", "acknowledged")):
            lease = by_number.get(number)
            if lease:
                request = self.env["pm.move.out.request"].create(
                    {"lease_id": lease.id, "requested_date": fields.Date.context_today(self), "tenant_note": "Sample data"}
                )
                if step == "acknowledged":
                    request.action_acknowledge()
        # problems: urgent, normal and one waiting for approval (above the 20,000 limit)
        tickets = (
            ("A5", "Burst pipe in the bathroom", "urgent", 0, "new"),
            ("A8", "Kitchen tap is dripping", "normal", 0, "new"),
            ("A12", "Roof leaks over the balcony", "normal", 35000, "waiting_approval"),
        )
        for number, title, priority, cost, step in tickets:
            lease = by_number.get(number)
            if not lease:
                continue
            ticket = self.env["pm.ticket"].create(
                {
                    "name": title,
                    "lease_id": lease.id,
                    "unit_id": lease.unit_id.id,
                    "priority": priority,
                    "estimated_cost": cost,
                    "source": "tenant",
                    "description": "Sample data",
                }
            )
            if step == "waiting_approval":
                ticket.vendor_name = "Sample roofer (07XX XXX XXX)"
                ticket.action_start()
        # a document the tenant of A12 can download
        if "A12" in by_number:
            lease = by_number["A12"]
            self.env["pm.document"].create(
                {
                    "name": "Lease agreement A12 (sample)",
                    "category": "lease",
                    "file": base64.b64encode(b"Sample lease agreement. This is demonstration data."),
                    "file_name": "lease_A12_sample.txt",
                    "building_id": building.id,
                    "lease_id": lease.id,
                    "visibility": "tenant",
                }
            )
