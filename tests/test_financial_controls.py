from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3
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
    OutcomeVerificationService,
)


class FinancialControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "finance.db")
        self.store.initialize()
        self.now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
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
                actor_id="owner",
                tenant_id="tenant-1",
                actor_type=ActorType.HUMAN,
                roles=frozenset({"business-owner", "finance-approver"}),
                business_ids=frozenset({"business-1"}),
            )
        )
        self.store.upsert_actor(
            ActorIdentity(
                actor_id="buyer",
                tenant_id="tenant-1",
                actor_type=ActorType.AGENT,
                roles=frozenset({"buyer"}),
                business_ids=frozenset({"business-1"}),
            )
        )
        self.store.upsert_objective(
            Objective(
                objective_id="objective-1",
                tenant_id="tenant-1",
                business_id="business-1",
                statement="Run one bounded acquisition experiment.",
                metric="qualified_leads",
                target=Decimal("10"),
                status=ObjectiveStatus.ACTIVE,
            ),
            next_review_at=self.now,
        )
        self.store.upsert_authority_envelope(
            AuthorityEnvelope(
                envelope_id="authority-1",
                tenant_id="tenant-1",
                business_id="business-1",
                rules=(
                    AuthorityRule(
                        action_type="ad.spend",
                        mode=AuthorityMode.AUTO,
                        platforms=frozenset({"meta"}),
                        accounts=frozenset({"account-1"}),
                        roles=frozenset({"buyer"}),
                        max_amount=Decimal("25.00"),
                        currency="USD",
                    ),
                ),
                expires_at=self.now + timedelta(days=30),
            )
        )
        self.service = OutcomeVerificationService(self.store)
        self.service.register_evidence_issuer(
            tenant_id="tenant-1",
            business_id="business-1",
            source_system="meta",
            evidence_kind=EvidenceKind.PRECONDITION,
            actor_id="buyer",
            issuer_version="meta-test/v1",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def create_budget(
        self,
        *,
        envelope_id: str = "budget-1",
        limit: Decimal = Decimal("30.00"),
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        created_by: str = "owner",
    ) -> bool:
        return self.store.create_spend_envelope(
            envelope_id=envelope_id,
            tenant_id="tenant-1",
            business_id="business-1",
            action_type="ad.spend",
            platform="meta",
            account_id="account-1",
            currency="USD",
            limit=limit,
            period_start=period_start or self.now - timedelta(hours=1),
            period_end=period_end or self.now + timedelta(days=1),
            created_by=created_by,
            rationale="Bounded acquisition test budget.",
            now=self.now - timedelta(hours=1),
        )

    def enqueue_and_claim(
        self,
        suffix: str,
        *,
        amount: Decimal,
    ) -> tuple[str, str]:
        work_id = f"work-{suffix}"
        worker_id = f"worker-{suffix}"
        self.assertTrue(
            self.store.enqueue_work_item(
                work_item_id=work_id,
                work_key=f"finance:{suffix}",
                objective_id="objective-1",
                tenant_id="tenant-1",
                business_id="business-1",
                title=f"Run bounded campaign {suffix}",
                rationale="Test cumulative spend enforcement.",
                action_type="ad.spend",
                assigned_actor_id="buyer",
                platform="meta",
                account_id="account-1",
                amount=amount,
                currency="USD",
                attributes={"campaign_id": suffix},
                authority_mode="auto",
                status="ready",
                priority_score=100,
                max_attempts=3,
                available_at=self.now,
                next_review_at=self.now + timedelta(hours=1),
                audit_id=f"audit-work-{suffix}",
            )
        )
        claimed = self.store.claim_next_work(
            worker_id=worker_id,
            now=self.now,
            lease_seconds=600,
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["work_item_id"], work_id)
        return work_id, worker_id

    def prepare_attempt(
        self,
        suffix: str,
        *,
        work_id: str,
        attempted_at: datetime | None = None,
    ) -> ExecutionAttempt:
        target = f"campaign:{suffix}"
        receipt_id = f"precondition-{suffix}"
        attempt_time = attempted_at or self.now + timedelta(seconds=1)
        self.assertTrue(
            self.service.capture_precondition(
                EvidenceReceipt(
                    receipt_id=receipt_id,
                    work_item_id=work_id,
                    tenant_id="tenant-1",
                    business_id="business-1",
                    evidence_kind=EvidenceKind.PRECONDITION,
                    source_system="meta",
                    source_ref=target,
                    captured_by="buyer",
                    observed_at=self.now,
                    valid_until=self.now + timedelta(minutes=5),
                    payload={"spend": "0.00", "status": "ready"},
                    created_at=self.now,
                    issuer_version="meta-test/v1",
                )
            )
        )
        return ExecutionAttempt(
            attempt_id=f"attempt-{suffix}",
            work_item_id=work_id,
            tenant_id="tenant-1",
            business_id="business-1",
            producer_id="buyer",
            execution_mode=ExecutionMode.EXTERNAL,
            action_type="ad.spend",
            target_ref=target,
            idempotency_key=f"spend:{suffix}",
            precondition_receipt_id=receipt_id,
            summary=f"Begin bounded campaign {suffix}.",
            attempted_at=attempt_time,
        )

    def test_cumulative_budget_blocks_individually_allowed_spend(self) -> None:
        self.create_budget()
        first_work, first_worker = self.enqueue_and_claim(
            "first",
            amount=Decimal("20.00"),
        )
        first = self.prepare_attempt("first", work_id=first_work)
        self.assertTrue(
            self.service.begin_attempt(first, worker_id=first_worker)
        )
        budget = self.store.get_spend_envelope("budget-1")
        self.assertEqual(budget["committed"], Decimal("20"))
        self.assertEqual(budget["remaining"], Decimal("10"))

        second_work, second_worker = self.enqueue_and_claim(
            "second",
            amount=Decimal("15.00"),
        )
        second = self.prepare_attempt("second", work_id=second_work)
        with self.assertRaisesRegex(
            ExecutionTruthError,
            "remaining budget",
        ):
            self.service.begin_attempt(second, worker_id=second_worker)
        self.assertIsNone(self.store.get_execution_attempt("attempt-second"))
        self.assertEqual(
            self.store.get_spend_envelope("budget-1")["committed"],
            Decimal("20"),
        )
        self.assertEqual(
            self.store.get_work_item(second_work)["status"],
            "claimed",
        )

    def test_exact_remaining_budget_is_allowed(self) -> None:
        self.create_budget()
        for suffix, amount in (
            ("first", Decimal("20.00")),
            ("second", Decimal("10.00")),
        ):
            work_id, worker_id = self.enqueue_and_claim(
                suffix,
                amount=amount,
            )
            attempt = self.prepare_attempt(suffix, work_id=work_id)
            self.assertTrue(
                self.service.begin_attempt(attempt, worker_id=worker_id)
            )
        budget = self.store.get_spend_envelope("budget-1")
        self.assertEqual(budget["committed"], Decimal("30"))
        self.assertEqual(budget["remaining"], Decimal("0"))
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(snapshot["counts"]["spend_envelopes"], 1)
        self.assertEqual(snapshot["counts"]["spend_commitments"], 2)
        page = render_dashboard(snapshot)
        self.assertIn("Spend envelopes", page)
        self.assertIn("Spend commitments", page)

    def test_spend_requires_one_active_matching_envelope(self) -> None:
        work_id, worker_id = self.enqueue_and_claim(
            "unbudgeted",
            amount=Decimal("5.00"),
        )
        attempt = self.prepare_attempt("unbudgeted", work_id=work_id)
        with self.assertRaisesRegex(
            ExecutionTruthError,
            "one active spend envelope",
        ):
            self.service.begin_attempt(attempt, worker_id=worker_id)

    def test_only_authorized_human_can_create_non_overlapping_budget(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "authorized in-scope human"):
            self.create_budget(created_by="buyer")
        self.assertTrue(self.create_budget())
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.create_budget(
                envelope_id="budget-overlap",
                period_start=self.now,
                period_end=self.now + timedelta(days=2),
            )

    def test_direct_sql_cannot_bypass_remaining_budget(self) -> None:
        self.create_budget(limit=Decimal("10.00"))
        work_id, _ = self.enqueue_and_claim(
            "forged",
            amount=Decimal("20.00"),
        )
        with closing(
            sqlite3.connect(self.store.path)
        ) as connection, connection:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "exceeds remaining budget",
            ):
                connection.execute(
                    """
                    INSERT INTO spend_commitments(
                        commitment_id, envelope_id, attempt_id, work_item_id,
                        tenant_id, business_id, amount_minor, currency,
                        created_at
                    )
                    VALUES (
                        'forged-commitment', 'budget-1', 'forged-attempt', ?,
                        'tenant-1', 'business-1', 2000, 'USD', ?
                    )
                    """,
                    (work_id, (self.now + timedelta(seconds=1)).isoformat()),
                )

    def test_direct_sql_attempt_requires_budget_commitment(self) -> None:
        self.create_budget()
        work_id, _ = self.enqueue_and_claim(
            "missing-commitment",
            amount=Decimal("5.00"),
        )
        with closing(sqlite3.connect(self.store.path)) as connection:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "durable budget commitment",
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
                        'attempt-no-budget', ?, 'tenant-1', 'business-1',
                        'buyer', 'external', 'ad.spend', 'campaign:missing',
                        'spend:missing', NULL, 'attempted',
                        'Forged unbudgeted spend', ?, NULL, ?, ?
                    )
                    """,
                    (
                        work_id,
                        (self.now + timedelta(seconds=1)).isoformat(),
                        (self.now + timedelta(seconds=1)).isoformat(),
                        (self.now + timedelta(seconds=1)).isoformat(),
                    ),
                )

    def test_data_attestation_detects_restored_budget_trigger_bypass(
        self,
    ) -> None:
        self.create_budget(limit=Decimal("10.00"))
        work_id, _ = self.enqueue_and_claim(
            "attestation-bypass",
            amount=Decimal("20.00"),
        )
        with closing(
            sqlite3.connect(self.store.path)
        ) as connection, connection:
            trigger = connection.execute(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND name = 'enforce_spend_commitment_insert'
                """
            ).fetchone()[0]
            connection.execute(
                "DROP TRIGGER enforce_spend_commitment_insert"
            )
            connection.execute(
                """
                INSERT INTO spend_commitments(
                    commitment_id, envelope_id, attempt_id, work_item_id,
                    tenant_id, business_id, amount_minor, currency, created_at
                )
                VALUES (
                    'bypass-commitment', 'budget-1', 'bypass-attempt', ?,
                    'tenant-1', 'business-1', 2000, 'USD', ?
                )
                """,
                (work_id, (self.now + timedelta(seconds=1)).isoformat()),
            )
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
                    'bypass-attempt', ?, 'tenant-1', 'business-1', 'buyer',
                    'external', 'ad.spend', 'campaign:bypass',
                    'spend:bypass', NULL, 'attempted', 'Bypassed spend', ?,
                    NULL, ?, ?
                )
                """,
                (
                    work_id,
                    (self.now + timedelta(seconds=1)).isoformat(),
                    (self.now + timedelta(seconds=1)).isoformat(),
                    (self.now + timedelta(seconds=1)).isoformat(),
                ),
            )
            connection.execute(trigger)
        status = self.store.schema_status()
        self.assertFalse(status["migration_valid"])
        self.assertTrue(
            any(
                "spend commitments exceed" in error
                for error in status["migration_errors"]
            )
        )

    def test_current_authority_is_rechecked_before_budget_commitment(
        self,
    ) -> None:
        self.create_budget()
        work_id, worker_id = self.enqueue_and_claim(
            "stale-authority",
            amount=Decimal("5.00"),
        )
        attempt = self.prepare_attempt(
            "stale-authority",
            work_id=work_id,
        )
        self.store.upsert_authority_envelope(
            AuthorityEnvelope(
                envelope_id="authority-revoked",
                tenant_id="tenant-1",
                business_id="business-1",
                rules=(),
                expires_at=self.now + timedelta(days=30),
            )
        )
        with self.assertRaisesRegex(
            ExecutionTruthError,
            "authority is stale",
        ):
            self.service.begin_attempt(attempt, worker_id=worker_id)
        self.assertEqual(
            self.store.get_spend_envelope("budget-1")["committed"],
            Decimal("0"),
        )

    def test_concurrent_attempts_cannot_overcommit_budget(self) -> None:
        self.create_budget(limit=Decimal("30.00"))
        prepared = []
        for suffix in ("one", "two"):
            work_id, worker_id = self.enqueue_and_claim(
                suffix,
                amount=Decimal("20.00"),
            )
            prepared.append(
                (
                    self.prepare_attempt(suffix, work_id=work_id),
                    worker_id,
                )
            )
        barrier = threading.Barrier(2)
        results: list[str] = []

        def begin(attempt: ExecutionAttempt, worker_id: str) -> None:
            barrier.wait()
            try:
                self.service.begin_attempt(attempt, worker_id=worker_id)
                results.append("committed")
            except ExecutionTruthError:
                results.append("blocked")

        threads = [
            threading.Thread(target=begin, args=item)
            for item in prepared
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(results, ["committed", "blocked"])
        self.assertEqual(
            self.store.get_spend_envelope("budget-1")["committed"],
            Decimal("20"),
        )


if __name__ == "__main__":
    unittest.main()
