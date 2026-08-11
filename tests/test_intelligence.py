from datetime import datetime, timedelta, timezone
from contextlib import closing
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os.autonomy import AutonomousLoop  # noqa: E402
from agent_os.contracts import (  # noqa: E402
    ActorIdentity,
    ActorType,
    AuthorityEnvelope,
    AuthorityMode,
    AuthorityRule,
    Business,
    MemoryRecord,
    MemoryType,
    Objective,
    ObjectiveStatus,
    Tenant,
    VerificationStatus,
)
from agent_os.dashboard import render_dashboard  # noqa: E402
from agent_os.intelligence import (  # noqa: E402
    BoundedPlanEvaluator,
    EvaluationDecision,
    IntelligenceRuntime,
    Playbook,
    PlaybookPlanner,
)
from agent_os.storage import SQLiteStore  # noqa: E402


class IntelligenceRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "intelligence.db")
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
                actor_id="growth-agent",
                tenant_id="tenant-1",
                actor_type=ActorType.AGENT,
                roles=frozenset({"marketing"}),
                business_ids=frozenset({"business-1"}),
            )
        )
        self.store.upsert_objective(
            Objective(
                objective_id="objective-leads",
                tenant_id="tenant-1",
                business_id="business-1",
                statement="Generate qualified leads.",
                metric="qualified_leads",
                target=Decimal("25"),
                status=ObjectiveStatus.ACTIVE,
                review_interval_seconds=3600,
            ),
            next_review_at=self.now,
        )
        self.playbook_path = (
            ROOT / "packs" / "northwind" / "qualified-lead-growth.json"
        )
        raw = json.loads(self.playbook_path.read_text())
        capability = raw["capability"]
        self.store.upsert_capability(
            capability_id=capability["capability_id"],
            display_name=capability["display_name"],
            description=capability["description"],
            required_role=capability["required_role"],
            action_types=tuple(capability["action_types"]),
        )
        self.store.assign_capability(
            tenant_id="tenant-1",
            business_id="business-1",
            actor_id="growth-agent",
            capability_id=capability["capability_id"],
        )
        self.set_authority(AuthorityMode.AUTO)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def set_authority(self, mode: AuthorityMode | None) -> None:
        rules = ()
        if mode is not None:
            rules = (
                AuthorityRule(
                    action_type="growth.funnel.diagnose",
                    mode=mode,
                    capability_ids=frozenset(
                        {"growth.qualified-lead-planning"}
                    ),
                ),
                AuthorityRule(
                    action_type="growth.experiment.design",
                    mode=mode,
                    capability_ids=frozenset(
                        {"growth.qualified-lead-planning"}
                    ),
                ),
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

    def add_evidence(
        self,
        *,
        evidence_id: str = "evidence-1",
        confidence: Decimal = Decimal("0.90"),
    ) -> None:
        self.store.insert_evidence(
            evidence_id=evidence_id,
            tenant_id="tenant-1",
            business_id="business-1",
            source_type="analytics_snapshot",
            source_ref="fixture:lead-funnel",
            statement="The observed visitor-to-lead conversion rate is 0.4%.",
            facts={"conversion_rate": "0.4%"},
            confidence=confidence,
            observed_at=self.now,
        )

    def test_accepted_plan_creates_bounded_work_and_candidate_memory(self) -> None:
        self.add_evidence()
        result = IntelligenceRuntime(self.store).plan_objective(
            objective_id="objective-leads",
            actor_id="growth-agent",
            evidence_ids=("evidence-1",),
            playbook=Playbook.from_path(self.playbook_path),
            now=self.now,
        )
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(result.decision, EvaluationDecision.ACCEPTED)
        self.assertEqual(result.work_items_created, 2)
        self.assertIsNotNone(result.candidate_memory_id)
        self.assertEqual(snapshot["counts"]["plans"], 1)
        self.assertEqual(snapshot["counts"]["work_items"], 2)
        self.assertEqual(snapshot["counts"]["memories"], 1)
        self.assertEqual(
            snapshot["memories"][0]["verification_status"],
            "candidate",
        )

    def test_worker_executes_work_derived_from_an_accepted_plan(self) -> None:
        self.add_evidence()
        IntelligenceRuntime(self.store).plan_objective(
            objective_id="objective-leads",
            actor_id="growth-agent",
            evidence_ids=("evidence-1",),
            playbook=Playbook.from_path(self.playbook_path),
            now=self.now,
        )
        report = AutonomousLoop(
            self.store,
            worker_id="bounded-worker",
        ).run_cycle(now=self.now)
        self.assertEqual(report.work_simulated, 2)
        self.assertEqual(
            self.store.dashboard_snapshot()["work_statuses"],
            {"simulated": 2},
        )

    def test_approval_plan_materializes_durable_requests_not_ready_work(
        self,
    ) -> None:
        self.set_authority(AuthorityMode.APPROVE)
        self.add_evidence()
        result = IntelligenceRuntime(self.store).plan_objective(
            objective_id="objective-leads",
            actor_id="growth-agent",
            evidence_ids=("evidence-1",),
            playbook=Playbook.from_path(self.playbook_path),
            now=self.now,
        )
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(result.work_items_created, 2)
        self.assertEqual(snapshot["work_statuses"], {"awaiting_approval": 2})
        self.assertEqual(snapshot["counts"]["approval_requests"], 2)
        self.assertEqual(len(snapshot["approvals"]), 2)
        self.assertIsNone(
            AutonomousLoop(self.store).execute_one(now=self.now)
        )

    def test_materialization_serializes_current_authority_with_commit(
        self,
    ) -> None:
        original = self.store._decide_authority_in_connection
        probe = {"attempted": False, "blocked": False}

        def authority_with_concurrent_revoke(connection, request, *, now):
            result = original(connection, request, now=now)
            if connection.in_transaction and not probe["attempted"]:
                probe["attempted"] = True
                with closing(
                    sqlite3.connect(self.store.path, timeout=0.01)
                ) as adversary:
                    with self.assertRaisesRegex(
                        sqlite3.OperationalError,
                        "locked",
                    ):
                        adversary.execute(
                            """
                            UPDATE authority_envelopes
                            SET rules_json = '[]'
                            WHERE tenant_id = 'tenant-1'
                              AND business_id = 'business-1'
                            """
                        )
                    probe["blocked"] = True
            return result

        self.store._decide_authority_in_connection = (
            authority_with_concurrent_revoke
        )
        self.add_evidence()
        result = IntelligenceRuntime(self.store).plan_objective(
            objective_id="objective-leads",
            actor_id="growth-agent",
            evidence_ids=("evidence-1",),
            playbook=Playbook.from_path(self.playbook_path),
            now=self.now,
        )
        self.assertTrue(probe["attempted"])
        self.assertTrue(probe["blocked"])
        self.assertEqual(result.work_items_created, 2)

    def test_low_confidence_evidence_rejects_plan_and_creates_no_work(self) -> None:
        self.add_evidence(confidence=Decimal("0.20"))
        result = IntelligenceRuntime(self.store).plan_objective(
            objective_id="objective-leads",
            actor_id="growth-agent",
            evidence_ids=("evidence-1",),
            playbook=Playbook.from_path(self.playbook_path),
            now=self.now,
        )
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(result.decision, EvaluationDecision.REJECTED)
        self.assertIn("evidence confidence", " ".join(result.reasons))
        self.assertEqual(snapshot["counts"]["work_items"], 0)
        self.assertEqual(snapshot["counts"]["memories"], 0)

    def test_forbidden_step_rejects_the_entire_plan(self) -> None:
        self.add_evidence()
        self.set_authority(None)
        result = IntelligenceRuntime(self.store).plan_objective(
            objective_id="objective-leads",
            actor_id="growth-agent",
            evidence_ids=("evidence-1",),
            playbook=Playbook.from_path(self.playbook_path),
            now=self.now,
        )
        self.assertEqual(result.decision, EvaluationDecision.REJECTED)
        self.assertEqual(result.work_items_created, 0)

    def test_rejected_plan_cannot_be_materialized_directly(self) -> None:
        self.add_evidence(confidence=Decimal("0.20"))
        result = IntelligenceRuntime(self.store).plan_objective(
            objective_id="objective-leads",
            actor_id="growth-agent",
            evidence_ids=("evidence-1",),
            playbook=Playbook.from_path(self.playbook_path),
            now=self.now,
        )
        plan = self.store.dashboard_snapshot()["plans"][0]
        memory = MemoryRecord(
            memory_id="memory-forged",
            tenant_id="tenant-1",
            business_id="business-1",
            memory_type=MemoryType.SEMANTIC,
            statement="Forged rejected-plan memory.",
            source_type="structured_plan",
            source_ref=result.plan_id,
            confidence=Decimal("0.90"),
            verification_status=VerificationStatus.CANDIDATE,
            created_at=self.now,
            observed_at=self.now,
        )
        with self.assertRaisesRegex(ValueError, "accepted"):
            self.store.materialize_plan(
                plan_id=result.plan_id,
                plan_hash=plan["plan_hash"],
                objective_id="objective-leads",
                tenant_id="tenant-1",
                business_id="business-1",
                work_items=(
                    {
                        "work_item_id": "work-forged",
                        "title": "Forged work",
                        "rationale": "Bypass evaluation.",
                        "action_type": "growth.funnel.diagnose",
                        "assigned_actor_id": "growth-agent",
                        "attributes": {},
                        "authority_mode": "auto",
                        "status": "ready",
                        "priority_score": 100,
                        "max_attempts": 3,
                        "audit_id": "audit-forged",
                    },
                ),
                next_review_at=self.now + timedelta(hours=1),
                memory=memory,
                now=self.now,
            )
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(snapshot["counts"]["work_items"], 0)
        self.assertEqual(snapshot["counts"]["memories"], 0)

    def test_evaluation_replay_is_deterministic(self) -> None:
        self.add_evidence()
        playbook = Playbook.from_path(self.playbook_path)
        objective = self.store.get_objective("objective-leads").objective
        evidence = self.store.get_evidence(("evidence-1",))
        plan = PlaybookPlanner().plan(
            objective=objective,
            actor_id="growth-agent",
            evidence=evidence,
            playbook=playbook,
        )
        evaluator = BoundedPlanEvaluator(self.store)
        first = evaluator.evaluate(plan, now=self.now)
        second = evaluator.evaluate(plan, now=self.now)
        self.assertEqual(first, second)
        self.assertEqual(first.digest(), second.digest())
        self.assertEqual(plan.digest(), plan.digest())

    def test_actor_without_assigned_capability_is_rejected(self) -> None:
        self.add_evidence()
        self.store.upsert_actor(
            ActorIdentity(
                actor_id="unassigned-marketer",
                tenant_id="tenant-1",
                actor_type=ActorType.AGENT,
                roles=frozenset({"marketing"}),
                business_ids=frozenset({"business-1"}),
            )
        )
        result = IntelligenceRuntime(self.store).plan_objective(
            objective_id="objective-leads",
            actor_id="unassigned-marketer",
            evidence_ids=("evidence-1",),
            playbook=Playbook.from_path(self.playbook_path),
            now=self.now,
        )
        self.assertEqual(result.decision, EvaluationDecision.REJECTED)
        self.assertIn("does not hold", " ".join(result.reasons))

    def test_cross_tenant_evidence_rejects_plan(self) -> None:
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
        self.store.insert_evidence(
            evidence_id="evidence-other-tenant",
            tenant_id="tenant-2",
            business_id="business-2",
            source_type="analytics_snapshot",
            source_ref="other-tenant",
            statement="Evidence belonging to a different tenant.",
            facts={"conversion_rate": "9.9%"},
            confidence=Decimal("0.90"),
            observed_at=self.now,
        )
        result = IntelligenceRuntime(self.store).plan_objective(
            objective_id="objective-leads",
            actor_id="growth-agent",
            evidence_ids=("evidence-other-tenant",),
            playbook=Playbook.from_path(self.playbook_path),
            now=self.now,
        )
        self.assertEqual(result.decision, EvaluationDecision.REJECTED)
        self.assertIn("identity boundary", " ".join(result.reasons))
        self.assertEqual(result.work_items_created, 0)

    def test_runtime_cannot_self_promote_memory_to_verified(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot self-promote"):
            self.store.insert_memory(
                MemoryRecord(
                    memory_id="memory-unearned",
                    tenant_id="tenant-1",
                    business_id="business-1",
                    memory_type=MemoryType.SEMANTIC,
                    statement="An unearned conclusion.",
                    source_type="test",
                    source_ref="test",
                    confidence=Decimal("0.90"),
                    verification_status=VerificationStatus.VERIFIED,
                    created_at=self.now,
                    observed_at=self.now,
                )
            )

    def test_plan_materialization_rolls_back_all_derived_state(self) -> None:
        self.add_evidence()
        plan_payload = {
            "tenant_id": "tenant-1",
            "business_id": "business-1",
            "objective_id": "objective-leads",
            "capability_id": "growth.qualified-lead-planning",
            "planner_id": "test",
            "hypothesis": "Atomic candidate.",
            "expected_metric": "qualified_leads",
            "evidence_refs": ("evidence-1",),
            "steps": (
                {
                    "title": "First",
                    "rationale": "Must roll back.",
                    "action_type": "growth.funnel.diagnose",
                    "assigned_actor_id": "growth-agent",
                    "expected_output": "First output",
                },
                {
                    "title": "Second",
                    "rationale": "Must also roll back.",
                    "action_type": "growth.experiment.design",
                    "assigned_actor_id": "growth-agent",
                    "expected_output": "Second output",
                },
            ),
        }
        plan_hash = hashlib.sha256(
            json.dumps(
                plan_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        evaluation_hash = hashlib.sha256(
            json.dumps(
                {
                    "authority_modes": ("auto", "auto"),
                    "decision": "accepted",
                    "reasons": (),
                    "score": 100,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        self.store.record_plan_and_evaluation(
            plan_id="plan-atomic",
            tenant_id="tenant-1",
            business_id="business-1",
            objective_id="objective-leads",
            capability_id="growth.qualified-lead-planning",
            planner_id="test",
            plan=plan_payload,
            plan_hash=plan_hash,
            status="accepted",
            evaluation_id="evaluation-atomic",
            evaluator_version="test",
            decision="accepted",
            score=100,
            reasons=(),
            authority_modes=("auto", "auto"),
            evaluation_hash=evaluation_hash,
            created_at=self.now,
        )
        valid_item = {
            "work_item_id": "work-atomic-1",
            "title": "First",
            "rationale": "Must roll back.",
            "action_type": "growth.funnel.diagnose",
            "assigned_actor_id": "growth-agent",
            "attributes": {
                "expected_output": "First output",
                "plan_id": "plan-atomic",
            },
            "authority_mode": "auto",
            "status": "ready",
            "priority_score": 100,
            "max_attempts": 3,
            "audit_id": "audit-atomic-1",
        }
        invalid_item = {
            **valid_item,
            "work_item_id": "work-atomic-2",
            "title": "Second",
            "rationale": "Must also roll back.",
            "action_type": "growth.experiment.design",
            "attributes": {
                "expected_output": "Second output",
                "plan_id": "plan-atomic",
                "not_json": object(),
            },
            "audit_id": "audit-atomic-2",
        }
        memory = MemoryRecord(
            memory_id="memory-atomic",
            tenant_id="tenant-1",
            business_id="business-1",
            memory_type=MemoryType.SEMANTIC,
            statement="Atomic candidate.",
            source_type="structured_plan",
            source_ref="plan-atomic",
            confidence=Decimal("0.60"),
            verification_status=VerificationStatus.CANDIDATE,
            evidence_refs=("evidence-1",),
            created_at=self.now,
            observed_at=self.now,
        )
        with self.assertRaises(TypeError):
            self.store.materialize_plan(
                plan_id="plan-atomic",
                plan_hash=plan_hash,
                objective_id="objective-leads",
                tenant_id="tenant-1",
                business_id="business-1",
                work_items=(valid_item, invalid_item),
                next_review_at=self.now + timedelta(hours=1),
                memory=memory,
                now=self.now,
            )
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(snapshot["counts"]["work_items"], 0)
        self.assertEqual(snapshot["counts"]["memories"], 0)
        self.assertEqual(snapshot["plans"][0]["status"], "accepted")
        valid_second_item = {
            **invalid_item,
            "attributes": {
                "expected_output": "Second output",
                "plan_id": "plan-atomic",
            },
        }
        self.set_authority(None)
        with self.assertRaisesRegex(ValueError, "authority is stale"):
            self.store.materialize_plan(
                plan_id="plan-atomic",
                plan_hash=plan_hash,
                objective_id="objective-leads",
                tenant_id="tenant-1",
                business_id="business-1",
                work_items=(valid_item, valid_second_item),
                next_review_at=self.now + timedelta(hours=1),
                memory=memory,
                now=self.now,
            )
        self.set_authority(AuthorityMode.AUTO)
        connection = sqlite3.connect(self.store.path)
        try:
            connection.execute(
                "DROP TRIGGER prevent_plan_evaluations_update"
            )
            connection.execute(
                """
                UPDATE plan_evaluations
                SET evaluation_hash = 'attacker-controlled'
                WHERE evaluation_id = 'evaluation-atomic'
                """
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, "evaluation is forged"):
            self.store.materialize_plan(
                plan_id="plan-atomic",
                plan_hash=plan_hash,
                objective_id="objective-leads",
                tenant_id="tenant-1",
                business_id="business-1",
                work_items=(valid_item, valid_second_item),
                next_review_at=self.now + timedelta(hours=1),
                memory=memory,
                now=self.now,
            )
        connection = sqlite3.connect(self.store.path)
        try:
            connection.execute(
                """
                UPDATE plan_evaluations
                SET evaluation_hash = ?
                WHERE evaluation_id = 'evaluation-atomic'
                """,
                (evaluation_hash,),
            )
            connection.execute("DROP TRIGGER restrict_structured_plan_update")
            connection.execute(
                """
                UPDATE structured_plans
                SET plan_json = ?
                WHERE plan_id = 'plan-atomic'
                """,
                (
                    json.dumps(
                        {
                            **plan_payload,
                            "hypothesis": "Attacker-controlled plan.",
                        },
                        sort_keys=True,
                    ),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, "durable plan content"):
            self.store.materialize_plan(
                plan_id="plan-atomic",
                plan_hash=plan_hash,
                objective_id="objective-leads",
                tenant_id="tenant-1",
                business_id="business-1",
                work_items=(valid_item, valid_second_item),
                next_review_at=self.now + timedelta(hours=1),
                memory=memory,
                now=self.now,
            )

    def test_dashboard_exposes_plan_evidence_and_learning_state(self) -> None:
        self.add_evidence()
        IntelligenceRuntime(self.store).plan_objective(
            objective_id="objective-leads",
            actor_id="growth-agent",
            evidence_ids=("evidence-1",),
            playbook=Playbook.from_path(self.playbook_path),
            now=self.now,
        )
        html = render_dashboard(self.store.dashboard_snapshot())
        self.assertIn("Evidence", html)
        self.assertIn("Plan evaluations", html)
        self.assertIn("Candidate memory", html)
        self.assertIn("analytics_snapshot", html)
        self.assertIn("accepted", html)


if __name__ == "__main__":
    unittest.main()
