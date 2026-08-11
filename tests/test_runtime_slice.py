from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os.contracts import (  # noqa: E402
    ActorIdentity,
    ActorType,
    AuthorityEnvelope,
    AuthorityMode,
    AuthorityRule,
    Business,
    EmergencyStopAction,
    Event,
    Tenant,
)
from agent_os.dashboard import render_dashboard, serve_dashboard  # noqa: E402
from agent_os.runtime import (  # noqa: E402
    AgentRuntime,
    DeterministicAtlasPlanner,
    RunStatus,
)
from agent_os.storage import (  # noqa: E402
    EventIdentityConflict,
    EventProcessingInProgress,
    SQLiteStore,
)


class RuntimeSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "runtime.db")
        self.store.initialize()
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
                actor_id="owner-1",
                tenant_id="tenant-1",
                actor_type=ActorType.HUMAN,
                roles=frozenset({"owner"}),
                business_ids=frozenset({"business-1"}),
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
        self.store.upsert_authority_envelope(
            AuthorityEnvelope(
                envelope_id="envelope-1",
                tenant_id="tenant-1",
                business_id="business-1",
                rules=(
                    AuthorityRule(
                        action_type="portfolio.review",
                        mode=AuthorityMode.AUTO,
                        roles=frozenset({"orchestrator"}),
                    ),
                    AuthorityRule(
                        action_type="experiment.plan",
                        mode=AuthorityMode.NOTIFY,
                        roles=frozenset({"orchestrator"}),
                    ),
                    AuthorityRule(
                        action_type="message.send",
                        mode=AuthorityMode.APPROVE,
                        roles=frozenset({"orchestrator"}),
                    ),
                ),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        self.runtime = AgentRuntime(self.store)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def event(
        self,
        *,
        event_id: str,
        kind: str = "objective.review.requested",
        actor_id: str = "owner-1",
        tenant_id: str = "tenant-1",
        business_id: str = "business-1",
        payload: dict | None = None,
    ) -> Event:
        return Event(
            event_id=event_id,
            tenant_id=tenant_id,
            business_id=business_id,
            source="test",
            actor_id=actor_id,
            kind=kind,
            occurred_at=datetime.now(timezone.utc),
            payload=payload or {},
            idempotency_key=event_id,
        )

    def test_authorized_event_completes_simulation_and_audit(self) -> None:
        result = self.runtime.process(self.event(event_id="evt-1"))
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(result.status, RunStatus.SIMULATED)
        self.assertEqual(result.authority_mode, AuthorityMode.AUTO)
        self.assertEqual(snapshot["counts"]["events"], 1)
        self.assertEqual(snapshot["counts"]["runs"], 1)
        self.assertEqual(snapshot["counts"]["audit_records"], 1)
        self.assertEqual(
            snapshot["audit_records"][0]["record_type"], "execution.simulated"
        )

    def test_emergency_stop_rejects_event_before_planning(self) -> None:
        self.store.upsert_actor(
            ActorIdentity(
                actor_id="owner-1",
                tenant_id="tenant-1",
                actor_type=ActorType.HUMAN,
                roles=frozenset({"owner", "business-owner"}),
                business_ids=frozenset({"business-1"}),
            )
        )
        self.store.record_emergency_stop(
            event_id="runtime-stop",
            tenant_id="tenant-1",
            business_id="business-1",
            actor_id="owner-1",
            action=EmergencyStopAction.ACTIVATED,
            reason="Pause all runtime work.",
            now=datetime.now(timezone.utc),
        )
        result = self.runtime.process(self.event(event_id="evt-stopped"))
        self.assertEqual(result.status, RunStatus.REJECTED)
        self.assertEqual(result.authority_mode, AuthorityMode.FORBIDDEN)
        self.assertEqual(
            self.store.dashboard_snapshot()["audit_records"][0]["record_type"],
            "emergency_stop.blocked",
        )

    def test_duplicate_delivery_returns_original_run(self) -> None:
        event = self.event(event_id="evt-duplicate")
        first = self.runtime.process(event)
        second = self.runtime.process(event)
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(first.run_id, second.run_id)
        self.assertTrue(second.duplicate)
        self.assertEqual(snapshot["counts"]["events"], 1)
        self.assertEqual(snapshot["counts"]["runs"], 1)
        self.assertEqual(snapshot["counts"]["audit_records"], 1)

    def test_idempotency_key_deduplicates_a_different_event_id(self) -> None:
        original = self.event(event_id="evt-original")
        retry = Event(
            event_id="evt-retry",
            tenant_id=original.tenant_id,
            business_id=original.business_id,
            source=original.source,
            actor_id=original.actor_id,
            kind=original.kind,
            occurred_at=original.occurred_at,
            payload=original.payload,
            idempotency_key=original.idempotency_key,
        )
        first = self.runtime.process(original)
        second = self.runtime.process(retry)
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(second.event_id, original.event_id)
        self.assertTrue(second.duplicate)
        self.assertEqual(snapshot["counts"]["events"], 1)

    def test_idempotency_key_reuse_with_different_content_is_rejected(
        self,
    ) -> None:
        original = self.event(event_id="evt-original-content")
        self.runtime.process(original)
        conflicting = Event(
            event_id="evt-conflicting-content",
            tenant_id=original.tenant_id,
            business_id=original.business_id,
            source=original.source,
            actor_id=original.actor_id,
            kind="metric.threshold_breached",
            occurred_at=original.occurred_at,
            payload={"different": True},
            idempotency_key=original.idempotency_key,
        )
        with self.assertRaisesRegex(
            EventIdentityConflict,
            "different content",
        ):
            self.runtime.process(conflicting)
        self.assertEqual(self.store.dashboard_snapshot()["counts"]["runs"], 1)

    def test_concurrent_delivery_has_one_processing_owner(self) -> None:
        started = threading.Event()
        release = threading.Event()
        planner_calls: list[str] = []

        class BlockingPlanner:
            def plan(self, event: Event):
                planner_calls.append(event.event_id)
                started.set()
                if not release.wait(timeout=2):
                    raise AssertionError("test planner release timed out")
                return DeterministicAtlasPlanner().plan(event)

        event = self.event(event_id="evt-concurrent")
        first_result: list[object] = []
        first_error: list[BaseException] = []

        def process_first() -> None:
            try:
                first_result.append(
                    AgentRuntime(
                        self.store,
                        planner=BlockingPlanner(),
                        worker_id="event-worker-first",
                    ).process(event)
                )
            except BaseException as error:
                first_error.append(error)

        thread = threading.Thread(target=process_first)
        thread.start()
        self.assertTrue(started.wait(timeout=2))
        with self.assertRaises(EventProcessingInProgress):
            AgentRuntime(
                self.store,
                worker_id="event-worker-second",
            ).process(event)
        release.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(first_error, [])
        self.assertEqual(len(first_result), 1)
        self.assertEqual(planner_calls, ["evt-concurrent"])
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(snapshot["counts"]["events"], 1)
        self.assertEqual(snapshot["counts"]["runs"], 1)

    def test_existing_receipt_without_run_is_recovered(self) -> None:
        event = self.event(event_id="evt-interrupted")
        self.store.insert_event(event)
        result = self.runtime.process(event)
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(result.status, RunStatus.SIMULATED)
        self.assertEqual(snapshot["counts"]["events"], 1)
        self.assertEqual(snapshot["counts"]["runs"], 1)
        self.assertEqual(snapshot["counts"]["audit_records"], 1)

    def test_event_id_cannot_cross_an_identity_boundary(self) -> None:
        original = self.event(event_id="evt-protected")
        self.runtime.process(original)
        hostile_retry = self.event(
            event_id=original.event_id,
            tenant_id="tenant-2",
            business_id="business-2",
        )
        with self.assertRaises(EventIdentityConflict):
            self.runtime.process(hostile_retry)

    def test_run_and_audit_commit_atomically(self) -> None:
        event = self.event(event_id="evt-atomic")
        claim = self.store.claim_event_processing(
            event,
            worker_id="worker-atomic",
            now=datetime.now(timezone.utc),
        )
        self.assertTrue(claim.claimed)
        with self.assertRaises(TypeError):
            self.store.record_outcome(
                run_id="run-atomic",
                event_id=event.event_id,
                tenant_id=event.tenant_id,
                business_id=event.business_id,
                action_type="portfolio.review",
                authority_mode="auto",
                status="simulated",
                summary="Must roll back.",
                audit_id="audit-atomic",
                audit_type="execution.simulated",
                audit_details={"not_json": object()},
                processing_worker_id="worker-atomic",
            )
        self.assertIsNone(self.store.get_run_for_event(event.event_id))

    def test_terminal_outcome_requires_the_live_processing_lease(self) -> None:
        event = self.event(event_id="evt-no-lease")
        self.store.insert_event(event)
        with self.assertRaises(EventProcessingInProgress):
            self.store.record_outcome(
                run_id="run-no-lease",
                event_id=event.event_id,
                tenant_id=event.tenant_id,
                business_id=event.business_id,
                action_type="portfolio.review",
                authority_mode="auto",
                status="simulated",
                summary="Must not persist.",
                audit_id="audit-no-lease",
                audit_type="execution.simulated",
                audit_details={},
                processing_worker_id="worker-without-claim",
            )
        self.assertIsNone(self.store.get_run_for_event(event.event_id))

    def test_unregistered_action_actor_is_rejected(self) -> None:
        class UnknownActorPlanner:
            def plan(self, event: Event):
                from agent_os.contracts import ActionRequest
                from agent_os.runtime import AtlasPlan

                return AtlasPlan(
                    action=ActionRequest(
                        action_type="portfolio.review",
                        tenant_id=event.tenant_id,
                        business_id=event.business_id,
                        actor_id="unregistered-agent",
                    ),
                    summary="Unknown agent proposed work.",
                )

        result = AgentRuntime(
            self.store, planner=UnknownActorPlanner()
        ).process(self.event(event_id="evt-unknown-action-actor"))
        self.assertEqual(result.status, RunStatus.REJECTED)
        self.assertIn("Action actor", result.summary)

    def test_unknown_actor_is_rejected_before_planning(self) -> None:
        result = self.runtime.process(
            self.event(event_id="evt-unknown-actor", actor_id="unknown")
        )
        self.assertEqual(result.status, RunStatus.REJECTED)
        self.assertEqual(result.authority_mode, AuthorityMode.FORBIDDEN)
        self.assertIn("actor", result.summary)

    def test_cross_tenant_event_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the tenant"):
            self.runtime.process(
                self.event(
                    event_id="evt-cross-tenant",
                    tenant_id="tenant-2",
                )
            )

    def test_unknown_action_defaults_forbidden(self) -> None:
        result = self.runtime.process(
            self.event(
                event_id="evt-bank",
                kind="command.received",
                payload={"requested_action": "bank.transfer"},
            )
        )
        self.assertEqual(result.status, RunStatus.REJECTED)
        self.assertEqual(result.authority_mode, AuthorityMode.FORBIDDEN)

    def test_approval_action_enters_hold(self) -> None:
        result = self.runtime.process(
            self.event(
                event_id="evt-message",
                kind="command.received",
                payload={"requested_action": "message.send"},
            )
        )
        self.assertEqual(result.status, RunStatus.AWAITING_APPROVAL)
        self.assertEqual(result.authority_mode, AuthorityMode.APPROVE)

    def test_dashboard_renders_durable_state(self) -> None:
        self.runtime.process(self.event(event_id="evt-dashboard"))
        html = render_dashboard(self.store.dashboard_snapshot())
        self.assertIn("Agent OS v2 Runtime", html)
        self.assertIn("portfolio.review", html)
        self.assertIn("execution.simulated", html)
        self.assertNotIn("<script", html)

    def test_dashboard_refuses_non_loopback_binding(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback-only"):
            serve_dashboard(self.store, host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()
