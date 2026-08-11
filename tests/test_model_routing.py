from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os.contracts import Business, Tenant  # noqa: E402
from agent_os.dashboard import render_dashboard  # noqa: E402
from agent_os.agents import load_constitution  # noqa: E402
from agent_os.routing import (  # noqa: E402
    DataClass,
    ModelCatalogEntry,
    ModelRouter,
    ProviderOutcome,
    ReasoningTier,
    RouteRequest,
    RouteStatus,
    RoutingError,
    route_request_from_constitution,
)
from agent_os.storage import SchemaDriftError, SQLiteStore  # noqa: E402


class ModelRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "routing.db"
        self.store = SQLiteStore(self.database)
        self.store.initialize()
        self.now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        self._add_scope("tenant-1", "business-1")
        self.router = ModelRouter(self.store)
        self.entries = (
            ModelCatalogEntry(
                model_id="utility-a",
                provider_id="provider-a",
                provider_model_ref="provider-a/utility-2026-07-01",
                reasoning_tier=ReasoningTier.UTILITY,
                tool_use=False,
                structured_output=True,
                modalities=frozenset({"text"}),
                context_window_tokens=32_000,
                allowed_data_classes=frozenset(
                    {DataClass.PUBLIC, DataClass.INTERNAL}
                ),
                input_micros_per_million=100_000,
                output_micros_per_million=200_000,
                quality_score=70,
                evaluation_version="eval-2026-07",
            ),
            ModelCatalogEntry(
                model_id="standard-a",
                provider_id="provider-a",
                provider_model_ref="provider-a/standard-2026-07-15",
                reasoning_tier=ReasoningTier.STANDARD,
                tool_use=True,
                structured_output=True,
                modalities=frozenset({"text", "code"}),
                context_window_tokens=128_000,
                allowed_data_classes=frozenset(
                    {
                        DataClass.PUBLIC,
                        DataClass.INTERNAL,
                        DataClass.CONFIDENTIAL,
                    }
                ),
                input_micros_per_million=500_000,
                output_micros_per_million=1_000_000,
                quality_score=85,
                evaluation_version="eval-2026-07",
            ),
            ModelCatalogEntry(
                model_id="advanced-b",
                provider_id="provider-b",
                provider_model_ref="provider-b/advanced-2026-07-20",
                reasoning_tier=ReasoningTier.ADVANCED,
                tool_use=True,
                structured_output=True,
                modalities=frozenset({"text", "code", "vision"}),
                context_window_tokens=256_000,
                allowed_data_classes=frozenset(DataClass),
                input_micros_per_million=1_000_000,
                output_micros_per_million=2_000_000,
                quality_score=95,
                evaluation_version="eval-2026-07",
            ),
        )
        self.router.register_catalog(
            "1.0.0", self.entries, created_at=self.now - timedelta(minutes=2)
        )
        self.router.activate_catalog(
            "1.0.0",
            activation_id="activate-v1",
            activated_at=self.now - timedelta(minutes=1),
        )
        self._configure_provider("provider-a", budget=100_000)
        self._configure_provider("provider-b", budget=100_000)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _add_scope(self, tenant_id: str, business_id: str) -> None:
        self.store.upsert_tenant(
            Tenant(tenant_id=tenant_id, display_name=tenant_id)
        )
        self.store.upsert_business(
            Business(
                business_id=business_id,
                tenant_id=tenant_id,
                legal_name=f"{business_id} LLC",
                display_name=business_id,
                base_currency="USD",
                timezone_name="UTC",
            )
        )

    def _configure_provider(
        self,
        provider_id: str,
        *,
        tenant_id: str = "tenant-1",
        business_id: str = "business-1",
        budget: int,
        enabled: bool = True,
        classes: frozenset[DataClass] = frozenset(DataClass),
    ) -> None:
        suffix = f"{tenant_id}-{business_id}-{provider_id}"
        self.router.bind_credential(
            credential_id=f"credential-{suffix}",
            tenant_id=tenant_id,
            business_id=business_id,
            provider_id=provider_id,
            credential_ref=f"vault://{suffix}",
            created_at=self.now - timedelta(minutes=1),
        )
        self.router.revise_provider_policy(
            policy_revision_id=f"policy-{suffix}-1",
            tenant_id=tenant_id,
            business_id=business_id,
            provider_id=provider_id,
            credential_id=f"credential-{suffix}",
            enabled=enabled,
            allowed_data_classes=classes,
            monthly_budget_micros=budget,
            created_at=self.now - timedelta(seconds=30),
        )

    def request(self, request_id: str = "request-1", **changes) -> RouteRequest:
        values = {
            "request_id": request_id,
            "tenant_id": "tenant-1",
            "business_id": "business-1",
            "reasoning_tier": ReasoningTier.STANDARD,
            "data_class": DataClass.INTERNAL,
            "required_modalities": frozenset({"text"}),
            "requires_tool_use": True,
            "requires_structured_output": True,
            "required_context_tokens": 10_000,
            "estimated_input_tokens": 1_000,
            "estimated_output_tokens": 500,
        }
        values.update(changes)
        return RouteRequest(**values)

    def test_routes_by_capability_policy_cost_and_stable_order(self) -> None:
        decision = self.router.route(self.request(), now=self.now)
        self.assertEqual(decision.status, RouteStatus.SELECTED)
        self.assertEqual(decision.model_id, "standard-a")
        self.assertEqual(
            decision.candidate_order, ("standard-a", "advanced-b")
        )
        self.assertEqual(decision.estimated_cost_micros, 1000)
        repeated = self.router.route(self.request(), now=self.now)
        self.assertEqual(repeated.decision_id, decision.decision_id)

    def test_request_identity_cannot_be_reused_for_changed_semantics(self) -> None:
        self.router.route(self.request(), now=self.now)
        with self.assertRaisesRegex(RoutingError, "different semantics"):
            self.router.route(
                self.request(estimated_output_tokens=501), now=self.now
            )

    def test_concurrent_same_request_materializes_one_decision(self) -> None:
        barrier = threading.Barrier(4)
        decisions: list[str] = []
        errors: list[Exception] = []

        def route_once() -> None:
            try:
                barrier.wait()
                decision = self.router.route(self.request(), now=self.now)
                decisions.append(decision.decision_id)
            except Exception as error:  # pragma: no cover - assertion surface
                errors.append(error)

        workers = [threading.Thread(target=route_once) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertFalse(errors)
        self.assertEqual(len(set(decisions)), 1)

    def test_concurrent_routes_cannot_overreserve_provider_budget(self) -> None:
        self.router.revise_provider_policy(
            policy_revision_id="policy-concurrent-budget",
            tenant_id="tenant-1",
            business_id="business-1",
            provider_id="provider-a",
            credential_id="credential-tenant-1-business-1-provider-a",
            enabled=True,
            allowed_data_classes=frozenset(DataClass),
            monthly_budget_micros=1000,
            created_at=self.now,
        )
        barrier = threading.Barrier(2)
        decisions = []

        def route_once(number: int) -> None:
            barrier.wait()
            decisions.append(
                self.router.route(
                    self.request(
                        f"budget-race-{number}",
                        excluded_model_ids=frozenset({"advanced-b"}),
                    ),
                    now=self.now,
                )
            )

        workers = [
            threading.Thread(target=route_once, args=(number,))
            for number in range(2)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(
            sorted(decision.status.value for decision in decisions),
            ["held", "selected"],
        )

    def test_incompatible_route_holds_instead_of_silent_downgrade(self) -> None:
        decision = self.router.route(
            self.request(
                reasoning_tier=ReasoningTier.ADVANCED,
                required_modalities=frozenset({"audio"}),
            ),
            now=self.now,
        )
        self.assertEqual(decision.status, RouteStatus.HELD)
        self.assertIsNone(decision.model_id)
        self.assertIn("modalities", decision.rejection_reasons["advanced-b"])
        self.assertIn("reasoning_tier", decision.rejection_reasons["standard-a"])

    def test_independent_evaluator_requires_a_different_provider(self) -> None:
        decision = self.router.route(
            self.request(independent_from_provider_id="provider-a"),
            now=self.now,
        )
        self.assertEqual(decision.model_id, "advanced-b")
        self.assertIn("independence", decision.rejection_reasons["standard-a"])

    def test_constitution_capabilities_translate_without_provider_grant(self) -> None:
        verifier = load_constitution("qa-verifier")
        with self.assertRaisesRegex(
            RoutingError, "requires the producer provider"
        ):
            route_request_from_constitution(
                verifier,
                request_id="qa-no-producer",
                tenant_id="tenant-1",
                business_id="business-1",
                data_class=DataClass.INTERNAL,
                estimated_input_tokens=100,
                estimated_output_tokens=50,
            )
        request = route_request_from_constitution(
            verifier,
            request_id="qa-route",
            tenant_id="tenant-1",
            business_id="business-1",
            data_class=DataClass.INTERNAL,
            estimated_input_tokens=100,
            estimated_output_tokens=50,
            independent_from_provider_id="provider-a",
        )
        self.assertEqual(request.independent_from_provider_id, "provider-a")
        self.assertFalse(
            {"provider_id", "model_id"} & set(verifier.model_requirements)
        )

    def test_budget_and_data_policy_fail_closed(self) -> None:
        budget_hold = self.router.route(
            self.request("budget", max_cost_micros=999), now=self.now
        )
        self.assertEqual(budget_hold.status, RouteStatus.HELD)
        self.assertTrue(
            all(
                "request_cost_ceiling" in reasons
                for reasons in budget_hold.rejection_reasons.values()
                if "reasoning_tier" not in reasons
            )
        )
        data_hold = self.router.route(
            self.request(
                "restricted",
                data_class=DataClass.RESTRICTED_FINANCIAL,
            ),
            now=self.now,
        )
        self.assertEqual(data_hold.model_id, "advanced-b")
        self.assertIn(
            "model_data_policy",
            data_hold.rejection_reasons["standard-a"],
        )

    def test_actual_usage_exhausts_provider_monthly_budget(self) -> None:
        self.router.revise_provider_policy(
            policy_revision_id="policy-budget-tight",
            tenant_id="tenant-1",
            business_id="business-1",
            provider_id="provider-a",
            credential_id="credential-tenant-1-business-1-provider-a",
            enabled=True,
            allowed_data_classes=frozenset(DataClass),
            monthly_budget_micros=1000,
            created_at=self.now,
        )
        first = self.router.route(self.request("spend-budget"), now=self.now)
        self.assertEqual(first.model_id, "standard-a")
        self.router.record_usage(
            usage_id="usage-budget",
            decision_id=first.decision_id,
            input_tokens=1000,
            output_tokens=500,
            outcome=ProviderOutcome.SUCCESS,
            latency_ms=1,
            observed_at=self.now,
        )
        second = self.router.route(
            self.request("after-budget"), now=self.now
        )
        self.assertEqual(second.model_id, "advanced-b")
        self.assertIn(
            "provider_monthly_budget",
            second.rejection_reasons["standard-a"],
        )

    def test_catalog_activation_versions_future_decisions(self) -> None:
        before = self.router.route(self.request("before-v2"), now=self.now)
        v2_entries = tuple(
            replace(entry, evaluation_version="eval-2026-08")
            for entry in self.entries
        )
        self.router.register_catalog(
            "1.1.0", v2_entries, created_at=self.now + timedelta(minutes=1)
        )
        self.router.activate_catalog(
            "1.1.0",
            activation_id="activate-v2",
            activated_at=self.now + timedelta(minutes=2),
        )
        after = self.router.route(
            self.request("after-v2"), now=self.now + timedelta(minutes=3)
        )
        self.assertEqual(before.catalog_version, "1.0.0")
        self.assertEqual(after.catalog_version, "1.1.0")
        with closing(sqlite3.connect(self.database)) as connection:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError, "catalog entries are append-only"
            ):
                connection.execute(
                    """
                    UPDATE model_catalog_entries
                    SET quality_score = 0
                    WHERE catalog_version = '1.0.0'
                    """
                )

    def test_fallback_requires_failure_and_creates_linked_new_decision(self) -> None:
        first = self.router.route(self.request(), now=self.now)
        with self.assertRaisesRegex(RoutingError, "recorded non-success"):
            self.router.route_fallback(first.decision_id, now=self.now)
        self.router.record_usage(
            usage_id="usage-first",
            decision_id=first.decision_id,
            input_tokens=1000,
            output_tokens=100,
            outcome=ProviderOutcome.RATE_LIMITED,
            latency_ms=20,
            observed_at=self.now,
        )
        fallback = self.router.route_fallback(
            first.decision_id, request_id="request-fallback", now=self.now
        )
        self.assertEqual(fallback.model_id, "advanced-b")
        self.assertEqual(fallback.previous_decision_id, first.decision_id)
        self.assertIn(
            "explicitly_excluded",
            fallback.rejection_reasons["standard-a"],
        )

    def test_three_failures_open_circuit_and_cooldown_allows_one_probe(self) -> None:
        for number in range(3):
            decision = self.router.route(
                self.request(f"failure-{number}"), now=self.now
            )
            self.router.record_usage(
                usage_id=f"usage-failure-{number}",
                decision_id=decision.decision_id,
                input_tokens=1,
                output_tokens=1,
                outcome=ProviderOutcome.RATE_LIMITED,
                latency_ms=1,
                observed_at=self.now + timedelta(seconds=number),
            )
        rerouted = self.router.route(
            self.request("while-open"), now=self.now + timedelta(minutes=1)
        )
        self.assertEqual(rerouted.model_id, "advanced-b")
        self.assertIn(
            "circuit_open", rerouted.rejection_reasons["standard-a"]
        )
        probe = self.router.route(
            self.request("probe"), now=self.now + timedelta(minutes=6)
        )
        self.assertEqual(probe.model_id, "standard-a")
        self.assertTrue(probe.is_circuit_probe)
        second = self.router.route(
            self.request("second-probe"), now=self.now + timedelta(minutes=6)
        )
        self.assertEqual(second.model_id, "advanced-b")
        self.assertIn(
            "circuit_probe_in_flight",
            second.rejection_reasons["standard-a"],
        )
        self.assertTrue(self.store.schema_status()["migration_valid"])

    def test_auth_failure_is_tenant_isolated(self) -> None:
        self._add_scope("tenant-2", "business-2")
        self._configure_provider(
            "provider-a",
            tenant_id="tenant-2",
            business_id="business-2",
            budget=100_000,
        )
        first = self.router.route(self.request(), now=self.now)
        self.router.record_usage(
            usage_id="usage-auth",
            decision_id=first.decision_id,
            input_tokens=0,
            output_tokens=0,
            outcome=ProviderOutcome.AUTH_ERROR,
            latency_ms=1,
            observed_at=self.now,
        )
        other = self.router.route(
            replace(
                self.request("other-tenant"),
                tenant_id="tenant-2",
                business_id="business-2",
            ),
            now=self.now,
        )
        self.assertEqual(other.model_id, "standard-a")

    def test_usage_cost_is_derived_and_immutable(self) -> None:
        decision = self.router.route(self.request(), now=self.now)
        cost = self.router.record_usage(
            usage_id="usage-cost",
            decision_id=decision.decision_id,
            input_tokens=1000,
            output_tokens=500,
            outcome=ProviderOutcome.SUCCESS,
            latency_ms=123,
            observed_at=self.now,
        )
        self.assertEqual(cost, 1000)
        with closing(sqlite3.connect(self.database)) as connection:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError, "model usage is append-only"
            ):
                connection.execute(
                    "UPDATE model_usage_records SET cost_micros = 0"
                )

    def test_usage_cannot_predate_its_route(self) -> None:
        decision = self.router.route(self.request(), now=self.now)
        with self.assertRaisesRegex(RoutingError, "cannot predate"):
            self.router.record_usage(
                usage_id="usage-before-route",
                decision_id=decision.decision_id,
                input_tokens=1,
                output_tokens=1,
                outcome=ProviderOutcome.SUCCESS,
                latency_ms=1,
                observed_at=self.now - timedelta(seconds=1),
            )

    def test_dashboard_exposes_routes_circuits_and_cost_telemetry(self) -> None:
        decision = self.router.route(self.request(), now=self.now)
        self.router.record_usage(
            usage_id="usage-dashboard",
            decision_id=decision.decision_id,
            input_tokens=100,
            output_tokens=50,
            outcome=ProviderOutcome.SUCCESS,
            latency_ms=25,
            observed_at=self.now,
        )
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(snapshot["counts"]["routing_decisions"], 1)
        self.assertEqual(snapshot["model_cost_totals"][0]["cost_micros"], 100)
        rendered = render_dashboard(snapshot)
        self.assertIn("Model routing decisions", rendered)
        self.assertIn("Model cost telemetry", rendered)
        self.assertIn("standard-a", rendered)

    def test_backup_preserves_routing_evidence(self) -> None:
        decision = self.router.route(self.request(), now=self.now)
        self.router.record_usage(
            usage_id="usage-backup",
            decision_id=decision.decision_id,
            input_tokens=10,
            output_tokens=5,
            outcome=ProviderOutcome.SUCCESS,
            latency_ms=10,
            observed_at=self.now,
        )
        backup_path = self.store.create_backup(
            Path(self.tempdir.name) / "routing-backup.db"
        )
        backup = SQLiteStore(backup_path)
        self.assertTrue(backup.schema_status()["migration_valid"])
        snapshot = backup.dashboard_snapshot()
        self.assertEqual(snapshot["counts"]["routing_decisions"], 1)
        self.assertEqual(snapshot["counts"]["model_usage_records"], 1)

    def test_direct_sql_cross_scope_and_credential_reuse_are_rejected(self) -> None:
        self._add_scope("tenant-2", "business-2")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO provider_credentials(
                        credential_id, tenant_id, business_id, provider_id,
                        credential_ref, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "crossed",
                        "tenant-2",
                        "business-1",
                        "provider-a",
                        "vault://crossed",
                        self.now.isoformat(),
                    ),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO provider_credentials(
                        credential_id, tenant_id, business_id, provider_id,
                        credential_ref, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "reused",
                        "tenant-2",
                        "business-2",
                        "provider-a",
                        "vault://tenant-1-business-1-provider-a",
                        self.now.isoformat(),
                    ),
                )

    def test_opaque_auto_latest_and_plaintext_credentials_are_refused(self) -> None:
        with self.assertRaisesRegex(RoutingError, "exact versioned"):
            replace(
                self.entries[0],
                model_id="opaque",
                provider_model_ref="openrouter/auto",
            )
        with self.assertRaisesRegex(RoutingError, "exact versioned"):
            replace(
                self.entries[0],
                model_id="latest",
                provider_model_ref="provider-a/model-latest",
            )
        with self.assertRaisesRegex(RoutingError, "credential references"):
            self.router.bind_credential(
                credential_id="plaintext",
                tenant_id="tenant-1",
                business_id="business-1",
                provider_id="provider-a",
                credential_ref="sk-plaintext",
            )

    def test_doctor_attests_routing_request_hash(self) -> None:
        self.router.route(self.request(), now=self.now)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "DROP TRIGGER prevent_routing_decisions_update"
            )
            connection.execute(
                """
                UPDATE routing_decisions
                SET request_hash = ?
                """,
                ("0" * 64,),
            )
            connection.commit()
        status = self.store.schema_status()
        self.assertEqual(status["integrity"], "ok")
        self.assertFalse(status["migration_valid"])
        self.assertTrue(
            any(
                "durable data attestation failed" in error
                or "schema object is missing" in error
                for error in status["migration_errors"]
            )
        )

    def test_doctor_rejects_direct_sql_incompatible_selection(self) -> None:
        forged = self.request(
            "forged-selection",
            reasoning_tier=ReasoningTier.ADVANCED,
        )
        request_json = json.dumps(
            forged.payload(), sort_keys=True, separators=(",", ":")
        )
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                INSERT INTO routing_decisions(
                    decision_id, request_id, tenant_id, business_id,
                    request_hash, request_json, catalog_version, status,
                    model_id, provider_id, credential_id, policy_revision_id,
                    estimated_cost_micros, candidate_order_json,
                    rejection_reasons_json, previous_decision_id,
                    is_circuit_probe, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "forged-decision",
                    forged.request_id,
                    forged.tenant_id,
                    forged.business_id,
                    hashlib.sha256(request_json.encode()).hexdigest(),
                    request_json,
                    "1.0.0",
                    "selected",
                    "utility-a",
                    "provider-a",
                    "credential-tenant-1-business-1-provider-a",
                    "policy-tenant-1-business-1-provider-a-1",
                    200,
                    '["utility-a"]',
                    "{}",
                    None,
                    0,
                    self.now.isoformat(),
                ),
            )
            connection.commit()
        status = self.store.schema_status()
        self.assertFalse(status["migration_valid"])
        self.assertTrue(
            any(
                "selected route is incompatible" in error
                for error in status["migration_errors"]
            )
        )

    def test_doctor_rejects_circuit_state_without_health_evidence(self) -> None:
        decision = self.router.route(self.request(), now=self.now)
        self.router.record_usage(
            usage_id="usage-circuit-attestation",
            decision_id=decision.decision_id,
            input_tokens=1,
            output_tokens=1,
            outcome=ProviderOutcome.AUTH_ERROR,
            latency_ms=1,
            observed_at=self.now,
        )
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """
                UPDATE model_circuit_states
                SET circuit_state = 'closed', consecutive_failures = 0,
                    open_until = NULL
                """
            )
            connection.commit()
        status = self.store.schema_status()
        self.assertFalse(status["migration_valid"])
        self.assertTrue(
            any(
                "circuit state does not match" in error
                for error in status["migration_errors"]
            )
        )

    def test_legacy_incident_inventory_maps_to_regressions(self) -> None:
        inventory = json.loads(
            (ROOT / "migrations" / "model-routing-incidents.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(inventory["schema_version"], 1)
        self.assertEqual(
            {incident["id"] for incident in inventory["incidents"]},
            {
                "ROUTE-INC-001",
                "ROUTE-INC-002",
                "ROUTE-INC-003",
                "ROUTE-INC-004",
                "ROUTE-INC-005",
            },
        )
        available = {
            name
            for name in dir(ModelRoutingTests)
            if name.startswith("test_")
        }
        self.assertTrue(
            all(
                incident["regression_test"] in available
                for incident in inventory["incidents"]
            )
        )


if __name__ == "__main__":
    unittest.main()
