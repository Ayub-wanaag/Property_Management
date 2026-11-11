import base64

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .water_common import WaterCommon

PHOTO = base64.b64encode(b"sample photo bytes")


@tagged("post_install", "-at_install")
class TestPhase2(WaterCommon):
    # ----- helpers -----
    def _new_lease(self, unit, deposit=1000.0):
        tenant = self.env["res.partner"].create({"name": "Phase2 Tenant %s" % unit.number})
        return self.env["pm.lease"].create(
            {"unit_id": unit.id, "tenant_id": tenant.id, "rent_amount": 1000, "deposit_amount": deposit}
        )

    def _held_deposit(self, amount=1000.0):
        lease = self.leases[0]
        deposit = self.env["pm.deposit"].create({"lease_id": lease.id, "amount": amount})
        deposit.action_receive()
        return deposit

    def _photos_for_all_rooms(self, inspection):
        for room in inspection.lease_id.unit_id.unit_type_id.room_ids:
            self.env["pm.inspection.photo"].create({"inspection_id": inspection.id, "room_id": room.id, "image": PHOTO})

    def _unpaid_invoice(self, lease, amount=500.0):
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": lease.tenant_id.id,
                "pm_lease_id": lease.id,
                "invoice_date": self.today,
                "invoice_line_ids": [
                    (0, 0, {"product_id": self.product_a.id, "quantity": 1, "price_unit": amount, "tax_ids": [(6, 0, [])]})
                ],
            }
        )
        invoice.action_post()
        return invoice

    # ===== 2.4 deposits =====
    def test_starting_a_lease_notes_the_deposit_and_opens_the_move_in_photos(self):
        lease = self._new_lease(self.units[2], deposit=1000)
        lease.action_start()
        deposit = self.env["pm.deposit"].search([("lease_id", "=", lease.id)])
        self.assertEqual((deposit.amount, deposit.state), (1000, "pending"))
        inspection = self.env["pm.inspection"].search([("lease_id", "=", lease.id)])
        self.assertEqual((inspection.kind, inspection.state), ("move_in", "draft"))

    def test_a_lease_without_a_deposit_makes_none(self):
        lease = self._new_lease(self.units[2], deposit=0)
        lease.action_start()
        self.assertFalse(self.env["pm.deposit"].search([("lease_id", "=", lease.id)]))

    def test_refund_is_deposit_minus_deductions_and_dated_from_the_building_rule(self):
        deposit = self._held_deposit(1000)
        self.env["pm.deposit.deduction"].create({"deposit_id": deposit.id, "reason": "damage", "description": "Window", "amount": 300})
        self.assertEqual((deposit.total_deductions, deposit.refund_amount, deposit.tenant_owes), (300, 700, 0))
        deposit.action_prepare_refund()
        self.assertEqual(deposit.state, "refund_pending")
        self.assertEqual((deposit.refund_due_date - self.today).days, self.building.deposit_refund_days)

    def test_deductions_bigger_than_the_deposit_leave_the_tenant_owing(self):
        deposit = self._held_deposit(1000)
        self.env["pm.deposit.deduction"].create({"deposit_id": deposit.id, "reason": "damage", "description": "Door", "amount": 1500})
        self.assertEqual((deposit.refund_amount, deposit.tenant_owes), (0, 500))

    def test_a_deduction_must_be_more_than_zero(self):
        deposit = self._held_deposit()
        with self.assertRaises(ValidationError):
            self.env["pm.deposit.deduction"].create({"deposit_id": deposit.id, "reason": "other", "description": "x", "amount": 0})

    def test_unpaid_invoices_come_off_the_deposit_once(self):
        deposit = self._held_deposit(1000)
        invoice = self._unpaid_invoice(self.leases[0], 500)
        deposit.action_add_unpaid_balances()
        line = deposit.deduction_ids
        self.assertEqual((line.amount, line.invoice_id), (500, invoice))
        with self.assertRaises(UserError):
            deposit.action_add_unpaid_balances()

    def test_final_water_comes_off_the_deposit_at_the_latest_rate(self):
        deposit = self._held_deposit(5000)
        meter = self._meter(self.units[0], "SM-F1", start=100)
        self._read(meter, 110)  # 10 units not billed yet
        self._cycle(ncwsc=1000, borehole=100, main=11)  # rate 100
        deposit.action_add_final_water()
        self.assertEqual(deposit.deduction_ids.amount, 1000)
        with self.assertRaises(UserError):
            deposit.action_add_final_water()

    def test_only_a_refund_that_is_being_prepared_can_be_disputed_and_a_reason_is_needed(self):
        deposit = self._held_deposit()
        with self.assertRaises(UserError):
            deposit._tenant_dispute("I disagree")
        deposit.action_prepare_refund()
        with self.assertRaises(ValidationError):
            deposit._tenant_dispute("   ")

    def test_dispute_flow_blocks_the_refund_until_decided(self):
        deposit = self._held_deposit()
        deposit.action_prepare_refund()
        dispute = deposit._tenant_dispute("The window was already cracked.")
        self.assertEqual(deposit.state, "disputed")
        with self.assertRaises(UserError):
            deposit.action_mark_refunded()
        with self.assertRaises(UserError):
            dispute.action_resolve()  # no decision written
        dispute.resolution_note = "Checked the move-in photos. Deduction removed."
        dispute.action_resolve()
        self.assertEqual((dispute.state, deposit.state), ("resolved", "refund_pending"))
        deposit.action_mark_refunded()
        self.assertEqual(deposit.state, "refunded")

    def test_deductions_are_locked_once_refunded(self):
        deposit = self._held_deposit()
        deposit.action_prepare_refund()
        deposit.action_mark_refunded()
        with self.assertRaises(UserError):
            self.env["pm.deposit.deduction"].create({"deposit_id": deposit.id, "reason": "other", "description": "x", "amount": 10})

    # ===== 2.5 inspections and move-out =====
    def test_unit_types_get_their_rooms(self):
        types = self.env["pm.unit.type"]
        two = types.create({"name": "2 bedroom test"})
        self.assertEqual(two.room_ids.mapped("name"), ["Living area", "Kitchen", "Bathroom", "Bedroom 1", "Bedroom 2"])
        self.assertEqual(types.create({"name": "Bedsitter test"}).room_ids.mapped("name"), ["Main room", "Kitchen", "Bathroom"])

    def test_every_room_needs_a_photo_before_an_inspection_can_finish(self):
        inspection = self.env["pm.inspection"]._make_for_lease(self.leases[0], "move_in")
        with self.assertRaises(UserError):
            inspection.action_done()
        self.assertTrue(inspection.missing_rooms)
        self._photos_for_all_rooms(inspection)
        self.assertFalse(inspection.missing_rooms)
        inspection.action_done()
        self.assertEqual(inspection.state, "done")
        with self.assertRaises(UserError):
            inspection.photo_ids[:1].unlink()

    def test_a_photo_over_the_size_limit_is_refused(self):
        self.building.max_photo_mb = 1
        inspection = self.env["pm.inspection"]._make_for_lease(self.leases[0], "move_in")
        room = inspection.unit_type_id.room_ids[:1]
        with self.assertRaises(ValidationError):
            self.env["pm.inspection.photo"].create(
                {"inspection_id": inspection.id, "room_id": room.id, "image": base64.b64encode(b"x" * 1_600_000)}
            )

    def _request_move_out(self, lease):
        return self.env["pm.move.out.request"].create({"lease_id": lease.id, "requested_date": self.today})

    def test_a_move_out_request_gives_notice(self):
        lease = self.leases[0]
        request = self._request_move_out(lease)
        self.assertEqual((lease.state, lease.unit_id.status), ("notice", "notice_given"))
        self.assertEqual(request.state, "requested")
        self.assertTrue(request.short_notice)  # the lease asks for 30 days, the date is today

    def test_only_one_move_out_request_at_a_time(self):
        self._request_move_out(self.leases[0])
        with self.assertRaises(ValidationError):
            self._request_move_out(self.leases[0])

    def test_full_move_out_flow(self):
        lease = self.leases[0]
        deposit = self._held_deposit(1000)
        request = self._request_move_out(lease)
        with self.assertRaises(UserError):
            request.action_review()  # no photos yet
        request.action_acknowledge()
        rooms = lease.unit_id.unit_type_id.room_ids
        self.env["pm.inspection"]._make_for_lease(lease, "move_in")
        self.env["pm.move.out.request"]._tenant_upload_photos(request, [(room, "r.jpg", b"img") for room in rooms])
        self.assertEqual(request.state, "photos_uploaded")
        self.assertIn("<img", request.comparison_html)
        request.action_review()
        self.assertEqual(request.state, "reviewed")
        with self.assertRaises(UserError):
            request.action_complete()  # the refund has not been prepared
        deposit.action_prepare_refund()
        request.action_complete()
        self.assertEqual((request.state, lease.state, lease.unit_id.status), ("completed", "ended", "vacant"))

    def test_move_out_cannot_complete_while_a_deduction_is_disputed(self):
        lease = self.leases[0]
        deposit = self._held_deposit()
        request = self._request_move_out(lease)
        request.action_acknowledge()
        rooms = lease.unit_id.unit_type_id.room_ids
        self.env["pm.move.out.request"]._tenant_upload_photos(request, [(room, "r.jpg", b"img") for room in rooms])
        request.action_review()
        deposit.action_prepare_refund()
        deposit._tenant_dispute("Not my damage.")
        with self.assertRaises(UserError):
            request.action_complete()

    # ===== 2.6 problems and approvals =====
    def _ticket(self, cost=0.0, priority="normal", **extra):
        values = {"name": "Leaking tap", "unit_id": self.units[0].id, "lease_id": self.leases[0].id, "priority": priority, "estimated_cost": cost}
        values.update(extra)
        return self.env["pm.ticket"].create(values)

    def test_a_cheap_repair_starts_straight_away(self):
        self.building.approval_threshold = 20000
        ticket = self._ticket(cost=5000, vendor_name="Joseph the plumber")
        ticket.action_assign()
        ticket.action_start()
        self.assertEqual(ticket.state, "in_progress")

    def test_no_threshold_means_no_approval_is_ever_needed(self):
        self.building.approval_threshold = 0
        ticket = self._ticket(cost=900000)
        self.assertFalse(ticket.requires_approval)

    def test_an_expensive_repair_waits_for_the_manager(self):
        self.building.approval_threshold = 20000
        ticket = self._ticket(cost=35000, vendor_name="Roofer")
        self.assertTrue(ticket.requires_approval)
        ticket.action_start()
        self.assertEqual(ticket.state, "waiting_approval")
        ticket.action_approve()
        self.assertEqual((ticket.state, ticket.approved, ticket.approved_by), ("in_progress", True, self.env.user))

    def test_a_caretaker_cannot_approve(self):
        self.building.approval_threshold = 20000
        caretaker = self._user("p2_caretaker", "group_pm_caretaker")
        ticket = self._ticket(cost=35000, assigned_user_id=caretaker.id)
        ticket.action_start()
        with self.assertRaises(AccessError):
            ticket.with_user(caretaker).action_approve()

    def test_changing_the_cost_clears_an_approval(self):
        self.building.approval_threshold = 20000
        ticket = self._ticket(cost=35000)
        ticket.action_start()
        ticket.action_approve()
        ticket.estimated_cost = 50000
        self.assertFalse(ticket.approved)

    def test_assigning_needs_someone_and_the_flow_runs_in_order(self):
        ticket = self._ticket()
        with self.assertRaises(UserError):
            ticket.action_assign()
        ticket.vendor_name = "Plumber"
        ticket.action_assign()
        ticket.action_start()
        with self.assertRaises(UserError):
            ticket.action_close()  # not done yet
        ticket.action_done()
        ticket.action_close()
        self.assertEqual(ticket.state, "closed")

    def test_urgent_problems_are_still_logged_and_sorted_first(self):
        normal = self._ticket(priority="normal")
        urgent = self._ticket(priority="urgent", name="Burst pipe")
        self.assertEqual(self.env["pm.ticket"].search([("id", "in", (normal | urgent).ids)])[0], urgent)

    def test_a_caretaker_sees_only_the_problems_assigned_to_them(self):
        caretaker = self._user("p2_caretaker2", "group_pm_caretaker")
        mine = self._ticket(assigned_user_id=caretaker.id)
        other = self._ticket(name="Someone else's")
        visible = self.env["pm.ticket"].with_user(caretaker).search([("id", "in", (mine | other).ids)])
        self.assertEqual(visible, mine)

    def test_today_counts_the_new_cards(self):
        self.building.approval_threshold = 20000
        before = self.env["pm.today"].create({})
        ticket = self._ticket(cost=35000, priority="urgent")
        ticket.action_start()
        self._request_move_out(self.leases[1])
        after = self.env["pm.today"].create({})
        self.assertEqual(after.approvals_count, before.approvals_count + 1)
        self.assertEqual(after.urgent_count, before.urgent_count + 1)
        self.assertEqual(after.moveouts_count, before.moveouts_count + 1)

    # ===== 2.7 help and documents =====
    def test_the_five_help_guides_exist(self):
        names = self.env["pm.help.guide"].search([]).mapped("name")
        for job in ("Move in a tenant", "Record a payment", "Enter meter readings", "Approve a late-payment request", "Move out a tenant"):
            self.assertIn(job, names)

    def _document(self, **extra):
        values = {"name": "Doc", "file": PHOTO, "building_id": self.building.id}
        values.update(extra)
        return self.env["pm.document"].create(values)

    def test_a_paper_for_the_tenant_needs_a_lease(self):
        with self.assertRaises(ValidationError):
            self._document(visibility="tenant")
        self._document(visibility="tenant", lease_id=self.leases[0].id)

    def test_managers_only_papers_are_hidden_from_other_staff(self):
        caretaker = self._user("p2_caretaker3", "group_pm_caretaker")
        private = self._document(visibility="managers")
        shared = self._document(visibility="staff")
        visible = self.env["pm.document"].with_user(caretaker).search([("id", "in", (private | shared).ids)])
        self.assertEqual(visible, shared)

    def test_a_file_over_ten_megabytes_is_refused(self):
        with self.assertRaises(ValidationError):
            self._document(file=base64.b64encode(b"x" * 11_000_000))

    # ===== maintenance costs =====
    def test_maintenance_products_exist(self):
        self.assertTrue(self.env.ref("property_management.product_maintenance_fund"))
        self.assertTrue(self.env.ref("property_management.product_repair_charge"))

    def test_a_shared_area_problem_needs_no_unit(self):
        ticket = self.env["pm.ticket"].create(
            {"name": "Stair light is out", "building_id": self.building.id, "category": "lighting", "charged_to": "pool"}
        )
        self.assertFalse(ticket.unit_id)
        self.assertEqual((ticket.building_id, ticket.charged_to), (self.building, "pool"))

    def test_the_building_follows_the_unit(self):
        ticket = self._ticket()
        self.assertEqual(ticket.building_id, self.building)

    def test_charging_the_tenant_needs_a_lease(self):
        with self.assertRaises(ValidationError):
            self.env["pm.ticket"].create(
                {"name": "Broken window", "building_id": self.building.id, "unit_id": self.units[2].id, "charged_to": "tenant"}
            )

    def test_a_repair_the_tenant_caused_becomes_a_draft_invoice_once(self):
        self.env.user.groups_id |= self.env.ref("property_management.group_pm_accountant")
        ticket = self._ticket(name="Broken window", category="carpentry", charged_to="tenant", vendor_name="Fundi", actual_cost=3800)
        with self.assertRaises(UserError):
            ticket.action_charge_tenant()  # work not done yet
        ticket.action_start()
        ticket.action_done()
        ticket.action_charge_tenant()
        invoice = ticket.repair_invoice_id
        self.assertEqual((invoice.state, invoice.amount_total, invoice.pm_kind), ("draft", 3800, "other"))
        with self.assertRaises(UserError):
            ticket.action_charge_tenant()

    def test_a_repair_charged_to_the_owner_cannot_be_invoiced_to_the_tenant(self):
        self.env.user.groups_id |= self.env.ref("property_management.group_pm_accountant")
        ticket = self._ticket(charged_to="owner", actual_cost=500)
        ticket.action_start()
        ticket.action_done()
        with self.assertRaises(UserError):
            ticket.action_charge_tenant()
