from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os import (  # noqa: E402
    ActorIdentity,
    ActorType,
    Business,
    CostModel,
    CutoverStage,
    OperationalProfile,
    ProductionError,
    ProductionReadinessService,
    QualificationDecision,
    REQUIRED_METRICS,
    REQUIRED_QUALIFICATIONS,
    ResilienceReport,
    Tenant,
    TenantDeploymentManifest,
    TenantPackageBuilder,
    UpgradePlan,
)
from agent_os.dashboard import render_dashboard  # noqa: E402
from agent_os.storage import LATEST_SCHEMA_VERSION, SQLiteStore  # noqa: E402


HASH_A = "a" * 64
HASH_B = "b" * 64


class ProductionHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.store = SQLiteStore(self.root / "production.db")
        self.store.initialize()
        self.now = datetime(2026, 7, 31, 20, tzinfo=timezone.utc)
        self.store.upsert_tenant(Tenant("tenant-1", "Tenant One"))
        self.store.upsert_business(Business(
            "business-1", "tenant-1", "One LLC", "One", "USD", "UTC"
        ))
        self.store.upsert_tenant(Tenant("tenant-2", "Tenant Two"))
        self.store.upsert_business(Business(
            "business-2", "tenant-2", "Two LLC", "Two", "USD", "UTC"
        ))
        self.store.upsert_actor(ActorIdentity(
            "ops", "tenant-1", ActorType.AGENT,
            frozenset({"platform-reliability", "operations"}),
            frozenset({"business-1"}),
        ))
        self.store.upsert_actor(ActorIdentity(
            "qa", "tenant-1", ActorType.AGENT, frozenset({"qa"}),
            frozenset({"business-1"}),
        ))
        self.store.upsert_actor(ActorIdentity(
            "owner", "tenant-1", ActorType.HUMAN,
            frozenset({"business-owner", "operations"}),
            frozenset({"business-1"}),
        ))
        self.store.upsert_actor(ActorIdentity(
            "outsider", "tenant-2", ActorType.AGENT,
            frozenset({"qa", "platform-reliability"}),
            frozenset({"business-2"}),
        ))
        self.service = ProductionReadinessService(self.store)

    def tearDown(self):
        self.tempdir.cleanup()

    def manifest(self, **changes):
        values = dict(
            tenant_id="tenant-1", business_id="business-1",
            release_version="2.0.0-alpha.19", image_digest="sha256:" + HASH_A,
            database_adapter="postgresql",
            runtime_database_role_ref="secretref://tenant-1/postgres/runtime",
            migration_database_role_ref="secretref://tenant-1/postgres/migration",
            backup_database_role_ref="secretref://tenant-1/postgres/backup",
            secret_provider="vault", attestation_provider="external-kms",
            attestation_key_ref="secretref://tenant-1/kms/truth",
            dashboard_origin="https://agent-os.example.invalid",
            dashboard_auth="oidc", tls_required=True,
            telemetry_mode="metadata-only", external_side_effects_enabled=False,
        )
        values.update(changes)
        return TenantDeploymentManifest(**values)

    def profile(self):
        return OperationalProfile(
            metrics=REQUIRED_METRICS, log_mode="metadata-only",
            trace_mode="metadata-only", retention_days=30,
            health_timeout_seconds=10,
            alert_route_ref="secretref://tenant-1/alerts/operations",
        )

    def costs(self, *, limit=25_000):
        return CostModel(
            currency="USD", monthly_fixed_minor=5_000,
            storage_gib_month_minor=20, operation_micros=5,
            model_cost_markup_bps=1_000, monthly_limit_minor=limit,
        )

    def resilience(self, **changes):
        values = dict(
            persistence_adapter="postgresql",
            isolation_control="row-level-security",
            attestation_control="external-kms", crash_cases=16,
            state_machine_cases=512, fuzz_seed=140019, failures=0,
            backup_hash=HASH_A, restore_integrity="ok",
            point_in_time_recovery=True, rpo_seconds=45, rto_seconds=180,
            max_rpo_seconds=60, max_rto_seconds=300, evidence_hash=HASH_B,
        )
        values.update(changes)
        return ResilienceReport(**values)

    def upgrade(self, **changes):
        values = dict(
            from_version="2.0.0-alpha.18", to_version="2.0.0-alpha.19",
            target_schema_version=LATEST_SCHEMA_VERSION,
            release_artifact_hash=HASH_A, pre_upgrade_backup_hash=HASH_B,
            migration_rehearsal_passed=True, rollback_rehearsal_passed=True,
            canary_passed=True,
        )
        values.update(changes)
        return UpgradePlan(**values)

    def qualify_all(self):
        manifest = self.manifest()
        self.service.qualify_manifest(
            manifest, producer_id="ops", verifier_id="qa", now=self.now
        )
        self.service.qualify_operations(
            tenant_id="tenant-1", business_id="business-1",
            release_version=manifest.release_version, profile=self.profile(),
            cost_model=self.costs(), storage_gib=10, operations=10_000,
            model_cost_micros=2_000_000, producer_id="ops",
            verifier_id="qa", now=self.now,
        )
        self.service.qualify_resilience(
            tenant_id="tenant-1", business_id="business-1",
            release_version=manifest.release_version,
            report=self.resilience(), producer_id="ops", verifier_id="qa",
            now=self.now,
        )
        self.service.qualify_security(
            manifest=manifest, threat_model_hash=HASH_A,
            critical_findings=0, high_findings=0, producer_id="ops",
            verifier_id="qa", now=self.now,
        )
        self.service.qualify_upgrade(
            tenant_id="tenant-1", business_id="business-1",
            plan=self.upgrade(), producer_id="ops", verifier_id="qa",
            now=self.now,
        )

    def test_policy_and_reference_package_match_runtime_contract(self):
        policy = json.loads(
            (ROOT / "deployment/production-qualification-policy.json").read_text()
        )
        example = json.loads(
            (ROOT / "deployment/reference/tenant-package.example.json").read_text()
        )
        schema = json.loads(
            (ROOT / "deployment/tenant-package.schema.json").read_text()
        )
        self.assertEqual(set(policy["required_qualifications"]), REQUIRED_QUALIFICATIONS)
        self.assertEqual(set(policy["required_metrics"]), REQUIRED_METRICS)
        self.assertFalse(policy["external_side_effects_enabled"])
        self.assertFalse(policy["legacy_disable_available"])
        self.assertEqual(set(example), set(schema["required"]))
        self.assertEqual(TenantDeploymentManifest(**example).database_adapter, "postgresql")

    def test_atomic_private_package_contains_references_but_no_secrets(self):
        target = self.root / "package"
        result = TenantPackageBuilder().build(self.manifest(), target)
        self.assertEqual(result, target)
        self.assertEqual(os.stat(target).st_mode & 0o777, 0o700)
        for path in target.iterdir():
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        content = (target / "manifest.json").read_text().lower()
        self.assertIn("secretref://", content)
        self.assertNotIn("password=", content)
        self.assertNotIn("postgresql://", content)
        self.assertIn('"external_side_effects_enabled": false', content)

    def test_package_publish_failure_leaves_no_partial_target(self):
        target = self.root / "failed-package"
        def fail(_: Path):
            raise RuntimeError("simulated crash")
        with self.assertRaises(RuntimeError):
            TenantPackageBuilder().build(
                self.manifest(), target, before_publish=fail
            )
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob(".agent-os-package-*")), [])

    def test_manifest_rejects_mutable_insecure_or_executing_configuration(self):
        invalid = (
            {"release_version": "latest"},
            {"image_digest": "latest"},
            {"database_adapter": "sqlite"},
            {"migration_database_role_ref": "secretref://tenant-1/postgres/runtime"},
            {"runtime_database_role_ref": "postgresql://user:password@db/x"},
            {"attestation_provider": "filesystem"},
            {"dashboard_origin": "http://example.invalid"},
            {"dashboard_auth": "none"},
            {"telemetry_mode": "full-content"},
            {"external_side_effects_enabled": True},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ProductionError):
                self.manifest(**changes)

    def test_qualification_requires_independent_scoped_operations_and_qa(self):
        with self.assertRaises(ProductionError):
            self.service.qualify_manifest(
                self.manifest(), producer_id="ops", verifier_id="ops"
            )
        with self.assertRaises(ProductionError):
            self.service.qualify_manifest(
                self.manifest(), producer_id="ops", verifier_id="outsider"
            )

    def test_full_qualification_yields_read_only_canary_not_activation(self):
        self.qualify_all()
        result = self.service.readiness(
            tenant_id="tenant-1", business_id="business-1",
            release_version="2.0.0-alpha.19",
        )
        self.assertEqual(result["decision"], "passed")
        self.assertEqual(set(result["qualified"]), REQUIRED_QUALIFICATIONS)
        self.assertEqual(result["eligible_mode"], "read_only_canary")
        self.assertFalse(result["external_side_effects_enabled"])
        self.assertTrue(self.store.schema_status()["migration_valid"])

    def test_failed_cost_limit_holds_readiness(self):
        estimate = self.service.qualify_operations(
            tenant_id="tenant-1", business_id="business-1",
            release_version="2.0.0-alpha.19", profile=self.profile(),
            cost_model=self.costs(limit=1), storage_gib=10,
            operations=10_000, model_cost_micros=2_000_000,
            producer_id="ops", verifier_id="qa", now=self.now,
        )
        self.assertGreater(estimate, 1)
        result = self.service.readiness(
            tenant_id="tenant-1", business_id="business-1",
            release_version="2.0.0-alpha.19",
        )
        self.assertEqual(result["decision"], "held")
        self.assertIn("cost", result["missing"])

    def test_newer_held_evidence_overrides_an_older_pass(self):
        self.service.record_qualification(
            tenant_id="tenant-1", business_id="business-1", kind="security",
            release_version="2.0.0-alpha.19", artifact_hash=HASH_A,
            checks={"review": True}, producer_id="ops", verifier_id="qa",
            now=self.now,
        )
        self.service.record_qualification(
            tenant_id="tenant-1", business_id="business-1", kind="security",
            release_version="2.0.0-alpha.19", artifact_hash=HASH_B,
            checks={"new_finding_closed": False}, producer_id="ops",
            verifier_id="qa", now=self.now,
        )
        result = self.service.readiness(
            tenant_id="tenant-1", business_id="business-1",
            release_version="2.0.0-alpha.19",
        )
        self.assertIn("security", result["missing"])

    def test_resilience_rejects_failed_or_undercovered_evidence(self):
        for changes in (
            {"persistence_adapter": "sqlite"}, {"crash_cases": 7},
            {"state_machine_cases": 127}, {"failures": 1},
            {"restore_integrity": "corrupt"}, {"point_in_time_recovery": False},
            {"rpo_seconds": 61}, {"rto_seconds": 301},
        ):
            with self.subTest(changes=changes), self.assertRaises(ProductionError):
                self.resilience(**changes)

    def test_upgrade_requires_current_schema_backup_canary_and_rollback(self):
        for changes in (
            {"target_schema_version": LATEST_SCHEMA_VERSION - 1},
            {"migration_rehearsal_passed": False},
            {"rollback_rehearsal_passed": False}, {"canary_passed": False},
            {"pre_upgrade_backup_hash": "bad"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ProductionError):
                self.upgrade(**changes)

    def test_qualification_tables_are_append_only_and_nonexecuting(self):
        self.service.qualify_manifest(
            self.manifest(), producer_id="ops", verifier_id="qa", now=self.now
        )
        with self.store._connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE production_qualifications SET decision='held'"
                )
            checks = json.dumps({"safe": True}, separators=(",", ":"))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO production_qualifications VALUES (
                        'executing','tenant-1','business-1','security',
                        '2.0.0-alpha.19',?,?,?,'ops','qa','passed',1,?
                    )""",
                    (HASH_A, checks, hashlib.sha256(checks.encode()).hexdigest(),
                     self.now.isoformat()),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO legacy_cutover_plans VALUES (
                        'disable','tenant-1','business-1','agent-os-v1',
                        'reporting','read_only','ops',?,1,0,?
                    )""",
                    (HASH_A, self.now.isoformat()),
                )
            row = connection.execute(
                "SELECT external_side_effects_enabled FROM production_qualifications LIMIT 1"
            ).fetchone()
        self.assertEqual(row[0], 0)

    def test_doctor_detects_forged_qualification_hash(self):
        checks = json.dumps({"real": True}, separators=(",", ":"))
        connection = sqlite3.connect(self.store.path)
        with connection:
            connection.execute(
                """INSERT INTO production_qualifications VALUES (
                    'forged','tenant-1','business-1','security','2.0.0-alpha.19',
                    ?,?,?, 'ops','qa','passed',0,?
                )""",
                (HASH_A, checks, HASH_B, self.now.isoformat()),
            )
        connection.close()
        status = self.store.schema_status()
        self.assertFalse(status["migration_valid"])
        self.assertTrue(any("qualification evidence" in e for e in status["migration_errors"]))

    def test_cutover_rehearsal_is_ordered_human_approved_and_read_only(self):
        plan = self.service.create_cutover_plan(
            tenant_id="tenant-1", business_id="business-1",
            source_system="agent-os-v1", capability_id="pinterest-reporting",
            mode="read_only", owner_id="ops", rollback_hash=HASH_A, now=self.now,
        )
        self.service.advance_cutover(
            plan_id=plan, stage=CutoverStage.SHADOW_COMPARED,
            actor_id="qa", evidence_hash=HASH_A, now=self.now,
        )
        self.service.advance_cutover(
            plan_id=plan, stage=CutoverStage.RECOVERY_VERIFIED,
            actor_id="qa", evidence_hash=HASH_B, now=self.now,
        )
        self.service.advance_cutover(
            plan_id=plan, stage=CutoverStage.APPROVED,
            actor_id="owner", evidence_hash=HASH_A, now=self.now,
        )
        self.service.advance_cutover(
            plan_id=plan, stage=CutoverStage.CANARY_OBSERVED,
            actor_id="ops", evidence_hash=HASH_B, now=self.now,
        )
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(snapshot["legacy_cutovers"][0]["latest_stage"], "canary_observed")
        self.assertEqual(snapshot["legacy_cutovers"][0]["legacy_disable_allowed"], 0)
        self.assertEqual(snapshot["legacy_cutovers"][0]["external_side_effects_enabled"], 0)

    def test_cutover_rejects_skip_agent_approval_cross_scope_and_write_mode(self):
        plan = self.service.create_cutover_plan(
            tenant_id="tenant-1", business_id="business-1",
            source_system="openclaw-legacy", capability_id="amazon-reporting",
            mode="proposal", owner_id="ops", rollback_hash=HASH_A,
        )
        with self.assertRaises(ProductionError):
            self.service.advance_cutover(
                plan_id=plan, stage=CutoverStage.APPROVED,
                actor_id="owner", evidence_hash=HASH_A,
            )
        self.service.advance_cutover(
            plan_id=plan, stage=CutoverStage.SHADOW_COMPARED,
            actor_id="qa", evidence_hash=HASH_A,
        )
        self.service.advance_cutover(
            plan_id=plan, stage=CutoverStage.RECOVERY_VERIFIED,
            actor_id="qa", evidence_hash=HASH_A,
        )
        with self.assertRaises(ProductionError):
            self.service.advance_cutover(
                plan_id=plan, stage=CutoverStage.APPROVED,
                actor_id="ops", evidence_hash=HASH_A,
            )
        with self.assertRaises(ProductionError):
            self.service.advance_cutover(
                plan_id=plan, stage=CutoverStage.APPROVED,
                actor_id="outsider", evidence_hash=HASH_A,
            )
        with self.assertRaises(ProductionError):
            self.service.create_cutover_plan(
                tenant_id="tenant-1", business_id="business-1",
                source_system="agent-os-v1", capability_id="publisher",
                mode="write", owner_id="ops", rollback_hash=HASH_A,
            )

    def test_cutover_can_roll_back_but_cannot_continue_after_rollback(self):
        plan = self.service.create_cutover_plan(
            tenant_id="tenant-1", business_id="business-1",
            source_system="agent-os-v1", capability_id="reporting",
            mode="shadow", owner_id="ops", rollback_hash=HASH_A,
        )
        for stage, actor in (
            (CutoverStage.SHADOW_COMPARED, "qa"),
            (CutoverStage.RECOVERY_VERIFIED, "qa"),
            (CutoverStage.APPROVED, "owner"),
        ):
            self.service.advance_cutover(
                plan_id=plan, stage=stage, actor_id=actor, evidence_hash=HASH_A
            )
        self.service.advance_cutover(
            plan_id=plan, stage=CutoverStage.ROLLED_BACK,
            actor_id="ops", evidence_hash=HASH_B,
        )
        with self.assertRaises(ProductionError):
            self.service.advance_cutover(
                plan_id=plan, stage=CutoverStage.CANARY_OBSERVED,
                actor_id="ops", evidence_hash=HASH_B,
            )

    def test_dashboard_exposes_qualification_without_secret_content(self):
        self.qualify_all()
        self.service.create_cutover_plan(
            tenant_id="tenant-1", business_id="business-1",
            source_system="agent-os-v1", capability_id="read-only-reporting",
            mode="read_only", owner_id="ops", rollback_hash=HASH_A,
        )
        html = render_dashboard(self.store.dashboard_snapshot())
        self.assertIn("Production qualification", html)
        self.assertIn("Legacy cutover rehearsals", html)
        self.assertIn("2.0.0-alpha.19", html)
        self.assertNotIn("secretref://", html)
        self.assertNotIn("postgresql://", html)

    def test_verified_backup_preserves_goal14_evidence(self):
        self.qualify_all()
        backup = self.store.create_backup(self.root / "qualified-backup.db")
        restored = SQLiteStore(backup)
        snapshot = restored.dashboard_snapshot()
        self.assertEqual(snapshot["counts"]["production_qualifications"], 8)
        self.assertTrue(restored.schema_status()["migration_valid"])

    def test_production_module_has_no_network_deploy_or_legacy_disable_client(self):
        source = (ROOT / "src/agent_os/production.py").read_text()
        for forbidden in (
            "import requests", "import httpx", "import socket",
            "subprocess", "disable_legacy", "external_side_effects_enabled=True",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
