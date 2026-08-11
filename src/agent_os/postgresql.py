"""Scoped PostgreSQL adapter for the Production Pilot 1 read-only canary.

The pilot adapter intentionally implements only the Goal 13 aggregate canary
and Goal 14 qualification/cutover surface. Unsupported Agent OS write paths are
absent, not silently redirected to SQLite.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterator, Sequence

from .contracts import ActorIdentity, ActorType, Business, Tenant
from .portfolio import AggregatePerformanceService


PILOT_SCHEMA_VERSION = 1
PILOT_SCHEMA_NAME = "postgresql_read_only_canary"
_SOURCE_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "deployment"
    / "postgresql"
    / "pilot-schema.sql"
)
PILOT_SCHEMA_PATH = Path(
    os.environ.get(
        "AOS_PILOT_SCHEMA_PATH",
        _SOURCE_SCHEMA_PATH
        if _SOURCE_SCHEMA_PATH.is_file()
        else Path(sys.prefix)
        / "share"
        / "agent-os"
        / "deployment"
        / "postgresql"
        / "pilot-schema.sql",
    )
)


class PostgreSQLPilotError(RuntimeError):
    """Raised when the scoped production pilot cannot fail closed."""


def _driver() -> tuple[Any, Any, Any]:
    try:
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
    except ImportError as error:
        raise PostgreSQLPilotError(
            "PostgreSQL pilot requires psycopg 3; install the project dependencies"
        ) from error
    return psycopg, sql, dict_row


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _convert(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {key: _convert(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_convert(item) for item in value]
    return value


class _CursorAdapter:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self) -> dict[str, Any] | None:
        row = self._cursor.fetchone()
        return _convert(row) if row is not None else None

    def fetchall(self) -> list[dict[str, Any]]:
        return [_convert(row) for row in self._cursor.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class _ConnectionAdapter:
    def __init__(self, connection: Any):
        self._connection = connection

    @staticmethod
    def _translate(statement: str) -> str:
        # Pilot statements are controlled source strings and contain no literal
        # question marks. Psycopg uses %s placeholders.
        return statement.replace("?", "%s")

    def execute(
        self,
        statement: str,
        parameters: Sequence[Any] | None = None,
    ) -> _CursorAdapter:
        cursor = self._connection.execute(
            self._translate(statement), tuple(parameters or ())
        )
        return _CursorAdapter(cursor)


class PostgreSQLPilotStore:
    """One-tenant/business PostgreSQL adapter with mandatory RLS context."""

    def __init__(self, dsn: str, *, tenant_id: str, business_id: str):
        if not dsn or not tenant_id.strip() or not business_id.strip():
            raise PostgreSQLPilotError("PostgreSQL DSN and scope are required")
        self._dsn = dsn
        self.tenant_id = tenant_id
        self.business_id = business_id
        self._active_connection: Any | None = None

    @classmethod
    def apply_schema(cls, admin_dsn: str) -> str:
        """Apply the idempotent pilot schema with an attested checksum."""
        psycopg, _, dict_row = _driver()
        schema = PILOT_SCHEMA_PATH.read_text(encoding="utf-8")
        checksum = hashlib.sha256(schema.encode()).hexdigest()
        with psycopg.connect(admin_dsn, row_factory=dict_row) as connection:
            connection.execute(schema)
            row = connection.execute(
                "SELECT name, checksum FROM pilot_schema_migrations WHERE version=%s",
                (PILOT_SCHEMA_VERSION,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """INSERT INTO pilot_schema_migrations(
                         version,name,checksum,applied_at
                       ) VALUES (%s,%s,%s,%s)""",
                    (PILOT_SCHEMA_VERSION, PILOT_SCHEMA_NAME, checksum, _utc_now()),
                )
            elif row["name"] != PILOT_SCHEMA_NAME or row["checksum"] != checksum:
                raise PostgreSQLPilotError(
                    "applied PostgreSQL pilot schema differs from source"
                )
        return checksum

    @classmethod
    def grant_runtime_role(
        cls,
        admin_dsn: str,
        role_name: str,
        *,
        tenant_id: str,
        business_id: str,
    ) -> None:
        """Bind and grant the bounded pilot surface to an existing login role."""
        if not role_name or role_name != role_name.strip():
            raise PostgreSQLPilotError("runtime role name is invalid")
        psycopg, sql, _ = _driver()
        readable = (
            "pilot_schema_migrations", "tenants", "businesses", "actors",
            "capability_pack_acceptances", "evidence_records",
            "aggregate_performance_snapshots",
            "aggregate_performance_verifications", "production_qualifications",
            "legacy_cutover_plans", "legacy_cutover_events",
        )
        writable = (
            "capability_pack_acceptances", "evidence_records",
            "aggregate_performance_snapshots",
            "aggregate_performance_verifications", "production_qualifications",
            "legacy_cutover_plans", "legacy_cutover_events",
        )
        with psycopg.connect(admin_dsn) as connection:
            role = sql.Identifier(role_name)
            role_status = connection.execute(
                """SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,
                          EXISTS(
                            SELECT 1 FROM pg_auth_members membership
                            WHERE membership.member=pg_roles.oid
                          ) AS has_membership
                   FROM pg_roles WHERE rolname=%s""",
                (role_name,),
            ).fetchone()
            if (
                role_status is None
                or not role_status[0]
                or any(role_status[1:])
            ):
                raise PostgreSQLPilotError(
                    "runtime role must be an unprivileged standalone login"
                )
            scoped = connection.execute(
                "SELECT 1 FROM businesses WHERE tenant_id=%s AND business_id=%s",
                (tenant_id, business_id),
            ).fetchone()
            if scoped is None:
                raise PostgreSQLPilotError("runtime role binding scope is missing")
            connection.execute(
                """INSERT INTO pilot_runtime_bindings(
                     role_name,tenant_id,business_id
                   ) VALUES (%s,%s,%s)
                   ON CONFLICT(role_name) DO UPDATE SET
                     tenant_id=excluded.tenant_id,
                     business_id=excluded.business_id""",
                (role_name, tenant_id, business_id),
            )
            for table in readable:
                connection.execute(
                    sql.SQL("GRANT SELECT ON {} TO {}").format(
                        sql.Identifier(table), role
                    )
                )
            for table in writable:
                connection.execute(
                    sql.SQL("GRANT INSERT ON {} TO {}").format(
                        sql.Identifier(table), role
                    )
                )
            connection.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(role)
            )
            for sequence in (
                "production_qualifications_rowid_seq",
                "legacy_cutover_events_rowid_seq",
            ):
                connection.execute(
                    sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {} TO {}").format(
                        sql.Identifier(sequence), role
                    )
                )
            connection.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION aos_scope_tenant() TO {}").format(role)
            )
            connection.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION aos_scope_business() TO {}").format(role)
            )

    @classmethod
    def bootstrap_scope(
        cls,
        admin_dsn: str,
        *,
        tenant: Tenant,
        business: Business,
        actors: Sequence[ActorIdentity],
    ) -> None:
        """Onboard identity records through the migration/admin boundary."""
        if business.tenant_id != tenant.tenant_id or any(
            actor.tenant_id != tenant.tenant_id
            or business.business_id not in actor.business_ids
            for actor in actors
        ):
            raise PostgreSQLPilotError("bootstrap identities cross scope")
        psycopg, _, _ = _driver()
        with psycopg.connect(admin_dsn) as connection:
            connection.execute(
                """INSERT INTO tenants(tenant_id,display_name,status)
                   VALUES (%s,%s,%s)
                   ON CONFLICT(tenant_id) DO UPDATE SET
                     display_name=excluded.display_name,status=excluded.status""",
                (tenant.tenant_id, tenant.display_name, tenant.status.value),
            )
            connection.execute(
                """INSERT INTO businesses(
                     business_id,tenant_id,legal_name,display_name,
                     base_currency,timezone_name
                   ) VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(business_id) DO UPDATE SET
                     legal_name=excluded.legal_name,
                     display_name=excluded.display_name,
                     base_currency=excluded.base_currency,
                     timezone_name=excluded.timezone_name""",
                (
                    business.business_id, business.tenant_id,
                    business.legal_name, business.display_name,
                    business.base_currency, business.timezone_name,
                ),
            )
            for actor in actors:
                connection.execute(
                    """INSERT INTO actors(
                         actor_id,tenant_id,actor_type,roles_json,
                         business_ids_json,enabled
                       ) VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(actor_id) DO UPDATE SET
                         actor_type=excluded.actor_type,
                         roles_json=excluded.roles_json,
                         business_ids_json=excluded.business_ids_json,
                         enabled=excluded.enabled""",
                    (
                        actor.actor_id, actor.tenant_id, actor.actor_type.value,
                        json.dumps(sorted(actor.roles)),
                        json.dumps(sorted(actor.business_ids)), int(actor.enabled),
                    ),
                )

    @contextmanager
    def _connection(self) -> Iterator[_ConnectionAdapter]:
        if self._active_connection is not None:
            yield _ConnectionAdapter(self._active_connection)
            return
        psycopg, _, dict_row = _driver()
        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            self._assert_bound_scope(connection)
            yield _ConnectionAdapter(connection)

    @contextmanager
    def _immediate_connection(self) -> Iterator[_ConnectionAdapter]:
        if self._active_connection is not None:
            yield _ConnectionAdapter(self._active_connection)
            return
        psycopg, _, dict_row = _driver()
        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            self._assert_bound_scope(connection)
            yield _ConnectionAdapter(connection)

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """Hold one serializable transaction across a complete canary import."""
        if self._active_connection is not None:
            raise PostgreSQLPilotError("nested pilot transactions are forbidden")
        psycopg, _, dict_row = _driver()
        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            self._assert_bound_scope(connection)
            self._active_connection = connection
            try:
                yield
            finally:
                self._active_connection = None

    def _assert_bound_scope(self, connection: Any) -> None:
        row = connection.execute(
            "SELECT aos_scope_tenant() AS tenant_id, aos_scope_business() AS business_id"
        ).fetchone()
        if (
            row is None
            or row["tenant_id"] != self.tenant_id
            or row["business_id"] != self.business_id
        ):
            raise PostgreSQLPilotError(
                "database login is not bound to the requested tenant/business"
            )

    def _require_business_scope(
        self,
        connection: _ConnectionAdapter,
        *,
        tenant_id: str,
        business_id: str,
    ) -> None:
        if tenant_id != self.tenant_id or business_id != self.business_id:
            raise PostgreSQLPilotError("requested business is outside adapter scope")
        if connection.execute(
            "SELECT 1 AS present FROM businesses WHERE tenant_id=? AND business_id=?",
            (tenant_id, business_id),
        ).fetchone() is None:
            raise PostgreSQLPilotError("scoped business is missing")

    def get_business(self, business_id: str) -> Business | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM businesses WHERE business_id=?", (business_id,)
            ).fetchone()
        if row is None:
            return None
        return Business(
            row["business_id"], row["tenant_id"], row["legal_name"],
            row["display_name"], row["base_currency"], row["timezone_name"],
        )

    def get_actor(self, actor_id: str) -> ActorIdentity | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM actors WHERE actor_id=?", (actor_id,)
            ).fetchone()
        if row is None:
            return None
        return ActorIdentity(
            actor_id=row["actor_id"], tenant_id=row["tenant_id"],
            actor_type=ActorType(row["actor_type"]),
            roles=frozenset(json.loads(row["roles_json"])),
            business_ids=frozenset(json.loads(row["business_ids_json"])),
            enabled=bool(row["enabled"]),
        )

    def insert_evidence(
        self,
        *,
        evidence_id: str,
        tenant_id: str,
        business_id: str,
        source_type: str,
        source_ref: str,
        statement: str,
        facts: dict[str, Any],
        confidence: Decimal,
        observed_at: datetime,
    ) -> None:
        if not Decimal("0") <= confidence <= Decimal("1"):
            raise PostgreSQLPilotError("evidence confidence is invalid")
        with self._immediate_connection() as connection:
            self._require_business_scope(
                connection, tenant_id=tenant_id, business_id=business_id
            )
            connection.execute(
                """INSERT INTO evidence_records(
                     evidence_id,tenant_id,business_id,source_type,source_ref,
                     statement,facts_json,confidence,observed_at,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    evidence_id, tenant_id, business_id, source_type, source_ref,
                    statement, json.dumps(facts, sort_keys=True), confidence,
                    observed_at.astimezone(timezone.utc).isoformat(), _utc_now(),
                ),
            )

    def get_evidence(self, evidence_ids: tuple[str, ...]) -> list[dict[str, Any]]:
        if not evidence_ids:
            return []
        placeholders = ",".join("?" for _ in evidence_ids)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM evidence_records WHERE evidence_id IN ({placeholders}) ORDER BY evidence_id",
                evidence_ids,
            ).fetchall()
        results = []
        for row in rows:
            row["facts"] = json.loads(row.pop("facts_json"))
            row["confidence"] = Decimal(row["confidence"])
            results.append(row)
        return results

    def schema_status(self) -> dict[str, Any]:
        schema = PILOT_SCHEMA_PATH.read_text(encoding="utf-8")
        checksum = hashlib.sha256(schema.encode()).hexdigest()
        errors: list[str] = []
        try:
            with self._connection() as connection:
                migration = connection.execute(
                    "SELECT * FROM pilot_schema_migrations WHERE version=?",
                    (PILOT_SCHEMA_VERSION,),
                ).fetchone()
                if migration is None:
                    errors.append("pilot migration is missing")
                elif (
                    migration["name"] != PILOT_SCHEMA_NAME
                    or migration["checksum"] != checksum
                ):
                    errors.append("pilot migration checksum or name differs")
                required = {
                    "tenants", "businesses", "pilot_runtime_bindings", "actors",
                    "evidence_records",
                    "capability_pack_acceptances",
                    "aggregate_performance_snapshots",
                    "aggregate_performance_verifications",
                    "production_qualifications", "legacy_cutover_plans",
                    "legacy_cutover_events",
                }
                tables = {
                    row["tablename"] for row in connection.execute(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    ).fetchall()
                }
                missing = sorted(required - tables)
                if missing:
                    errors.append(f"pilot tables are missing: {missing}")
                rls = connection.execute(
                    """SELECT relname,relrowsecurity,relforcerowsecurity
                       FROM pg_class WHERE relname IN (
                         'tenants','businesses','actors','evidence_records',
                         'aggregate_performance_snapshots',
                         'aggregate_performance_verifications',
                         'production_qualifications','legacy_cutover_plans',
                         'legacy_cutover_events'
                       )"""
                ).fetchall()
                if any(
                    not row["relrowsecurity"] or not row["relforcerowsecurity"]
                    for row in rls
                ) or len(rls) != 9:
                    errors.append("pilot row-level security is incomplete")
                for row in connection.execute(
                    "SELECT * FROM aggregate_performance_snapshots"
                ).fetchall():
                    payload = AggregatePerformanceService._payload(row)
                    if _digest(payload) != row["snapshot_hash"]:
                        errors.append("aggregate snapshot evidence is inconsistent")
                        break
                for row in connection.execute(
                    "SELECT checks_json,checks_hash,decision FROM production_qualifications"
                ).fetchall():
                    checks = json.loads(row["checks_json"])
                    digest = hashlib.sha256(
                        json.dumps(checks, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                    if (
                        digest != row["checks_hash"]
                        or (row["decision"] == "passed") != all(checks.values())
                    ):
                        errors.append("production qualification evidence is inconsistent")
                        break
        except PostgreSQLPilotError as error:
            errors.append(f"PostgreSQL pilot check failed: {error}")
        except Exception:
            errors.append("PostgreSQL pilot check failed")
        return {
            "adapter": "postgresql-pilot",
            "database": "postgresql://redacted",
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "current_version": PILOT_SCHEMA_VERSION if not errors else 0,
            "expected_version": PILOT_SCHEMA_VERSION,
            "migration_valid": not errors,
            "migration_errors": errors,
            "external_side_effects_enabled": False,
        }

    def pilot_snapshot(self) -> dict[str, Any]:
        with self._connection() as connection:
            counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) AS count FROM {table}"
                ).fetchone()["count"]
                for table in (
                    "evidence_records", "aggregate_performance_snapshots",
                    "aggregate_performance_verifications",
                    "production_qualifications", "legacy_cutover_plans",
                )
            }
            aggregates = connection.execute(
                """SELECT snapshot.snapshot_id,snapshot.channel,snapshot.offer_key,
                          snapshot.window_start,snapshot.window_end,
                          snapshot.outbound_clicks,snapshot.conversions,
                          snapshot.evidence_class,verification.decision
                   FROM aggregate_performance_snapshots snapshot
                   LEFT JOIN aggregate_performance_verifications verification
                     ON verification.snapshot_id=snapshot.snapshot_id
                   ORDER BY snapshot.imported_at DESC LIMIT 20"""
            ).fetchall()
            qualifications = connection.execute(
                """SELECT kind,release_version,decision,qualified_at
                   FROM production_qualifications ORDER BY rowid DESC LIMIT 20"""
            ).fetchall()
            cutovers = connection.execute(
                """SELECT plan.plan_id,plan.capability_id,plan.mode,event.stage,
                          plan.external_side_effects_enabled
                   FROM legacy_cutover_plans plan
                   JOIN legacy_cutover_events event ON event.rowid=(
                     SELECT latest.rowid FROM legacy_cutover_events latest
                     WHERE latest.plan_id=plan.plan_id
                     ORDER BY latest.rowid DESC LIMIT 1
                   ) ORDER BY event.rowid DESC LIMIT 20"""
            ).fetchall()
        return {
            "adapter": "postgresql-pilot",
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "generated_at": _utc_now(),
            "counts": counts,
            "aggregates": aggregates,
            "qualifications": qualifications,
            "cutovers": cutovers,
            "external_side_effects_enabled": False,
        }
