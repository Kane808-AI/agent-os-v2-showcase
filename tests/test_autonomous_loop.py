from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os.autonomy import (  # noqa: E402
    AutonomousLoop,
    ExecutionResult,
    LeaseLostError,
    WorkStatus,
)
from agent_os.contracts import (  # noqa: E402
    ActorIdentity,
    ActorType,
    ApprovalDecision,
    AuthorityEnvelope,
    AuthorityMode,
    AuthorityRule,
    Business,
    EmergencyStopAction,
    Objective,
    ObjectiveStatus,
    Tenant,
)
from agent_os.dashboard import render_dashboard  # noqa: E402
from agent_os.storage import SQLiteStore  # noqa: E402


class AutonomousLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "autonomy.db")
        self.store.initialize()
        self.now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
        self.store.upsert_tenant(
            Tenant(tenant_id="tenant-1", display_name="Tenant One")
        )
        self.store.upsert_business(
            Business(
                business_id="business-1",
                tenant_id="tenant-1",
                legal_name="Business One LLC",
                display_name="Business One",
                base_currency="USD",
                timezone_name="America/Los_Angeles",
            )
        )
        self.store.upsert_actor(
            ActorIdentity(
                actor_id="atlas",
                tenant_id="tenant-1",
                actor_type=ActorType.AGENT,
                roles=frozenset({"orchestrator"}),
                business_ids=frozenset({"business-1"}),
            )
        )
        self.store.upsert_actor(
            ActorIdentity(
                actor_id="marketing-agent",
                tenant_id="tenant-1",
                actor_type=ActorType.AGENT,
                roles=frozenset({"marketing"}),
                business_ids=frozenset({"business-1"}),
            )
        )
        self.store.upsert_actor(
            ActorIdentity(
                actor_id="owner",
                tenant_id="tenant-1",
                actor_type=ActorType.HUMAN,
                roles=frozenset({"business-owner", "approver"}),
                business_ids=frozenset({"business-1"}),
            )
        )
        self.set_authority(AuthorityMode.AUTO)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def set_authority(self, mode: AuthorityMode | None) -> None:
        rules = (
            (
                AuthorityRule(
                    action_type="marketing.pipeline.review",
                    mode=mode,
                    roles=frozenset({"marketing"}),
                ),
            )
            if mode is not None
            else ()
        )
        self.store.upsert_authority_envelope(
            AuthorityEnvelope(
                envelope_id="envelope-1",
                tenant_id="tenant-1",
                business_id="business-1",
                rules=rules,
                expires_at=self.now + timedelta(days=30),
            )
        )

    def add_objective(
        self,
        *,
        objective_id: str = "objective-1",
        priority: int = 10,
    ) -> None:
        self.store.upsert_objective(
            Objective(
                objective_id=objective_id,
                tenant_id="tenant-1",
                business_id="business-1",
                statement="Generate qualified leads.",
                metric="qualified_leads",
                target=Decimal("25"),
                current_value=Decimal("0"),
                status=ObjectiveStatus.ACTIVE,
                priority=priority,
                review_interval_seconds=3600,
            ),
            next_review_at=self.now,
        )

    def test_cycle_discovers_and_completes_work_without_an_event(self) -> None:
        self.add_objective()
        report = AutonomousLoop(
            self.store, worker_id="worker-1"
        ).run_cycle(now=self.now)
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(report.objectives_reviewed, 1)
        self.assertEqual(report.work_discovered, 1)
        self.assertEqual(report.work_simulated, 1)
        self.assertEqual(snapshot["counts"]["events"], 0)
        self.assertEqual(snapshot["work_statuses"], {"simulated": 1})
        self.assertEqual(
            [
                record["record_type"]
                for record in reversed(snapshot["audit_records"])
            ],
            ["work.discovered", "work.simulated"],
        )

    def test_second_discovery_cycle_does_not_duplicate_work(self) -> None:
        self.add_objective()
        loop = AutonomousLoop(self.store, worker_id="worker-1")
        first = loop.run_cycle(now=self.now, max_work=0)
        second = loop.run_cycle(now=self.now, max_work=0)
        self.assertEqual(first.work_discovered, 1)
        self.assertEqual(second.objectives_reviewed, 0)
        self.assertEqual(
            self.store.dashboard_snapshot()["counts"]["work_items"], 1
        )

    def test_missing_department_agent_defers_instead_of_crashing(self) -> None:
        self.add_objective()
        self.store.upsert_actor(
            ActorIdentity(
                actor_id="marketing-agent",
                tenant_id="tenant-1",
                actor_type=ActorType.AGENT,
                roles=frozenset({"marketing"}),
                business_ids=frozenset({"business-1"}),
                enabled=False,
            )
        )
        report = AutonomousLoop(self.store).run_cycle(now=self.now)
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(report.work_deferred, 1)
        self.assertEqual(snapshot["counts"]["work_items"], 0)
        self.assertEqual(
            snapshot["audit_records"][0]["record_type"],
            "work.discovery_deferred",
        )

    def test_approval_policy_creates_a_hold_without_execution(self) -> None:
        self.set_authority(AuthorityMode.APPROVE)
        self.add_objective()
        report = AutonomousLoop(self.store).run_cycle(now=self.now)
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(report.approval_holds, 1)
        self.assertEqual(report.work_simulated, 0)
        self.assertEqual(snapshot["work_statuses"], {"awaiting_approval": 1})

    def test_separate_approver_releases_held_work_for_execution(self) -> None:
        self.set_authority(AuthorityMode.APPROVE)
        self.add_objective()
        loop = AutonomousLoop(self.store, worker_id="worker-1")
        loop.run_cycle(now=self.now, max_work=0)
        work = self.store.dashboard_snapshot()["work_items"][0]
        approval = self.store.get_work_approval(work["work_item_id"])
        self.assertIsNotNone(approval)
        with self.assertRaisesRegex(ValueError, "separate authorized approver"):
            self.store.decide_work_approval(
                approval_id=approval["approval_id"],
                event_id="approval-by-requester",
                approver_id="marketing-agent",
                decision=ApprovalDecision.APPROVED,
                rationale="Self approval must fail.",
                now=self.now + timedelta(minutes=1),
            )
        self.assertTrue(
            self.store.decide_work_approval(
                approval_id=approval["approval_id"],
                event_id="approval-by-owner",
                approver_id="owner",
                decision=ApprovalDecision.APPROVED,
                rationale="Bounded review is approved.",
                now=self.now + timedelta(minutes=1),
            )
        )
        self.assertEqual(
            self.store.get_work_item(work["work_item_id"])["status"],
            "ready",
        )
        self.assertEqual(
            loop.execute_one(now=self.now + timedelta(minutes=2)),
            WorkStatus.SIMULATED,
        )

    def test_approval_can_be_revoked_and_reapproved_before_execution(
        self,
    ) -> None:
        self.set_authority(AuthorityMode.APPROVE)
        self.add_objective()
        loop = AutonomousLoop(self.store, worker_id="worker-1")
        loop.run_cycle(now=self.now, max_work=0)
        work = self.store.dashboard_snapshot()["work_items"][0]
        approval = self.store.get_work_approval(work["work_item_id"])
        self.store.decide_work_approval(
            approval_id=approval["approval_id"],
            event_id="approval-granted",
            approver_id="owner",
            decision=ApprovalDecision.APPROVED,
            rationale="Initial bounded approval.",
            now=self.now + timedelta(minutes=1),
        )
        self.store.decide_work_approval(
            approval_id=approval["approval_id"],
            event_id="approval-revoked",
            approver_id="owner",
            decision=ApprovalDecision.REVOKED,
            rationale="Conditions changed before execution.",
            now=self.now + timedelta(minutes=2),
        )
        self.assertEqual(
            self.store.get_work_item(work["work_item_id"])["status"],
            "awaiting_approval",
        )
        self.assertIsNone(
            loop.execute_one(now=self.now + timedelta(minutes=3))
        )
        self.store.decide_work_approval(
            approval_id=approval["approval_id"],
            event_id="approval-restored",
            approver_id="owner",
            decision=ApprovalDecision.APPROVED,
            rationale="Conditions were revalidated.",
            now=self.now + timedelta(minutes=4),
        )
        self.assertEqual(
            loop.execute_one(now=self.now + timedelta(minutes=5)),
            WorkStatus.SIMULATED,
        )

    def test_expired_approval_never_releases_work(self) -> None:
        self.set_authority(AuthorityMode.APPROVE)
        self.add_objective()
        AutonomousLoop(self.store).run_cycle(now=self.now, max_work=0)
        work = self.store.dashboard_snapshot()["work_items"][0]
        approval = self.store.get_work_approval(work["work_item_id"])
        self.store.decide_work_approval(
            approval_id=approval["approval_id"],
            event_id="approval-before-expiry",
            approver_id="owner",
            decision=ApprovalDecision.APPROVED,
            rationale="Valid only inside the approval window.",
            now=self.now + timedelta(minutes=1),
        )
        claimed = self.store.claim_next_work(
            worker_id="worker-before-expiry",
            now=self.now + timedelta(minutes=2),
            lease_seconds=100_000,
        )
        self.assertIsNotNone(claimed)
        expired_at = self.now + timedelta(days=1, seconds=1)
        self.assertEqual(
            self.store.expire_work_approvals(now=expired_at),
            1,
        )
        self.assertEqual(
            self.store.get_work_item(work["work_item_id"])["status"],
            WorkStatus.APPROVAL_EXPIRED.value,
        )
        with self.assertRaisesRegex(ValueError, "expired"):
            self.store.decide_work_approval(
                approval_id=approval["approval_id"],
                event_id="late-approval",
                approver_id="owner",
                decision=ApprovalDecision.APPROVED,
                rationale="Too late.",
                now=expired_at,
            )
        self.assertFalse(
            self.store.resolve_claimed_work(
                work_item_id=work["work_item_id"],
                worker_id="worker-before-expiry",
                status="simulated",
                authority_mode="approve",
                record_type="work.simulated",
                details={"status": "simulated"},
                audit_id="stale-expired-resolution",
                now=expired_at,
            )
        )
        self.assertIsNone(
            AutonomousLoop(self.store).execute_one(now=expired_at)
        )

    def test_emergency_stop_releases_leases_and_blocks_new_execution(
        self,
    ) -> None:
        self.add_objective()
        loop = AutonomousLoop(self.store, worker_id="worker-1")
        loop.run_cycle(now=self.now, max_work=0)
        claimed = self.store.claim_next_work(
            worker_id="worker-before-stop",
            now=self.now,
            lease_seconds=300,
        )
        self.assertIsNotNone(claimed)
        with self.assertRaisesRegex(ValueError, "authorized in-scope human"):
            self.store.record_emergency_stop(
                event_id="stop-by-agent",
                tenant_id="tenant-1",
                business_id="business-1",
                actor_id="marketing-agent",
                action=EmergencyStopAction.ACTIVATED,
                reason="Agent must not control the stop.",
                now=self.now + timedelta(seconds=1),
            )
        self.assertTrue(
            self.store.record_emergency_stop(
                event_id="stop-active",
                tenant_id="tenant-1",
                business_id="business-1",
                actor_id="owner",
                action=EmergencyStopAction.ACTIVATED,
                reason="Owner paused autonomous execution.",
                now=self.now + timedelta(seconds=1),
            )
        )
        released = self.store.get_work_item(claimed["work_item_id"])
        self.assertEqual(released["status"], "ready")
        self.assertIsNone(
            loop.execute_one(now=self.now + timedelta(seconds=2))
        )
        self.assertFalse(
            self.store.resolve_claimed_work(
                work_item_id=claimed["work_item_id"],
                worker_id="worker-before-stop",
                status="simulated",
                authority_mode="auto",
                record_type="work.simulated",
                details={"status": "simulated"},
                audit_id="stale-stop-resolution",
                now=self.now + timedelta(seconds=2),
            )
        )
        self.store.record_emergency_stop(
            event_id="stop-cleared",
            tenant_id="tenant-1",
            business_id="business-1",
            actor_id="owner",
            action=EmergencyStopAction.CLEARED,
            reason="Owner verified safe resumption.",
            now=self.now + timedelta(seconds=3),
        )
        self.assertEqual(
            loop.execute_one(now=self.now + timedelta(seconds=4)),
            WorkStatus.SIMULATED,
        )

    def test_direct_sql_cannot_self_approve_or_change_held_work(self) -> None:
        self.set_authority(AuthorityMode.APPROVE)
        self.add_objective()
        AutonomousLoop(self.store).run_cycle(now=self.now, max_work=0)
        work = self.store.dashboard_snapshot()["work_items"][0]
        approval = self.store.get_work_approval(work["work_item_id"])
        with sqlite3.connect(self.store.path) as connection:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "separate authorized human approver",
            ):
                connection.execute(
                    """
                    INSERT INTO approval_events(
                        event_id, approval_id, tenant_id, business_id,
                        actor_id, decision, rationale, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'approved', ?, ?)
                    """,
                    (
                        "forged-self-approval",
                        approval["approval_id"],
                        "tenant-1",
                        "business-1",
                        "marketing-agent",
                        "Agent forged its own approval.",
                        (self.now + timedelta(minutes=1)).isoformat(),
                    ),
                )
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "approval-bound work semantics are immutable",
            ):
                connection.execute(
                    """
                    UPDATE work_items
                    SET title = 'Changed after approval request'
                    WHERE work_item_id = ?
                    """,
                    (work["work_item_id"],),
                )

    def test_policy_is_checked_again_after_work_is_discovered(self) -> None:
        self.add_objective()
        loop = AutonomousLoop(self.store, worker_id="worker-1")
        loop.run_cycle(now=self.now, max_work=0)
        self.set_authority(None)
        status = loop.execute_one(now=self.now)
        self.assertEqual(status, WorkStatus.REJECTED)
        self.assertEqual(
            self.store.dashboard_snapshot()["work_statuses"],
            {"rejected": 1},
        )

    def test_only_one_worker_can_hold_a_live_lease(self) -> None:
        self.add_objective()
        loop = AutonomousLoop(self.store, worker_id="discovery")
        loop.run_cycle(now=self.now, max_work=0)
        first = self.store.claim_next_work(
            worker_id="worker-a",
            now=self.now,
            lease_seconds=10,
        )
        second = self.store.claim_next_work(
            worker_id="worker-b",
            now=self.now,
            lease_seconds=10,
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_stale_worker_cannot_report_success_after_lease_loss(self) -> None:
        self.add_objective()
        AutonomousLoop(self.store).run_cycle(now=self.now, max_work=0)
        store = self.store
        reclaim_time = self.now + timedelta(seconds=11)

        class LeaseStealingExecutor:
            def execute(self, work_item: dict[str, object]) -> ExecutionResult:
                reclaimed = store.claim_next_work(
                    worker_id="worker-b",
                    now=reclaim_time,
                    lease_seconds=10,
                )
                if reclaimed is None:
                    raise AssertionError("expected the expired lease to recover")
                return ExecutionResult(
                    success=True,
                    summary="Stale worker must not commit this.",
                )

        loop = AutonomousLoop(
            self.store,
            executor=LeaseStealingExecutor(),
            worker_id="worker-a",
            lease_seconds=10,
        )
        with self.assertRaises(LeaseLostError):
            loop.execute_one(now=self.now)
        item = self.store.dashboard_snapshot()["work_items"][0]
        self.assertEqual(item["status"], "claimed")
        self.assertEqual(item["attempt_count"], 2)

    def test_expired_lease_is_recovered_by_another_worker(self) -> None:
        self.add_objective()
        AutonomousLoop(self.store).run_cycle(now=self.now, max_work=0)
        claimed = self.store.claim_next_work(
            worker_id="worker-a",
            now=self.now,
            lease_seconds=10,
        )
        self.assertIsNotNone(claimed)
        status = AutonomousLoop(
            self.store,
            worker_id="worker-b",
            lease_seconds=10,
        ).execute_one(now=self.now + timedelta(seconds=11))
        item = self.store.get_work_item(str(claimed["work_item_id"]))
        self.assertEqual(status, WorkStatus.SIMULATED)
        self.assertEqual(item["status"], "simulated")
        self.assertEqual(item["attempt_count"], 2)
        self.assertEqual(
            self.store.dashboard_snapshot()["audit_records"][0]["record_type"],
            "work.simulated",
        )
        self.assertIn(
            "work.lease_recovered",
            {
                record["record_type"]
                for record in self.store.dashboard_snapshot()["audit_records"]
            },
        )

    def test_final_attempt_lease_expiry_becomes_terminal_failure(self) -> None:
        self.add_objective()
        AutonomousLoop(self.store).run_cycle(now=self.now, max_work=0)
        first = self.store.claim_next_work(
            worker_id="worker-1",
            now=self.now,
            lease_seconds=10,
        )
        self.assertIsNotNone(first)
        second = self.store.claim_next_work(
            worker_id="worker-2",
            now=self.now + timedelta(seconds=11),
            lease_seconds=10,
        )
        self.assertIsNotNone(second)
        third = self.store.claim_next_work(
            worker_id="worker-3",
            now=self.now + timedelta(seconds=22),
            lease_seconds=10,
        )
        self.assertIsNotNone(third)
        fourth = self.store.claim_next_work(
            worker_id="worker-4",
            now=self.now + timedelta(seconds=33),
            lease_seconds=10,
        )
        item = self.store.get_work_item(str(first["work_item_id"]))
        self.assertIsNone(fourth)
        self.assertEqual(item["status"], "failed")
        self.assertEqual(
            item["last_error"],
            "lease expired after final attempt",
        )

    def test_failures_back_off_and_end_after_max_attempts(self) -> None:
        class FailingExecutor:
            def execute(self, work_item: dict[str, object]) -> ExecutionResult:
                return ExecutionResult(
                    success=False,
                    summary="Simulated failure.",
                    error="test failure",
                )

        self.add_objective()
        loop = AutonomousLoop(
            self.store,
            executor=FailingExecutor(),
            worker_id="worker-failing",
            retry_base_seconds=1,
        )
        first = loop.run_cycle(now=self.now)
        self.assertEqual(first.retries_scheduled, 1)
        self.assertIsNone(loop.execute_one(now=self.now))
        self.assertEqual(
            loop.execute_one(now=self.now + timedelta(seconds=1)),
            WorkStatus.READY,
        )
        self.assertEqual(
            loop.execute_one(now=self.now + timedelta(seconds=3)),
            WorkStatus.FAILED,
        )
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(snapshot["work_statuses"], {"failed": 1})
        self.assertEqual(snapshot["work_items"][0]["attempt_count"], 3)

    def test_higher_priority_objective_executes_first(self) -> None:
        self.add_objective(objective_id="objective-low", priority=100)
        self.add_objective(objective_id="objective-high", priority=1)
        report = AutonomousLoop(self.store).run_cycle(
            now=self.now,
            max_work=1,
        )
        snapshot = self.store.dashboard_snapshot()
        status_by_objective = {
            item["objective_id"]: item["status"]
            for item in snapshot["work_items"]
        }
        self.assertEqual(report.work_discovered, 2)
        self.assertEqual(status_by_objective["objective-high"], "simulated")
        self.assertEqual(status_by_objective["objective-low"], "ready")

    def test_dashboard_shows_objectives_work_and_attempt_state(self) -> None:
        self.add_objective()
        AutonomousLoop(self.store).run_cycle(now=self.now)
        html = render_dashboard(self.store.dashboard_snapshot())
        self.assertIn("Business objectives", html)
        self.assertIn("Autonomous work", html)
        self.assertIn("Generate qualified leads.", html)
        self.assertIn("marketing.pipeline.review", html)
        self.assertIn("marketing-agent", html)
        self.assertNotIn("<script", html)

    def test_due_times_are_normalized_across_timezone_offsets(self) -> None:
        pacific = timezone(timedelta(hours=-7))
        self.store.upsert_objective(
            Objective(
                objective_id="objective-offset",
                tenant_id="tenant-1",
                business_id="business-1",
                statement="Generate qualified leads.",
                metric="qualified_leads",
                target=Decimal("25"),
                status=ObjectiveStatus.ACTIVE,
            ),
            next_review_at=self.now.astimezone(pacific),
        )
        due = self.store.list_due_objectives(now=self.now)
        self.assertEqual(
            [record.objective.objective_id for record in due],
            ["objective-offset"],
        )

    def test_objective_identity_cannot_move_across_tenants(self) -> None:
        self.add_objective(objective_id="objective-protected")
        self.store.upsert_tenant(
            Tenant(tenant_id="tenant-2", display_name="Tenant Two")
        )
        self.store.upsert_business(
            Business(
                business_id="business-2",
                tenant_id="tenant-2",
                legal_name="Business Two LLC",
                display_name="Business Two",
                base_currency="USD",
                timezone_name="America/Los_Angeles",
            )
        )
        with self.assertRaisesRegex(ValueError, "cannot move"):
            self.store.upsert_objective(
                Objective(
                    objective_id="objective-protected",
                    tenant_id="tenant-2",
                    business_id="business-2",
                    statement="Different tenant objective.",
                    metric="qualified_leads",
                    target=Decimal("10"),
                    status=ObjectiveStatus.ACTIVE,
                )
            )

    def test_global_cycle_resolves_each_tenants_orchestrator_role(self) -> None:
        self.add_objective(objective_id="objective-tenant-1")
        self.store.upsert_tenant(
            Tenant(tenant_id="tenant-2", display_name="Tenant Two")
        )
        self.store.upsert_business(
            Business(
                business_id="business-2",
                tenant_id="tenant-2",
                legal_name="Business Two LLC",
                display_name="Business Two",
                base_currency="USD",
                timezone_name="America/Los_Angeles",
            )
        )
        self.store.upsert_actor(
            ActorIdentity(
                actor_id="tenant-2-orchestrator",
                tenant_id="tenant-2",
                actor_type=ActorType.AGENT,
                roles=frozenset({"orchestrator"}),
                business_ids=frozenset({"business-2"}),
            )
        )
        self.store.upsert_actor(
            ActorIdentity(
                actor_id="tenant-2-marketing",
                tenant_id="tenant-2",
                actor_type=ActorType.AGENT,
                roles=frozenset({"marketing"}),
                business_ids=frozenset({"business-2"}),
            )
        )
        self.store.upsert_authority_envelope(
            AuthorityEnvelope(
                envelope_id="envelope-2",
                tenant_id="tenant-2",
                business_id="business-2",
                rules=(
                    AuthorityRule(
                        action_type="marketing.pipeline.review",
                        mode=AuthorityMode.AUTO,
                        roles=frozenset({"marketing"}),
                    ),
                ),
                expires_at=self.now + timedelta(days=30),
            )
        )
        self.store.upsert_objective(
            Objective(
                objective_id="objective-tenant-2",
                tenant_id="tenant-2",
                business_id="business-2",
                statement="Generate qualified leads for tenant two.",
                metric="qualified_leads",
                target=Decimal("10"),
                status=ObjectiveStatus.ACTIVE,
            ),
            next_review_at=self.now,
        )
        report = AutonomousLoop(self.store).run_cycle(now=self.now)
        snapshot = self.store.dashboard_snapshot()
        assignee_by_tenant = {
            item["business_id"]: item["assigned_actor_id"]
            for item in snapshot["work_items"]
        }
        self.assertEqual(report.objectives_reviewed, 2)
        self.assertEqual(report.work_simulated, 2)
        self.assertEqual(
            assignee_by_tenant,
            {
                "business-1": "marketing-agent",
                "business-2": "tenant-2-marketing",
            },
        )


if __name__ == "__main__":
    unittest.main()
