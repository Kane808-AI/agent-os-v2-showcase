from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from pathlib import Path
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os import (  # noqa: E402
    ActorIdentity,
    ActorType,
    AggregatePerformanceService,
    AggregateSnapshot,
    Business,
    CapabilityPackCatalog,
    CostModel,
    CutoverStage,
    OperationalProfile,
    ProductionReadinessService,
    REQUIRED_METRICS,
    ResilienceReport,
    Tenant,
    TenantDeploymentManifest,
    UpgradePlan,
)
from agent_os.postgresql import (  # noqa: E402
    PILOT_SCHEMA_VERSION,
    PostgreSQLPilotStore,
)
from agent_os.pilot_canary import import_report, validate_report  # noqa: E402
from agent_os.storage import LATEST_SCHEMA_VERSION  # noqa: E402


ADMIN_DSN = os.environ.get("AOS_TEST_POSTGRES_ADMIN_DSN")


@unittest.skipUnless(ADMIN_DSN, "requires isolated PostgreSQL integration DSN")
class PostgreSQLPilotIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        cls.psycopg = psycopg
        cls.suffix = uuid4().hex[:10]
        cls.runtime_role = f"aos_pilot_{cls.suffix}"
        cls.other_role = f"aos_other_{cls.suffix}"
        cls.runtime_password = f"runtime-{cls.suffix}"
        cls.other_password = f"other-{cls.suffix}"
        cls.tenant_id = f"tenant-{cls.suffix}"
        cls.business_id = f"business-{cls.suffix}"
        cls.other_tenant_id = f"tenant-other-{cls.suffix}"
        cls.other_business_id = f"business-other-{cls.suffix}"
        cls.now = datetime(2026, 7, 31, 20, tzinfo=timezone.utc)
        PostgreSQLPilotStore.apply_schema(ADMIN_DSN)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as connection:
            for role, password in (
                (cls.runtime_role, cls.runtime_password),
                (cls.other_role, cls.other_password),
            ):
                connection.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
                    ).format(sql.Identifier(role), sql.Literal(password))
                )
        cls.ops_id = f"ops-{cls.suffix}"
        cls.qa_id = f"qa-{cls.suffix}"
        cls.owner_id = f"owner-{cls.suffix}"
        PostgreSQLPilotStore.bootstrap_scope(
            ADMIN_DSN,
            tenant=Tenant(cls.tenant_id, "Pilot Tenant"),
            business=Business(
                cls.business_id, cls.tenant_id, "Pilot LLC", "Pilot", "USD", "UTC"
            ),
            actors=(
                ActorIdentity(
                    cls.ops_id, cls.tenant_id, ActorType.AGENT,
                    frozenset({"commerce", "operations", "platform-reliability"}),
                    frozenset({cls.business_id}),
                ),
                ActorIdentity(
                    cls.qa_id, cls.tenant_id, ActorType.AGENT,
                    frozenset({"qa"}), frozenset({cls.business_id}),
                ),
                ActorIdentity(
                    cls.owner_id, cls.tenant_id, ActorType.HUMAN,
                    frozenset({"business-owner", "operations"}),
                    frozenset({cls.business_id}),
                ),
            ),
        )
        PostgreSQLPilotStore.bootstrap_scope(
            ADMIN_DSN,
            tenant=Tenant(cls.other_tenant_id, "Other Tenant"),
            business=Business(
                cls.other_business_id, cls.other_tenant_id,
                "Other LLC", "Other", "USD", "UTC",
            ),
            actors=(ActorIdentity(
                f"other-{cls.suffix}", cls.other_tenant_id, ActorType.AGENT,
                frozenset({"commerce"}), frozenset({cls.other_business_id}),
            ),),
        )
        PostgreSQLPilotStore.grant_runtime_role(
            ADMIN_DSN, cls.runtime_role,
            tenant_id=cls.tenant_id, business_id=cls.business_id,
        )
        PostgreSQLPilotStore.grant_runtime_role(
            ADMIN_DSN, cls.other_role,
            tenant_id=cls.other_tenant_id, business_id=cls.other_business_id,
        )
        parsed = conninfo_to_dict(ADMIN_DSN)
        cls.runtime_dsn = make_conninfo(
            **{**parsed, "user": cls.runtime_role, "password": cls.runtime_password}
        )
        cls.store = PostgreSQLPilotStore(
            cls.runtime_dsn, tenant_id=cls.tenant_id, business_id=cls.business_id
        )

    @classmethod
    def tearDownClass(cls):
        with cls.psycopg.connect(ADMIN_DSN, autocommit=True) as connection:
            for role in (cls.runtime_role, cls.other_role):
                connection.execute(
                    "DELETE FROM pilot_runtime_bindings WHERE role_name=%s", (role,)
                )
                connection.execute(f'DROP OWNED BY "{role}"')
                connection.execute(f'DROP ROLE "{role}"')

    def test_schema_is_attested_and_login_scope_cannot_be_claimed(self):
        status = self.store.schema_status()
        self.assertTrue(status["migration_valid"], status)
        self.assertEqual(status["current_version"], PILOT_SCHEMA_VERSION)
        self.assertIsNone(self.store.get_business(self.other_business_id))
        claimed = PostgreSQLPilotStore(
            self.runtime_dsn,
            tenant_id=self.other_tenant_id,
            business_id=self.other_business_id,
        ).schema_status()
        self.assertFalse(claimed["migration_valid"])
        self.assertIn("not bound", " ".join(claimed["migration_errors"]))

    def test_read_only_pinterest_amazon_aggregate_path_is_real_postgresql(self):
        results = CapabilityPackCatalog(
            ROOT / "departments", ROOT / "agents"
        ).evaluate_all(store=self.store, now=self.now)
        self.assertEqual(len(results), 13)
        pinterest_id = f"pinterest-{self.suffix}"
        amazon_id = f"amazon-{self.suffix}"
        self.store.insert_evidence(
            evidence_id=pinterest_id, tenant_id=self.tenant_id,
            business_id=self.business_id, source_type="pinterest_aggregate",
            source_ref="pinterest:live-export", statement="Read-only Pinterest totals.",
            facts={"impressions": 136, "engagements": 3,
                   "content_clicks": 2, "outbound_clicks": 1},
            confidence=Decimal("0.90"), observed_at=self.now-timedelta(hours=1),
        )
        self.store.insert_evidence(
            evidence_id=amazon_id, tenant_id=self.tenant_id,
            business_id=self.business_id, source_type="affiliate_report",
            source_ref="amazon:live-export", statement="Read-only Amazon totals.",
            facts={"conversions": 0, "gross_revenue_minor": 0,
                   "commission_minor": 0}, confidence=Decimal("0.90"),
            observed_at=self.now-timedelta(hours=1),
        )
        service = AggregatePerformanceService(self.store)
        result = service.import_snapshot(
            tenant_id=self.tenant_id, business_id=self.business_id,
            producer_id=self.ops_id,
            snapshot=AggregateSnapshot(
                channel="pinterest", offer_key="amazon-B08M94BTYC",
                source_system="pinterest-amazon-readonly",
                source_ref=f"live:{self.suffix}",
                window_start=self.now-timedelta(days=30),
                window_end=self.now-timedelta(days=1), impressions=136,
                engagements=3, content_clicks=2, outbound_clicks=1,
                conversions=0, gross_revenue_minor=0, commission_minor=0,
                minimum_outbound_clicks=1,
                evidence_refs=(pinterest_id, amazon_id),
            ), now=self.now,
        )
        self.assertEqual(
            service.verify(
                snapshot_id=result.snapshot_id, verifier_id=self.qa_id, now=self.now
            ).value,
            "verified",
        )
        self.assertEqual(
            self.store.pilot_snapshot()["aggregates"][0]["evidence_class"],
            "directional_aggregate",
        )

    def test_goal14_qualification_and_cutover_remain_nonexecuting(self):
        service = ProductionReadinessService(self.store)
        manifest = TenantDeploymentManifest(
            tenant_id=self.tenant_id, business_id=self.business_id,
            release_version="2.0.0-alpha.20",
            image_digest="sha256:" + "a" * 64,
            database_adapter="postgresql",
            runtime_database_role_ref="secretref://pilot/postgres/runtime",
            migration_database_role_ref="secretref://pilot/postgres/migration",
            backup_database_role_ref="secretref://pilot/postgres/backup",
            secret_provider="gcp-secret-manager",
            attestation_provider="external-kms",
            attestation_key_ref="secretref://pilot/kms/truth",
            dashboard_origin="https://pilot.example.invalid",
            dashboard_auth="oidc", tls_required=True,
            telemetry_mode="metadata-only",
        )
        service.qualify_manifest(
            manifest, producer_id=self.ops_id, verifier_id=self.qa_id, now=self.now
        )
        service.qualify_operations(
            tenant_id=self.tenant_id, business_id=self.business_id,
            release_version=manifest.release_version,
            profile=OperationalProfile(
                REQUIRED_METRICS, "metadata-only", "metadata-only", 30, 10,
                "secretref://pilot/alerts/operations",
            ),
            cost_model=CostModel("USD", 5000, 20, 5, 1000, 25000),
            storage_gib=10, operations=10000, model_cost_micros=2000000,
            producer_id=self.ops_id, verifier_id=self.qa_id, now=self.now,
        )
        service.qualify_resilience(
            tenant_id=self.tenant_id, business_id=self.business_id,
            release_version=manifest.release_version,
            report=ResilienceReport(
                "postgresql", "row-level-security", "external-kms",
                16, 512, 140019, 0, "b"*64, "ok", True,
                45, 180, 60, 300, "c"*64,
            ), producer_id=self.ops_id, verifier_id=self.qa_id, now=self.now,
        )
        service.qualify_security(
            manifest=manifest, threat_model_hash="d"*64,
            critical_findings=0, high_findings=0, producer_id=self.ops_id,
            verifier_id=self.qa_id, now=self.now,
        )
        service.qualify_upgrade(
            tenant_id=self.tenant_id, business_id=self.business_id,
            plan=UpgradePlan(
                "2.0.0-alpha.19", "2.0.0-alpha.20", LATEST_SCHEMA_VERSION,
                "e"*64, "f"*64, True, True, True,
            ), producer_id=self.ops_id, verifier_id=self.qa_id, now=self.now,
        )
        readiness = service.readiness(
            tenant_id=self.tenant_id, business_id=self.business_id,
            release_version=manifest.release_version,
        )
        self.assertEqual(readiness["eligible_mode"], "read_only_canary")
        self.assertFalse(readiness["external_side_effects_enabled"])
        plan_id = service.create_cutover_plan(
            tenant_id=self.tenant_id, business_id=self.business_id,
            source_system="agent-os-v1", capability_id=f"report-{self.suffix}",
            mode="read_only", owner_id=self.ops_id, rollback_hash="a"*64,
            now=self.now,
        )
        for stage, actor in (
            (CutoverStage.SHADOW_COMPARED, self.qa_id),
            (CutoverStage.RECOVERY_VERIFIED, self.qa_id),
            (CutoverStage.APPROVED, self.owner_id),
            (CutoverStage.CANARY_OBSERVED, self.ops_id),
        ):
            service.advance_cutover(
                plan_id=plan_id, stage=stage, actor_id=actor,
                evidence_hash="b"*64, now=self.now,
            )
        cutover = self.store.pilot_snapshot()["cutovers"][0]
        self.assertEqual(cutover["stage"], "canary_observed")
        self.assertEqual(cutover["external_side_effects_enabled"], 0)

    def test_database_blocks_cross_scope_and_append_only_mutation(self):
        with self.store._connection() as connection:
            with self.assertRaises(Exception):
                connection.execute(
                    """INSERT INTO evidence_records(
                         evidence_id,tenant_id,business_id,source_type,source_ref,
                         statement,facts_json,confidence,observed_at,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        f"cross-{self.suffix}", self.other_tenant_id,
                        self.other_business_id, "test", "test", "cross",
                        "{}", Decimal("1"), self.now.isoformat(), self.now.isoformat(),
                    ),
                )
        evidence_id = f"append-only-{self.suffix}"
        self.store.insert_evidence(
            evidence_id=evidence_id,
            tenant_id=self.tenant_id,
            business_id=self.business_id,
            source_type="test",
            source_ref="append-only",
            statement="Append-only integration fixture.",
            facts={},
            confidence=Decimal("1"),
            observed_at=self.now,
        )
        with self.store._connection() as connection:
            with self.assertRaises(Exception):
                connection.execute(
                    "DELETE FROM evidence_records WHERE evidence_id=?",
                    (evidence_id,),
                )

    def test_failed_verification_rolls_back_complete_canary_batch(self):
        before = self.store.pilot_snapshot()["counts"]
        report = validate_report({
            "schema_version": 1,
            "mode": "read_only",
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "producer_id": self.ops_id,
            "verifier_id": f"missing-verifier-{self.suffix}",
            "offer_key": "amazon-B08M94BTYC",
            "source_ref": f"rollback:{self.suffix}",
            "window_start": (self.now-timedelta(days=30)).isoformat(),
            "window_end": (self.now-timedelta(days=1)).isoformat(),
            "observed_at": (self.now-timedelta(hours=1)).isoformat(),
            "pinterest": {
                "impressions": 10,
                "engagements": 2,
                "content_clicks": 1,
                "outbound_clicks": 1,
            },
            "amazon": {
                "conversions": 0,
                "gross_revenue_minor": 0,
                "commission_minor": 0,
            },
        })
        with self.assertRaises(Exception):
            import_report(self.store, report, now=self.now)
        self.assertEqual(self.store.pilot_snapshot()["counts"], before)


if __name__ == "__main__":
    unittest.main()
