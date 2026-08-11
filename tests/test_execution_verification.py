from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from contextlib import closing
import json
import sqlite3
import sys
import tempfile
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
    Objective,
    ObjectiveStatus,
    Tenant,
)
from agent_os.storage import SQLiteStore  # noqa: E402
from agent_os.dashboard import render_dashboard  # noqa: E402
from agent_os.verification import (  # noqa: E402
    EvidenceKind,
    EvidenceReceipt,
    ExecutionAttempt,
    ExecutionMode,
    ExecutionTruthError,
    IndependentVerificationError,
    OutcomeVerificationService,
    StaleStateError,
    UnverifiedOutcomeError,
    VerificationDecision,
    VerificationRequest,
)


class ExecutionVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "verification.db")
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
                actor_id="producer",
                tenant_id="tenant-1",
                actor_type=ActorType.AGENT,
                roles=frozenset({"marketing"}),
                business_ids=frozenset({"business-1"}),
            )
        )
        self.store.upsert_actor(
            ActorIdentity(
                actor_id="qa",
                tenant_id="tenant-1",
                actor_type=ActorType.AGENT,
                roles=frozenset({"qa"}),
                business_ids=frozenset({"business-1"}),
            )
        )
        self.store.upsert_authority_envelope(
            AuthorityEnvelope(
                envelope_id="verification-authority",
                tenant_id="tenant-1",
                business_id="business-1",
                rules=(
                    AuthorityRule(
                        action_type="record.update",
                        mode=AuthorityMode.AUTO,
                        roles=frozenset({"marketing"}),
                    ),
                ),
                expires_at=self.now + timedelta(days=30),
            )
        )
        self.store.upsert_objective(
            Objective(
                objective_id="objective-1",
                tenant_id="tenant-1",
                business_id="business-1",
                statement="Keep the external record accurate.",
                metric="record_accuracy",
                target=Decimal("1"),
                status=ObjectiveStatus.ACTIVE,
            ),
            next_review_at=self.now,
        )
        self.store.enqueue_work_item(
            work_item_id="work-1",
            work_key="verification:work-1",
            objective_id="objective-1",
            tenant_id="tenant-1",
            business_id="business-1",
            title="Update one external record",
            rationale="Exercise the execution truth boundary.",
            action_type="record.update",
            assigned_actor_id="producer",
            platform="test-system",
            account_id="account-1",
            amount=None,
            currency=None,
            attributes={"record_id": "record-1"},
            authority_mode="auto",
            status="ready",
            priority_score=100,
            max_attempts=3,
            available_at=self.now,
            next_review_at=self.now + timedelta(hours=1),
            audit_id="audit-work-discovered",
        )
        self.worker_id = "worker-1"
        claimed = self.store.claim_next_work(
            worker_id=self.worker_id,
            now=self.now,
            lease_seconds=60,
        )
        self.assertIsNotNone(claimed)
        self.service = OutcomeVerificationService(self.store)
        for kind in (
            EvidenceKind.PRECONDITION,
            EvidenceKind.EXTERNAL_READBACK,
            EvidenceKind.MACHINE_CHECK,
        ):
            self.service.register_evidence_issuer(
                tenant_id="tenant-1",
                business_id="business-1",
                source_system="test-system",
                evidence_kind=kind,
                actor_id="producer",
                issuer_version="test-adapter/v1",
            )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def precondition(
        self,
        *,
        valid_until: datetime | None = None,
        payload: dict[str, object] | None = None,
    ) -> EvidenceReceipt:
        return EvidenceReceipt(
            receipt_id="receipt-before",
            work_item_id="work-1",
            tenant_id="tenant-1",
            business_id="business-1",
            evidence_kind=EvidenceKind.PRECONDITION,
            source_system="test-system",
            source_ref="record:record-1",
            captured_by="producer",
            observed_at=self.now,
            valid_until=valid_until or self.now + timedelta(seconds=30),
            payload=payload or {"version": 1, "status": "old"},
            created_at=self.now,
            issuer_version="test-adapter/v1",
        )

    def attempt(
        self,
        *,
        idempotency_key: str = "record-1:update:1",
    ) -> ExecutionAttempt:
        return ExecutionAttempt(
            attempt_id="attempt-1",
            work_item_id="work-1",
            tenant_id="tenant-1",
            business_id="business-1",
            producer_id="producer",
            execution_mode=ExecutionMode.EXTERNAL,
            action_type="record.update",
            target_ref="record:record-1",
            idempotency_key=idempotency_key,
            precondition_receipt_id="receipt-before",
            summary="Attempted to update record-1 to active.",
            attempted_at=self.now + timedelta(seconds=1),
        )

    def readback(
        self,
        *,
        receipt_id: str = "receipt-after",
        evidence_kind: EvidenceKind = EvidenceKind.EXTERNAL_READBACK,
        source_system: str = "test-system",
        source_ref: str = "record:record-1",
        observed_at: datetime | None = None,
        valid_until: datetime | None = None,
        payload: dict[str, object] | None = None,
    ) -> EvidenceReceipt:
        observed = observed_at or self.now + timedelta(seconds=2)
        return EvidenceReceipt(
            receipt_id=receipt_id,
            work_item_id="work-1",
            attempt_id="attempt-1",
            tenant_id="tenant-1",
            business_id="business-1",
            evidence_kind=evidence_kind,
            source_system=source_system,
            source_ref=source_ref,
            captured_by="producer",
            observed_at=observed,
            valid_until=valid_until or self.now + timedelta(seconds=30),
            payload=payload or {"version": 2, "status": "active"},
            created_at=observed,
            issuer_version=(
                "test-adapter/v1"
                if evidence_kind
                in {
                    EvidenceKind.EXTERNAL_READBACK,
                    EvidenceKind.MACHINE_CHECK,
                }
                else None
            ),
        )

    def verification(
        self,
        *,
        verifier_id: str = "qa",
        expected_facts: dict[str, object] | None = None,
        decided_at: datetime | None = None,
        evidence_receipt_ids: tuple[str, ...] = ("receipt-after",),
    ) -> VerificationRequest:
        return VerificationRequest(
            verification_id="verification-1",
            attempt_id="attempt-1",
            verifier_id=verifier_id,
            decision=VerificationDecision.VERIFIED,
            evidence_receipt_ids=evidence_receipt_ids,
            expected_facts=expected_facts or {
                "version": 2,
                "status": "active",
            },
            rationale="Independent read-back matches the expected result.",
            policy_version="outcome-verification/v1",
            decided_at=decided_at or self.now + timedelta(seconds=3),
        )

    def begin_valid_attempt(self) -> None:
        self.assertTrue(
            self.service.capture_precondition(self.precondition())
        )
        self.assertTrue(
            self.service.begin_attempt(
                self.attempt(),
                worker_id=self.worker_id,
            )
        )

    def test_external_attempt_observation_and_verification_are_distinct(self) -> None:
        self.begin_valid_attempt()
        self.assertEqual(
            self.store.get_work_item("work-1")["status"],
            "attempted",
        )
        self.assertTrue(self.service.attach_evidence(self.readback()))
        self.assertEqual(
            self.store.get_work_item("work-1")["status"],
            "observed",
        )
        self.assertTrue(self.service.verify(self.verification()))
        claim = self.service.completion_claim("work-1")
        self.assertEqual(
            claim.verification_state,
            VerificationDecision.VERIFIED,
        )
        self.assertEqual(claim.verifier_id, "qa")
        self.assertEqual(claim.evidence_refs, ("receipt-after",))
        self.assertEqual(
            claim.observed_result,
            {"version": 2, "status": "active"},
        )
        self.assertEqual(
            self.store.get_work_item("work-1")["status"],
            "verified",
        )

    def test_stale_precondition_blocks_external_attempt(self) -> None:
        stale = self.precondition(
            valid_until=self.now + timedelta(milliseconds=500)
        )
        self.service.capture_precondition(stale)
        with self.assertRaises(StaleStateError):
            self.service.begin_attempt(
                self.attempt(),
                worker_id=self.worker_id,
            )
        self.assertEqual(
            self.store.get_work_item("work-1")["status"],
            "claimed",
        )

    def test_dashboard_distinguishes_attempts_receipts_and_verifications(
        self,
    ) -> None:
        self.begin_valid_attempt()
        self.service.attach_evidence(self.readback())
        self.service.verify(self.verification())
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(snapshot["counts"]["execution_attempts"], 1)
        self.assertEqual(snapshot["counts"]["evidence_receipts"], 2)
        self.assertEqual(snapshot["counts"]["outcome_verifications"], 1)
        html = render_dashboard(snapshot)
        self.assertIn("Execution attempts", html)
        self.assertIn("Evidence receipts", html)
        self.assertIn("Outcome verifications", html)
        self.assertIn("attempted work is not completion", html.lower())
        self.assertNotIn("<script", html)

    def test_pre_attempt_readback_cannot_be_observed_result(self) -> None:
        self.begin_valid_attempt()
        with self.assertRaises(StaleStateError):
            self.service.attach_evidence(
                self.readback(observed_at=self.now)
            )
        self.assertEqual(
            self.store.get_work_item("work-1")["status"],
            "attempted",
        )

    def test_stale_readback_cannot_support_verification(self) -> None:
        self.begin_valid_attempt()
        self.service.attach_evidence(
            self.readback(
                valid_until=self.now + timedelta(seconds=3)
            )
        )
        with self.assertRaises(StaleStateError):
            self.service.verify(
                self.verification(
                    decided_at=self.now + timedelta(seconds=3)
                )
            )
        self.assertEqual(
            self.store.get_work_item("work-1")["status"],
            "observed",
        )

    def test_future_readback_cannot_support_earlier_verification(self) -> None:
        self.begin_valid_attempt()
        self.service.attach_evidence(
            self.readback(
                observed_at=self.now + timedelta(seconds=4)
            )
        )
        with self.assertRaises(StaleStateError):
            self.service.verify(
                self.verification(
                    decided_at=self.now + timedelta(seconds=3)
                )
            )

    def test_producer_cannot_verify_own_work(self) -> None:
        self.begin_valid_attempt()
        self.service.attach_evidence(self.readback())
        with self.assertRaises(IndependentVerificationError):
            self.service.verify(
                self.verification(verifier_id="producer")
            )
        with self.assertRaises(UnverifiedOutcomeError):
            self.service.completion_claim("work-1")

    def test_executor_narration_cannot_verify_outcome(self) -> None:
        self.begin_valid_attempt()
        self.service.attach_evidence(
            self.readback(evidence_kind=EvidenceKind.EXECUTOR_REPORT)
        )
        with self.assertRaises(ExecutionTruthError):
            self.service.verify(self.verification())
        self.assertEqual(
            self.store.get_work_item("work-1")["status"],
            "attempted",
        )

    def test_expected_facts_must_match_external_readback(self) -> None:
        self.begin_valid_attempt()
        self.service.attach_evidence(self.readback())
        with self.assertRaises(ExecutionTruthError):
            self.service.verify(
                self.verification(expected_facts={"status": "deleted"})
            )
        with self.assertRaises(UnverifiedOutcomeError):
            self.service.completion_claim("work-1")

    def test_storage_writer_cannot_manufacture_verified_completion(self) -> None:
        self.begin_valid_attempt()
        with self.assertRaisesRegex(ValueError, "observed"):
            self.store.record_outcome_verification(
                {
                    "verification_id": "verification-forged",
                    "attempt_id": "attempt-1",
                    "work_item_id": "work-1",
                    "tenant_id": "tenant-1",
                    "business_id": "business-1",
                    "verifier_id": "producer",
                    "decision": "verified",
                    "evidence_receipt_ids": [],
                    "expected_facts": {},
                    "rationale": "Trust the producer.",
                    "policy_version": "forged/v1",
                    "decided_at": self.now + timedelta(seconds=2),
                }
            )
        with self.assertRaises(UnverifiedOutcomeError):
            self.service.completion_claim("work-1")

        self.service.attach_evidence(self.readback())
        forged = {
            "verification_id": "verification-forged-observed",
            "attempt_id": "attempt-1",
            "work_item_id": "work-1",
            "tenant_id": "tenant-1",
            "business_id": "business-1",
            "verifier_id": "qa",
            "decision": "verified",
            "evidence_receipt_ids": [],
            "expected_facts": {},
            "rationale": "Ignore the durable evidence.",
            "policy_version": "forged/v1",
            "decided_at": self.now + timedelta(seconds=3),
        }
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.store.record_outcome_verification(forged)
        forged["evidence_receipt_ids"] = ["receipt-after"]
        forged["expected_facts"] = {
            "version": 2,
            "status": "active",
        }
        forged["verifier_id"] = "producer"
        with self.assertRaisesRegex(ValueError, "independent"):
            self.store.record_outcome_verification(forged)
        with self.assertRaises(UnverifiedOutcomeError):
            self.service.completion_claim("work-1")

    def test_wrong_target_readback_is_rejected(self) -> None:
        self.begin_valid_attempt()
        with self.assertRaisesRegex(ExecutionTruthError, "target"):
            self.service.attach_evidence(
                self.readback(source_ref="record:someone-elses-record")
            )

    def test_unregistered_evidence_issuer_is_rejected(self) -> None:
        self.begin_valid_attempt()
        with self.assertRaisesRegex(ExecutionTruthError, "issuer"):
            self.service.attach_evidence(
                self.readback(source_system="unregistered-system")
            )

    def test_disabled_issuer_cannot_support_an_external_attempt(self) -> None:
        self.assertTrue(
            self.service.capture_precondition(self.precondition())
        )
        self.store.upsert_actor(
            ActorIdentity(
                actor_id="producer",
                tenant_id="tenant-1",
                actor_type=ActorType.AGENT,
                roles=frozenset({"marketing"}),
                business_ids=frozenset({"business-1"}),
                enabled=False,
            )
        )
        with self.assertRaisesRegex(ValueError, "issuer"):
            self.store.begin_execution_attempt(
                attempt=self.attempt().as_record(),
                worker_id=self.worker_id,
            )

    def test_verification_cannot_cherry_pick_older_readback(self) -> None:
        self.begin_valid_attempt()
        self.service.attach_evidence(
            self.readback(
                receipt_id="receipt-after-old",
                observed_at=self.now + timedelta(seconds=2),
            )
        )
        self.service.attach_evidence(
            self.readback(
                receipt_id="receipt-after-new",
                observed_at=self.now + timedelta(seconds=3),
                payload={"version": 3, "status": "deleted"},
            )
        )
        with self.assertRaisesRegex(ExecutionTruthError, "latest"):
            self.service.verify(
                self.verification(
                    evidence_receipt_ids=("receipt-after-old",),
                    decided_at=self.now + timedelta(seconds=4),
                )
            )

    def test_simulation_cannot_be_claimed_as_completion(self) -> None:
        resolved = self.store.resolve_claimed_work(
            work_item_id="work-1",
            worker_id=self.worker_id,
            status="simulated",
            authority_mode="auto",
            record_type="work.simulated",
            details={"status": "simulated"},
            audit_id="audit-simulated",
            now=self.now + timedelta(seconds=1),
        )
        self.assertTrue(resolved)
        with self.assertRaises(UnverifiedOutcomeError):
            self.service.completion_claim("work-1")

    def test_direct_sql_cannot_create_external_money_movement(self) -> None:
        with closing(sqlite3.connect(self.store.path)) as connection:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "financial commitments are forbidden",
            ):
                connection.execute(
                    """
                    INSERT INTO execution_attempts(
                        attempt_id, work_item_id, tenant_id, business_id,
                        producer_id, execution_mode, action_type, target_ref,
                        idempotency_key, precondition_receipt_id, status,
                        summary, attempted_at, observed_at, updated_at,
                        reconciliation_available_at
                    )
                    VALUES (
                        'forged-payment', 'work-1', 'tenant-1', 'business-1',
                        'producer', 'external', 'finance.payment.execute',
                        'bank:external', 'forged-payment', NULL, 'attempted',
                        'Forged payment attempt', ?, NULL, ?, ?
                    )
                    """,
                    (
                        self.now.isoformat(),
                        self.now.isoformat(),
                        self.now.isoformat(),
                    ),
                )

    def test_simulated_attempt_cannot_be_promoted_to_verified(self) -> None:
        simulated = ExecutionAttempt(
            attempt_id="attempt-1",
            work_item_id="work-1",
            tenant_id="tenant-1",
            business_id="business-1",
            producer_id="producer",
            execution_mode=ExecutionMode.SIMULATED,
            action_type="record.update",
            target_ref="record:record-1",
            idempotency_key="record-1:simulation:1",
            summary="Simulated an update without an external write.",
            attempted_at=self.now + timedelta(seconds=1),
        )
        self.service.begin_attempt(simulated, worker_id=self.worker_id)
        self.service.attach_evidence(self.readback())
        with self.assertRaises(ExecutionTruthError):
            self.service.verify(self.verification())
        with self.assertRaises(UnverifiedOutcomeError):
            self.service.completion_claim("work-1")

    def test_generic_resolver_cannot_assert_verified_status(self) -> None:
        with self.assertRaises(ValueError):
            self.store.resolve_claimed_work(
                work_item_id="work-1",
                worker_id=self.worker_id,
                status="verified",
                authority_mode="auto",
                record_type="outcome.verified",
                details={"status": "verified"},
                audit_id="audit-false-verification",
                now=self.now + timedelta(seconds=1),
            )
        self.assertEqual(
            self.store.get_work_item("work-1")["status"],
            "claimed",
        )

    def test_receipt_identity_is_immutable(self) -> None:
        original = self.precondition()
        self.assertTrue(self.service.capture_precondition(original))
        self.assertFalse(self.service.capture_precondition(original))
        changed = self.precondition(payload={"version": 99})
        with self.assertRaises(ExecutionTruthError):
            self.service.capture_precondition(changed)

    def test_persisted_receipt_cannot_be_rewritten(self) -> None:
        self.begin_valid_attempt()
        self.service.attach_evidence(self.readback())
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "append-only",
            ):
                connection.execute(
                    """
                    UPDATE evidence_receipts
                    SET payload_json =
                        '{"status": "attacker-controlled", "version": 999}'
                    WHERE receipt_id = 'receipt-after'
                    """
                )
        self.assertTrue(self.service.verify(self.verification()))

    def test_verified_completion_records_remain_append_only(self) -> None:
        self.begin_valid_attempt()
        self.service.attach_evidence(self.readback())
        self.assertTrue(self.service.verify(self.verification()))
        original = self.service.completion_claim("work-1")
        backup_path = (
            Path(self.tempdir.name) / "verified-completion-backup.db"
        )
        self.store.create_backup(backup_path)
        restored = OutcomeVerificationService(SQLiteStore(backup_path))
        self.assertEqual(restored.completion_claim("work-1"), original)
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "append-only",
            ):
                connection.execute(
                    """
                    UPDATE evidence_receipts
                    SET payload_json =
                        '{"status":"attacker-controlled","version":999}',
                        content_hash = 'attacker-recomputed-hash'
                    WHERE receipt_id = 'receipt-after'
                    """
                )
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "append-only",
            ):
                connection.execute(
                    """
                    UPDATE outcome_verifications
                    SET expected_facts_json =
                        '{"status":"attacker-controlled","version":999}'
                    WHERE verification_id = 'verification-1'
                    """
                )
        self.assertEqual(
            self.service.completion_claim("work-1"),
            original,
        )

    def test_direct_sql_cannot_manufacture_authenticated_completion(
        self,
    ) -> None:
        self.begin_valid_attempt()
        record = self.readback().as_record()
        decided_at = self.now + timedelta(seconds=3)
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO evidence_receipts(
                    receipt_id, work_item_id, attempt_id, tenant_id,
                    business_id, evidence_kind, source_system, source_ref,
                    captured_by, observed_at, valid_until, payload_json,
                    content_hash, created_at, issuer_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["receipt_id"],
                    record["work_item_id"],
                    record["attempt_id"],
                    record["tenant_id"],
                    record["business_id"],
                    record["evidence_kind"],
                    record["source_system"],
                    record["source_ref"],
                    record["captured_by"],
                    record["observed_at"].isoformat(),
                    record["valid_until"].isoformat(),
                    json.dumps(record["payload"], sort_keys=True),
                    record["content_hash"],
                    record["created_at"].isoformat(),
                    record["issuer_version"],
                ),
            )
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "authenticated attestation",
            ):
                connection.execute(
                    """
                    UPDATE execution_attempts
                    SET status = 'verified'
                    WHERE attempt_id = 'attempt-1'
                    """
                )
            connection.execute(
                """
                INSERT INTO outcome_verifications(
                    verification_id, attempt_id, work_item_id, tenant_id,
                    business_id, verifier_id, decision,
                    evidence_receipt_ids_json, expected_facts_json, rationale,
                    policy_version, decided_at
                )
                VALUES (
                    'verification-forged', 'attempt-1', 'work-1',
                    'tenant-1', 'business-1', 'qa', 'verified',
                    '["receipt-after"]',
                    '{"status": "active", "version": 2}',
                    'direct SQL forgery', 'attacker/v1', ?
                )
                """,
                (decided_at.isoformat(),),
            )
            connection.execute(
                """
                INSERT INTO completion_attestations(
                    verification_id, attempt_id, work_item_id, tenant_id,
                    business_id, key_id, signature, created_at,
                    payload_version
                )
                VALUES (
                    'verification-forged', 'attempt-1', 'work-1',
                    'tenant-1', 'business-1', 'attacker-key',
                    'attacker-signature', ?, 2
                )
                """,
                (decided_at.isoformat(),),
            )
            connection.execute(
                """
                UPDATE execution_attempts
                SET status = 'verified', observed_at = ?, updated_at = ?
                WHERE attempt_id = 'attempt-1'
                """,
                (record["observed_at"].isoformat(), decided_at.isoformat()),
            )
            connection.execute(
                """
                UPDATE work_items
                SET status = 'verified', updated_at = ?
                WHERE work_item_id = 'work-1'
                """,
                (decided_at.isoformat(),),
            )
        with self.assertRaises(UnverifiedOutcomeError):
            self.service.completion_claim("work-1")
        self.assertFalse(self.store.schema_status()["migration_valid"])

    def test_direct_sql_terminal_inserts_require_attestations(self) -> None:
        attempted_at = self.now + timedelta(seconds=1)
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "authenticated attestation",
            ):
                connection.execute(
                    """
                    INSERT INTO execution_attempts(
                        attempt_id, work_item_id, tenant_id, business_id,
                        producer_id, execution_mode, action_type, target_ref,
                        idempotency_key, status, summary, attempted_at,
                        updated_at
                    )
                    VALUES (
                        'attempt-inserted-terminal', 'work-1', 'tenant-1',
                        'business-1', 'producer', 'external',
                        'record.update', 'record:record-1',
                        'terminal-insert:1', 'verified', 'forged terminal',
                        ?, ?
                    )
                    """,
                    (attempted_at.isoformat(), attempted_at.isoformat()),
                )
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "authenticated attestation",
            ):
                connection.execute(
                    """
                    INSERT INTO work_items(
                        work_item_id, work_key, objective_id, tenant_id,
                        business_id, title, rationale, action_type,
                        assigned_actor_id, attributes_json, authority_mode,
                        status, priority_score, attempt_count, max_attempts,
                        available_at, created_at, updated_at
                    )
                    VALUES (
                        'work-inserted-terminal', 'terminal:insert',
                        'objective-1', 'tenant-1', 'business-1',
                        'Forged terminal work', 'No governed transition',
                        'record.update', 'producer', '{}', 'auto', 'verified',
                        1, 0, 1, ?, ?, ?
                    )
                    """,
                    (
                        attempted_at.isoformat(),
                        attempted_at.isoformat(),
                        attempted_at.isoformat(),
                    ),
                )

    def test_completion_signature_binds_work_semantics(self) -> None:
        self.begin_valid_attempt()
        self.service.attach_evidence(self.readback())
        self.assertTrue(self.service.verify(self.verification()))
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            trigger = connection.execute(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND name = 'prevent_terminal_work_update'
                """
            ).fetchone()[0]
            connection.execute("DROP TRIGGER prevent_terminal_work_update")
            connection.execute(
                """
                UPDATE work_items
                SET title = 'Move money',
                    action_type = 'finance.payment.execute',
                    amount = '10000.00',
                    currency = 'USD',
                    attributes_json = '{"destination":"external"}'
                WHERE work_item_id = 'work-1'
                """
            )
            connection.execute(trigger)
        with self.assertRaises(UnverifiedOutcomeError):
            self.service.completion_claim("work-1")
        status = self.store.schema_status()
        self.assertFalse(status["migration_valid"])
        self.assertTrue(
            any(
                "signature is invalid" in error
                for error in status["migration_errors"]
            )
        )

    def test_completion_signature_detects_restored_schema_scope_movement(
        self,
    ) -> None:
        self.store.upsert_business(
            Business(
                business_id="business-2",
                tenant_id="tenant-1",
                legal_name="Business Two LLC",
                display_name="Business Two",
                base_currency="USD",
                timezone_name="UTC",
            )
        )
        for actor_id, roles in (
            ("producer", frozenset({"marketing"})),
            ("qa", frozenset({"qa"})),
        ):
            self.store.upsert_actor(
                ActorIdentity(
                    actor_id=actor_id,
                    tenant_id="tenant-1",
                    actor_type=ActorType.AGENT,
                    roles=roles,
                    business_ids=frozenset({"business-1", "business-2"}),
                )
            )
        self.begin_valid_attempt()
        self.service.attach_evidence(self.readback())
        self.assertTrue(self.service.verify(self.verification()))
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            triggers = connection.execute(
                """
                SELECT name, sql
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND tbl_name IN ('objectives', 'work_items')
                ORDER BY name
                """
            ).fetchall()
            for name, _ in triggers:
                connection.execute(f'DROP TRIGGER "{name}"')
            connection.execute(
                """
                UPDATE objectives
                SET business_id = 'business-2'
                WHERE objective_id = 'objective-1'
                """
            )
            connection.execute(
                """
                UPDATE work_items
                SET business_id = 'business-2'
                WHERE work_item_id = 'work-1'
                """
            )
            for _, sql in triggers:
                connection.execute(sql)
        with self.assertRaises(UnverifiedOutcomeError):
            self.service.completion_claim("work-1")
        self.assertFalse(self.store.schema_status()["migration_valid"])

    def test_parent_scope_cannot_move_away_from_existing_work(self) -> None:
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
                timezone_name="UTC",
            )
        )
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "orphan child identity",
            ):
                connection.execute(
                    """
                    UPDATE objectives
                    SET tenant_id = 'tenant-2', business_id = 'business-2'
                    WHERE objective_id = 'objective-1'
                    """
                )
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "orphan child identity",
            ):
                connection.execute(
                    """
                    DELETE FROM objectives
                    WHERE objective_id = 'objective-1'
                    """
                )

    def test_receipt_cannot_cross_the_work_identity_boundary(self) -> None:
        cross_tenant = EvidenceReceipt(
            receipt_id="receipt-cross-tenant",
            work_item_id="work-1",
            tenant_id="tenant-2",
            business_id="business-1",
            evidence_kind=EvidenceKind.PRECONDITION,
            source_system="test-system",
            source_ref="record:record-1",
            captured_by="producer",
            observed_at=self.now,
            valid_until=self.now + timedelta(seconds=30),
            payload={"version": 1},
            created_at=self.now,
            issuer_version="test-adapter/v1",
        )
        with self.assertRaises(ExecutionTruthError):
            self.service.capture_precondition(cross_tenant)

    def test_uncertain_attempt_is_claimed_for_read_only_reconciliation(
        self,
    ) -> None:
        self.begin_valid_attempt()
        reconcile_time = self.now + timedelta(seconds=2)
        claimed = self.service.claim_uncertain_attempt(
            worker_id="reconciler-1",
            now=reconcile_time,
            lease_seconds=30,
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["status"], "reconciling")
        self.assertIsNone(
            self.service.claim_uncertain_attempt(
                worker_id="reconciler-2",
                now=reconcile_time,
                lease_seconds=30,
            )
        )
        self.service.attach_evidence(self.readback())
        self.assertEqual(
            self.store.get_execution_attempt("attempt-1")["status"],
            "observed",
        )
        self.assertEqual(
            self.store.get_work_item("work-1")["status"],
            "observed",
        )

    def test_uncertain_attempt_reconciliation_is_bounded(self) -> None:
        self.begin_valid_attempt()
        for offset in (2, 4, 6):
            current = self.now + timedelta(seconds=offset)
            claimed = self.service.claim_uncertain_attempt(
                worker_id="reconciler",
                now=current,
                lease_seconds=30,
            )
            self.assertIsNotNone(claimed)
            status = self.service.defer_uncertain_attempt(
                attempt_id="attempt-1",
                worker_id="reconciler",
                error="external read-back unavailable",
                now=current,
                retry_seconds=1,
            )
        self.assertEqual(status, "reconciliation_failed")
        self.assertEqual(
            self.store.get_work_item("work-1")["status"],
            "reconciliation_failed",
        )


if __name__ == "__main__":
    unittest.main()
