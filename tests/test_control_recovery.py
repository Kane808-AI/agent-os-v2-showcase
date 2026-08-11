from pathlib import Path
from argparse import Namespace
from contextlib import closing, redirect_stdout
import hashlib
from io import StringIO
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
    Tenant,
)
from agent_os.cli import (  # noqa: E402
    command_doctor,
    command_migrate,
    command_render,
)
from agent_os.storage import (  # noqa: E402
    BASELINE_MIGRATION_NAME,
    BASELINE_SCHEMA_VERSION,
    LATEST_SCHEMA_VERSION,
    MigrationRequiredError,
    SCHEMA,
    SCHEMA_MIGRATIONS,
    SCHEMA_MIGRATIONS_TABLE,
    SchemaDriftError,
    SQLiteStore,
    TrustKeyError,
    UnledgeredDatabaseError,
)


class ControlAndRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "runtime.db"
        self.store = SQLiteStore(self.database)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def create_version_one_state(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript(SCHEMA_MIGRATIONS_TABLE)
            connection.executescript(SCHEMA)
            connection.execute(
                """
                INSERT INTO schema_migrations(
                    version, name, checksum, applied_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    BASELINE_SCHEMA_VERSION,
                    BASELINE_MIGRATION_NAME,
                    hashlib.sha256(SCHEMA.encode("utf-8")).hexdigest(),
                    "2026-07-28T12:00:00+00:00",
                ),
            )

    def test_initialize_registers_append_only_migrations_once(self) -> None:
        self.store.initialize()
        self.store.initialize()
        status = self.store.schema_status()
        self.assertEqual(status["integrity"], "ok")
        self.assertEqual(status["current_version"], LATEST_SCHEMA_VERSION)
        self.assertEqual(status["expected_version"], LATEST_SCHEMA_VERSION)
        self.assertEqual(self.database.stat().st_mode & 0o077, 0)
        self.assertEqual(len(status["migrations"]), 14)
        self.assertEqual(
            status["migrations"][0]["name"],
            "initial_runtime_schema",
        )
        self.assertEqual(
            status["migrations"][1]["name"],
            "execution_truth_and_outcome_verification",
        )
        self.assertEqual(
            status["migrations"][2]["name"],
            "foundation_trust_boundary_hardening",
        )
        self.assertEqual(
            status["migrations"][3]["name"],
            "durable_trust_attestation",
        )
        self.assertEqual(
            status["migrations"][4]["name"],
            "authenticated_truth_and_serializable_boundaries",
        )
        self.assertEqual(
            status["migrations"][5]["name"],
            "semantic_completion_and_paired_recovery",
        )
        self.assertEqual(
            status["migrations"][6]["name"],
            "approval_lifecycle_and_emergency_stop",
        )
        self.assertEqual(
            status["migrations"][7]["name"],
            "forbid_external_financial_execution",
        )
        self.assertEqual(
            status["migrations"][8]["name"],
            "cumulative_spend_envelopes",
        )
        self.assertEqual(
            status["migrations"][9]["name"],
            "governed_model_routing",
        )
        self.assertEqual(
            status["migrations"][10]["name"],
            "real_model_shadow_runtime",
        )
        self.assertEqual(
            status["migrations"][11]["name"],
            "affiliate_marketing_shadow_loop",
        )
        self.assertEqual(
            status["migrations"][12]["name"],
            "portfolio_capability_expansion",
        )
        self.assertEqual(
            status["migrations"][13]["name"],
            "production_and_resale_hardening",
        )
        self.assertEqual(
            status["migrations"][0]["version"],
            BASELINE_SCHEMA_VERSION,
        )

    def test_incompatible_unledgered_database_is_refused(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "CREATE TABLE tenants("
                "tenant_id TEXT PRIMARY KEY, display_name TEXT, status TEXT)"
            )
        with self.assertRaises(UnledgeredDatabaseError):
            self.store.initialize()

    def test_empty_migration_ledger_is_refused_without_bootstrapping(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript(SCHEMA_MIGRATIONS_TABLE)
        status = self.store.schema_status()
        self.assertFalse(status["migration_valid"])
        self.assertIn("migration ledger is empty", status["migration_errors"])
        with self.assertRaises(UnledgeredDatabaseError):
            self.store.initialize()
        with closing(sqlite3.connect(self.database)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    """
                )
            }
        self.assertEqual(tables, {"schema_migrations"})

    def test_backup_first_migration_preserves_existing_state(self) -> None:
        self.create_version_one_state()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                """
                INSERT INTO tenants(tenant_id, display_name, status)
                VALUES ('tenant-existing', 'Existing Tenant', 'active')
                """
            )
        backup = self.root / "upgrade-backup.db"
        self.assertEqual(self.store.migrate(backup), backup)
        status = self.store.schema_status()
        self.assertEqual(status["current_version"], LATEST_SCHEMA_VERSION)
        self.assertEqual(
            self.store.get_tenant("tenant-existing").display_name,
            "Existing Tenant",
        )
        self.assertEqual(
            SQLiteStore(backup).schema_status()["current_version"],
            BASELINE_SCHEMA_VERSION,
        )
        with closing(sqlite3.connect(self.database)) as connection, connection:
            tables = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                    """
                )
            }
        self.assertTrue(
            {
                "execution_attempts",
                "evidence_receipts",
                "outcome_verifications",
            }
            <= tables
        )

    def test_migration_six_refuses_version_one_terminal_attestations(
        self,
    ) -> None:
        applied_at = "2026-07-30T00:00:00+00:00"
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript(SCHEMA_MIGRATIONS_TABLE)
            connection.executescript(SCHEMA)
            connection.execute(
                """
                INSERT INTO schema_migrations(
                    version, name, checksum, applied_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    BASELINE_SCHEMA_VERSION,
                    BASELINE_MIGRATION_NAME,
                    hashlib.sha256(SCHEMA.encode("utf-8")).hexdigest(),
                    applied_at,
                ),
            )
            for version, name, sql in SCHEMA_MIGRATIONS:
                if version >= 6:
                    break
                connection.executescript(sql)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(
                        version, name, checksum, applied_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        version,
                        name,
                        hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                        applied_at,
                    ),
                )
            trigger_rows = connection.execute(
                """
                SELECT name, sql
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND name IN (
                      'enforce_completion_attestations_scope_insert',
                      'enforce_completion_attestation_identity_insert'
                  )
                ORDER BY name
                """
            ).fetchall()
            connection.commit()
            connection.execute("PRAGMA foreign_keys = OFF")
            for name, _ in trigger_rows:
                connection.execute(f'DROP TRIGGER "{name}"')
            connection.execute(
                """
                INSERT INTO completion_attestations(
                    verification_id, attempt_id, work_item_id, tenant_id,
                    business_id, key_id, signature, created_at
                )
                VALUES (
                    'legacy-verification', 'legacy-attempt', 'legacy-work',
                    'legacy-tenant', 'legacy-business', 'legacy-key',
                    'legacy-signature', ?
                )
                """,
                (applied_at,),
            )
            for _, sql in trigger_rows:
                connection.execute(sql)
            connection.execute("PRAGMA foreign_keys = ON")
        self.store.truth_key_path.write_text(
            "11" * 32 + "\n",
            encoding="ascii",
        )
        self.store.truth_key_path.chmod(0o600)
        backup = self.root / "version-five-backup.db"
        with self.assertRaisesRegex(
            SchemaDriftError,
            "semantic-attestation migration",
        ):
            self.store.migrate(backup)
        self.assertEqual(
            self.store.schema_status()["current_version"],
            5,
        )
        self.assertTrue(backup.exists())
        self.assertEqual(SQLiteStore(backup).schema_status()["current_version"], 5)

    def test_doctor_is_read_only_and_does_not_migrate(self) -> None:
        self.create_version_one_state()
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            command_doctor(Namespace(db=self.database))
        with closing(sqlite3.connect(self.database)) as connection, connection:
            current = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        self.assertEqual(current, BASELINE_SCHEMA_VERSION)
        report = json.loads(output.getvalue())
        self.assertEqual(report["current_version"], BASELINE_SCHEMA_VERSION)

    def test_ordinary_render_refuses_to_migrate_existing_state(self) -> None:
        self.create_version_one_state()
        with self.assertRaises(MigrationRequiredError):
            command_render(
                Namespace(
                    db=self.database,
                    html=self.root / "dashboard.html",
                )
            )
        self.assertFalse((self.root / "dashboard.html").exists())
        self.assertEqual(
            self.store.schema_status()["current_version"],
            BASELINE_SCHEMA_VERSION,
        )

    def test_ledger_without_declared_schema_is_rejected(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript(SCHEMA_MIGRATIONS_TABLE)
            rows = [
                (
                    BASELINE_SCHEMA_VERSION,
                    BASELINE_MIGRATION_NAME,
                    hashlib.sha256(SCHEMA.encode("utf-8")).hexdigest(),
                    "2026-07-29T00:00:00+00:00",
                ),
                *[
                    (
                        version,
                        name,
                        hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                        "2026-07-29T00:00:00+00:00",
                    )
                    for version, name, sql in SCHEMA_MIGRATIONS
                ],
            ]
            connection.executemany(
                """
                INSERT INTO schema_migrations(
                    version, name, checksum, applied_at
                )
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )
        status = self.store.schema_status()
        self.assertFalse(status["migration_valid"])
        self.assertTrue(
            any("schema object is missing" in error for error in status["migration_errors"])
        )
        with self.assertRaises(SchemaDriftError):
            self.store.initialize()

    def test_explicit_migrate_creates_pre_migration_backup(self) -> None:
        self.create_version_one_state()
        backup = self.root / "pre-migration.db"
        with redirect_stdout(StringIO()):
            command_migrate(
                Namespace(db=self.database, backup=backup)
            )
        self.assertEqual(
            self.store.schema_status()["current_version"],
            LATEST_SCHEMA_VERSION,
        )
        self.assertEqual(
            SQLiteStore(backup).schema_status()["current_version"],
            BASELINE_SCHEMA_VERSION,
        )

    def test_migration_blocks_writes_between_backup_and_schema_change(
        self,
    ) -> None:
        self.create_version_one_state()
        backup = self.root / "exclusive-migration.db"
        original = self.store.create_backup
        blocked = {"value": False}

        def backup_then_probe(destination):
            result = original(destination)
            with closing(
                sqlite3.connect(self.database, timeout=0.01)
            ) as concurrent:
                with self.assertRaisesRegex(
                    sqlite3.OperationalError,
                    "locked",
                ):
                    concurrent.execute(
                        """
                        INSERT INTO tenants(
                            tenant_id, display_name, status
                        )
                        VALUES (
                            'after-backup', 'After Backup', 'active'
                        )
                        """
                    )
                blocked["value"] = True
            return result

        self.store.create_backup = backup_then_probe
        self.store.migrate(backup)
        self.assertTrue(blocked["value"])
        self.assertIsNone(self.store.get_tenant("after-backup"))
        self.assertIsNone(SQLiteStore(backup).get_tenant("after-backup"))

    def test_backup_preserves_state_and_passes_integrity_check(self) -> None:
        self.store.initialize()
        self.store.upsert_tenant(
            Tenant(tenant_id="tenant-1", display_name="Tenant One")
        )
        backup_path = self.store.create_backup(self.root / "backups" / "copy.db")
        backup = SQLiteStore(backup_path)
        self.assertEqual(backup.schema_status()["integrity"], "ok")
        self.assertTrue(backup.schema_status()["migration_valid"])
        self.assertTrue(backup.truth_key_path.exists())
        self.assertEqual(backup_path.stat().st_mode & 0o077, 0)
        self.assertEqual(backup.truth_key_path.stat().st_mode & 0o077, 0)
        self.assertEqual(backup.get_tenant("tenant-1").display_name, "Tenant One")

    def test_backup_uses_one_captured_key_during_source_key_interleaving(
        self,
    ) -> None:
        self.store.initialize()
        original_key = self.store.truth_key_path.read_text(encoding="ascii")
        original_connect = self.store._connect
        calls = {"count": 0}

        def connect_with_key_interleaving(*, allow_schema_changes=False):
            calls["count"] += 1
            if calls["count"] == 2:
                self.store.truth_key_path.write_text(
                    "00" * 32 + "\n",
                    encoding="ascii",
                )
                self.store.truth_key_path.chmod(0o600)
            return original_connect(
                allow_schema_changes=allow_schema_changes
            )

        self.store._connect = connect_with_key_interleaving
        backup_path = self.store.create_backup(
            self.root / "paired-backup.db"
        )
        backup = SQLiteStore(backup_path)
        self.assertTrue(backup.schema_status()["migration_valid"])
        self.assertEqual(
            backup.truth_key_path.read_text(encoding="ascii"),
            original_key,
        )

    def test_runtime_connections_reject_schema_control(self) -> None:
        self.store.initialize()
        with self.store._connection() as connection:
            with self.assertRaisesRegex(
                sqlite3.DatabaseError,
                "not authorized",
            ):
                connection.execute("DROP TABLE work_items")
        self.assertTrue(self.store.schema_status()["migration_valid"])

    def test_missing_truth_key_is_reported_without_replacement(self) -> None:
        self.store.initialize()
        self.store.truth_key_path.unlink()
        status = self.store.schema_status()
        self.assertFalse(status["migration_valid"])
        self.assertTrue(
            any(
                "durable-truth key is missing" in error
                for error in status["migration_errors"]
            )
        )
        with self.assertRaises(TrustKeyError):
            self.store.create_backup(self.root / "missing-key-backup.db")
        with self.assertRaises(TrustKeyError):
            self.store.initialize()
        self.assertFalse(self.store.truth_key_path.exists())

    def test_backup_refuses_overwrite_and_source_destination_alias(self) -> None:
        self.store.initialize()
        with self.assertRaises(ValueError):
            self.store.create_backup(self.database)
        destination = self.root / "backup.db"
        destination.touch()
        with self.assertRaises(FileExistsError):
            self.store.create_backup(destination)

    def test_cross_tenant_authority_envelope_is_rejected(self) -> None:
        self.store.initialize()
        for suffix in ("1", "2"):
            self.store.upsert_tenant(
                Tenant(
                    tenant_id=f"tenant-{suffix}",
                    display_name=f"Tenant {suffix}",
                )
            )
            self.store.upsert_business(
                Business(
                    business_id=f"business-{suffix}",
                    tenant_id=f"tenant-{suffix}",
                    legal_name=f"Business {suffix} LLC",
                    display_name=f"Business {suffix}",
                    base_currency="USD",
                    timezone_name="UTC",
                )
            )
        with self.assertRaisesRegex(ValueError, "outside the tenant"):
            self.store.upsert_authority_envelope(
                AuthorityEnvelope(
                    envelope_id="cross-tenant-envelope",
                    tenant_id="tenant-1",
                    business_id="business-2",
                    rules=(
                        AuthorityRule(
                            action_type="record.update",
                            mode=AuthorityMode.AUTO,
                            roles=frozenset({"operator"}),
                        ),
                    ),
                )
            )

    def test_database_trigger_rejects_cross_scope_mutation(self) -> None:
        self.store.initialize()
        for suffix in ("1", "2"):
            self.store.upsert_tenant(
                Tenant(
                    tenant_id=f"tenant-{suffix}",
                    display_name=f"Tenant {suffix}",
                )
            )
            self.store.upsert_business(
                Business(
                    business_id=f"business-{suffix}",
                    tenant_id=f"tenant-{suffix}",
                    legal_name=f"Business {suffix} LLC",
                    display_name=f"Business {suffix}",
                    base_currency="USD",
                    timezone_name="UTC",
                )
            )
        with closing(sqlite3.connect(self.database)) as connection, connection:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "ownership mismatch",
            ):
                connection.execute(
                    """
                    INSERT INTO audit_records(
                        audit_id, tenant_id, business_id, record_type,
                        details_json, created_at
                    )
                    VALUES (
                        'audit-cross', 'tenant-1', 'business-2',
                        'forged', '{}', '2026-07-29T00:00:00+00:00'
                    )
                    """
                )
            connection.execute(
                """
                INSERT INTO events(
                    event_id, tenant_id, business_id, source, actor_id, kind,
                    occurred_at, payload_json, idempotency_key, received_at,
                    event_fingerprint
                )
                VALUES (
                    'event-parent', 'tenant-1', 'business-1', 'test',
                    'external-actor', 'test.event',
                    '2026-07-29T00:00:00+00:00', '{}', 'event-parent',
                    '2026-07-29T00:00:00+00:00', 'fingerprint'
                )
                """
            )
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "parent identity",
            ):
                connection.execute(
                    """
                    INSERT INTO event_processing(
                        event_id, tenant_id, business_id, status, claimed_by,
                        lease_expires_at, created_at, updated_at
                    )
                    VALUES (
                        'event-parent', 'tenant-2', 'business-2', 'processing',
                        'forged-worker', '2026-07-29T01:00:00+00:00',
                        '2026-07-29T00:00:00+00:00',
                        '2026-07-29T00:00:00+00:00'
                    )
                    """
                )
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "append-only",
            ):
                connection.execute(
                    """
                    UPDATE events
                    SET kind = 'approval.requested',
                        payload_json = '{"forged": true}',
                        event_fingerprint = 'recomputed-by-attacker'
                    WHERE event_id = 'event-parent'
                    """
                )

    def test_database_trigger_rejects_cross_scope_actor_links(self) -> None:
        self.store.initialize()
        self.store.upsert_tenant(
            Tenant(tenant_id="tenant-1", display_name="Tenant One")
        )
        self.store.upsert_tenant(
            Tenant(tenant_id="tenant-2", display_name="Tenant Two")
        )
        for suffix in ("1", "2"):
            self.store.upsert_business(
                Business(
                    business_id=f"business-{suffix}",
                    tenant_id="tenant-1",
                    legal_name=f"Business {suffix} LLC",
                    display_name=f"Business {suffix}",
                    base_currency="USD",
                    timezone_name="UTC",
                )
            )
        with closing(sqlite3.connect(self.database)) as connection, connection:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "membership crosses tenant",
            ):
                connection.execute(
                    """
                    INSERT INTO actors(
                        actor_id, tenant_id, actor_type, roles_json,
                        business_ids_json, enabled
                    )
                    VALUES (
                        'actor-forged', 'tenant-1', 'agent', '[]',
                        '["business-outside"]', 1
                    )
                    """
                )
        self.store.upsert_actor(
            ActorIdentity(
                actor_id="actor-1",
                tenant_id="tenant-1",
                actor_type=ActorType.AGENT,
                roles=frozenset({"operator"}),
                business_ids=frozenset({"business-1"}),
            )
        )
        self.store.upsert_capability(
            capability_id="records.write",
            display_name="Write records",
            description="Write a bounded record.",
            required_role="operator",
            action_types=("record.update",),
        )
        self.store.assign_capability(
            tenant_id="tenant-1",
            business_id="business-1",
            actor_id="actor-1",
            capability_id="records.write",
        )
        with closing(sqlite3.connect(self.database)) as connection, connection:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "actor crosses tenant/business",
            ):
                connection.execute(
                    """
                    INSERT INTO agent_capabilities(
                        tenant_id, business_id, actor_id, capability_id, enabled
                    )
                    VALUES (
                        'tenant-1', 'business-2', 'actor-1',
                        'records.write', 1
                    )
                    """
                )
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "cannot orphan scoped state",
            ):
                connection.execute(
                    """
                    UPDATE actors
                    SET business_ids_json = '["business-2"]'
                    WHERE actor_id = 'actor-1'
                    """
                )
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "cannot move across tenants",
            ):
                connection.execute(
                    """
                    UPDATE businesses
                    SET tenant_id = 'tenant-2'
                    WHERE business_id = 'business-1'
                    """
                )

    def test_requirements_registry_has_unique_valid_ids_and_model_controls(self) -> None:
        registry = json.loads(
            (ROOT / "docs" / "requirements" / "registry.json").read_text()
        )
        requirements = registry["requirements"]
        identifiers = [requirement["id"] for requirement in requirements]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(
            set(requirement["status"] for requirement in requirements)
            <= set(registry["allowed_statuses"])
        )
        routing = [
            requirement
            for requirement in requirements
            if requirement["id"].startswith("AOS-LLM-")
        ]
        self.assertGreaterEqual(len(routing), 3)
        self.assertTrue(all(requirement["goal"] == 10 for requirement in routing))
        shadow = next(
            requirement
            for requirement in requirements
            if requirement["id"] == "AOS-MODEL-001"
        )
        self.assertEqual(shadow["goal"], 11)
        self.assertEqual(shadow["status"], "verified")
        self.assertTrue(shadow["evidence"])
        affiliate = next(
            requirement
            for requirement in requirements
            if requirement["id"] == "AOS-B75-001"
        )
        self.assertEqual(affiliate["goal"], 12)
        self.assertEqual(affiliate["status"], "verified")
        self.assertTrue(affiliate["evidence"])
        portfolio = next(
            requirement
            for requirement in requirements
            if requirement["id"] == "AOS-PORT-001"
        )
        self.assertEqual(portfolio["goal"], 13)
        self.assertEqual(portfolio["status"], "verified")
        self.assertTrue(portfolio["evidence"])
        communications = next(
            requirement
            for requirement in requirements
            if requirement["id"] == "AOS-COMMS-001"
        )
        self.assertEqual(communications["goal"], 13)
        self.assertEqual(communications["status"], "verified")
        self.assertTrue(communications["evidence"])
        for requirement_id in ("AOS-PROD-001", "AOS-PROD-002", "AOS-MIG-001"):
            production = next(
                requirement for requirement in requirements
                if requirement["id"] == requirement_id
            )
            self.assertEqual(production["goal"], 14)
            self.assertEqual(production["status"], "verified")
            self.assertTrue(production["evidence"])
        verification = [
            requirement
            for requirement in requirements
            if requirement["id"].startswith("AOS-VERIFY-")
        ]
        self.assertGreaterEqual(len(verification), 3)
        self.assertTrue(
            all(
                requirement["goal"] == 8
                and requirement["status"] == "verified"
                and requirement["evidence"]
                for requirement in verification
            )
        )


if __name__ == "__main__":
    unittest.main()
