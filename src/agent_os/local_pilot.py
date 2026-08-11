"""Local-only controls for the bounded PostgreSQL pilot.

The local pilot runs on an internal Docker network and accepts only normalized
aggregate files. It is an evidence-producing canary, not a production
qualification or an external-account connector.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Sequence

from .contracts import ActorIdentity, ActorType, Business, Tenant
from .postgresql import PostgreSQLPilotError, PostgreSQLPilotStore, _driver


GIB = 1024 ** 3
DEFAULT_MINIMUM_FREE_BYTES = 50 * GIB
DEFAULT_MAXIMUM_DATABASE_BYTES = 1 * GIB
LOCAL_DATABASE = "agent_os_pilot"
LOCAL_HOST = "agent-os-local-pilot-postgres"
LOCAL_TENANT_ID = "tenant-local-pilot"
LOCAL_BUSINESS_ID = "business-local-pilot"
LOCAL_PRODUCER_ID = "operations-local-pilot"
LOCAL_VERIFIER_ID = "qa-local-pilot"
LOCAL_OWNER_ID = "owner-local-pilot"
LOCAL_ROLES = {
    "migration": "aos_local_migration",
    "runtime": "aos_local_runtime",
    "backup": "aos_local_backup",
}


@dataclass(frozen=True)
class StorageGuardReport:
    free_bytes: int
    database_bytes: int
    minimum_free_bytes: int
    maximum_database_bytes: int
    allowed: bool
    reasons: tuple[str, ...]


def evaluate_storage_guard(
    *,
    free_bytes: int,
    database_bytes: int,
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
    maximum_database_bytes: int = DEFAULT_MAXIMUM_DATABASE_BYTES,
) -> StorageGuardReport:
    """Return a fail-closed local storage decision without changing state."""
    values = (free_bytes, database_bytes, minimum_free_bytes, maximum_database_bytes)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
           for value in values):
        raise ValueError("storage guard values must be non-negative integers")
    reasons: list[str] = []
    if free_bytes < minimum_free_bytes:
        reasons.append("host free space is below the local-pilot stop line")
    if database_bytes > maximum_database_bytes:
        reasons.append("local pilot database exceeds its size ceiling")
    return StorageGuardReport(
        free_bytes=free_bytes,
        database_bytes=database_bytes,
        minimum_free_bytes=minimum_free_bytes,
        maximum_database_bytes=maximum_database_bytes,
        allowed=not reasons,
        reasons=tuple(reasons),
    )


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _write_once(path: Path, value: str) -> None:
    if path.exists():
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise PostgreSQLPilotError(f"local secret has unsafe permissions: {path.name}")
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def initialize_secrets(directory: Path) -> dict[str, Any]:
    """Create private local-only credentials without replacing existing values."""
    _secure_directory(directory)
    required = {
        "postgres.env", "admin.dsn", "migration.dsn", "runtime.dsn",
        "backup.dsn", "backup.env",
    }
    existing = {path.name for path in directory.iterdir() if path.is_file()}
    if existing & required and not required.issubset(existing):
        missing = sorted(required - existing)
        raise PostgreSQLPilotError(
            f"partial local secret set; restore or remove it manually: {missing}"
        )
    if required.issubset(existing):
        for name in required:
            _write_once(directory / name, "")
        return {"created": False, "files": sorted(required)}

    passwords = {
        "admin": secrets.token_hex(24),
        **{name: secrets.token_hex(24) for name in LOCAL_ROLES},
    }

    def dsn(role: str, password: str) -> str:
        return (
            f"postgresql://{role}:{password}@{LOCAL_HOST}:5432/"
            f"{LOCAL_DATABASE}?sslmode=disable\n"
        )

    values = {
        "postgres.env": (
            f"POSTGRES_PASSWORD={passwords['admin']}\n"
            f"POSTGRES_DB={LOCAL_DATABASE}\n"
            "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256\n"
        ),
        "admin.dsn": dsn("postgres", passwords["admin"]),
        "migration.dsn": dsn(LOCAL_ROLES["migration"], passwords["migration"]),
        "runtime.dsn": dsn(LOCAL_ROLES["runtime"], passwords["runtime"]),
        "backup.dsn": dsn(LOCAL_ROLES["backup"], passwords["backup"]),
        "backup.env": (
            f"PGHOST={LOCAL_HOST}\nPGPORT=5432\nPGDATABASE={LOCAL_DATABASE}\n"
            f"PGUSER={LOCAL_ROLES['backup']}\nPGPASSWORD={passwords['backup']}\n"
        ),
    }
    for name, value in values.items():
        _write_once(directory / name, value)
    return {"created": True, "files": sorted(required)}


def _read_secret(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > 64 * 1024:
        raise PostgreSQLPilotError(f"local secret is unavailable: {path.name}")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise PostgreSQLPilotError(f"local secret has unsafe permissions: {path.name}")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise PostgreSQLPilotError(f"local secret is empty: {path.name}")
    return value


def _role_password(dsn: str, expected_role: str) -> str:
    psycopg, _, _ = _driver()
    parsed = psycopg.conninfo.conninfo_to_dict(dsn)
    if parsed.get("user") != expected_role or not parsed.get("password"):
        raise PostgreSQLPilotError(f"local {expected_role} DSN is malformed")
    return str(parsed["password"])


def bootstrap_local_pilot(directory: Path) -> dict[str, Any]:
    """Create separated local roles, schema, fixed scope, and runtime binding."""
    psycopg, sql, _ = _driver()
    admin_dsn = _read_secret(directory / "admin.dsn")
    role_dsns = {
        name: _read_secret(directory / f"{name}.dsn") for name in LOCAL_ROLES
    }
    passwords = {
        name: _role_password(role_dsns[name], role) for name, role in LOCAL_ROLES.items()
    }
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        for name, role_name in LOCAL_ROLES.items():
            role = sql.Identifier(role_name)
            password = sql.Literal(passwords[name])
            bypass = sql.SQL("BYPASSRLS") if name in {"migration", "backup"} else sql.SQL("NOBYPASSRLS")
            if connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (role_name,)
            ).fetchone() is None:
                connection.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOINHERIT {}"
                    ).format(role, password, bypass)
                )
            else:
                connection.execute(
                    sql.SQL(
                        "ALTER ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOINHERIT {}"
                    ).format(role, password, bypass)
                )
        connection.execute(
            sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                sql.Identifier(LOCAL_DATABASE), sql.Identifier(LOCAL_ROLES["migration"])
            )
        )

    migration_dsn = role_dsns["migration"]
    checksum = PostgreSQLPilotStore.apply_schema(migration_dsn)
    PostgreSQLPilotStore.bootstrap_scope(
        migration_dsn,
        tenant=Tenant(LOCAL_TENANT_ID, "Local Pilot Tenant"),
        business=Business(
            LOCAL_BUSINESS_ID, LOCAL_TENANT_ID, "Local Pilot Business",
            "Local Pilot", "USD", "UTC",
        ),
        actors=(
            ActorIdentity(
                LOCAL_PRODUCER_ID, LOCAL_TENANT_ID, ActorType.AGENT,
                frozenset({"commerce", "operations", "platform-reliability"}),
                frozenset({LOCAL_BUSINESS_ID}),
            ),
            ActorIdentity(
                LOCAL_VERIFIER_ID, LOCAL_TENANT_ID, ActorType.AGENT,
                frozenset({"qa", "verifier"}), frozenset({LOCAL_BUSINESS_ID}),
            ),
            ActorIdentity(
                LOCAL_OWNER_ID, LOCAL_TENANT_ID, ActorType.HUMAN,
                frozenset({"business-owner", "operations"}),
                frozenset({LOCAL_BUSINESS_ID}),
            ),
        ),
    )
    PostgreSQLPilotStore.grant_runtime_role(
        migration_dsn, LOCAL_ROLES["runtime"],
        tenant_id=LOCAL_TENANT_ID, business_id=LOCAL_BUSINESS_ID,
    )
    with psycopg.connect(migration_dsn) as connection:
        backup = sql.Identifier(LOCAL_ROLES["backup"])
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(LOCAL_DATABASE), backup
            )
        )
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(backup))
        connection.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(backup)
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(backup)
        )
        connection.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}"
            ).format(backup)
        )
    return {
        "business_id": LOCAL_BUSINESS_ID,
        "external_side_effects_enabled": False,
        "roles": dict(LOCAL_ROLES),
        "schema_checksum": checksum,
        "tenant_id": LOCAL_TENANT_ID,
    }


def local_status(
    directory: Path,
    *,
    host_free_bytes: int,
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
    maximum_database_bytes: int = DEFAULT_MAXIMUM_DATABASE_BYTES,
) -> dict[str, Any]:
    runtime_dsn = _read_secret(directory / "runtime.dsn")
    store = PostgreSQLPilotStore(
        runtime_dsn, tenant_id=LOCAL_TENANT_ID, business_id=LOCAL_BUSINESS_ID
    )
    psycopg, _, dict_row = _driver()
    with psycopg.connect(runtime_dsn, row_factory=dict_row) as connection:
        store._assert_bound_scope(connection)
        database_bytes = int(connection.execute(
            "SELECT pg_database_size(current_database()) AS database_bytes"
        ).fetchone()["database_bytes"])
    guard = evaluate_storage_guard(
        free_bytes=host_free_bytes,
        database_bytes=database_bytes,
        minimum_free_bytes=minimum_free_bytes,
        maximum_database_bytes=maximum_database_bytes,
    )
    return {
        "database_bytes": database_bytes,
        "external_side_effects_enabled": False,
        "schema": store.schema_status(),
        "snapshot": store.pilot_snapshot(),
        "storage_guard": asdict(guard),
    }


def rotate_backups(directory: Path, keep: int = 7) -> dict[str, Any]:
    if keep < 1:
        raise ValueError("at least one backup must be retained")
    _secure_directory(directory)
    backups = sorted(directory.glob("agent-os-local-pilot-*.dump"), reverse=True)
    removed: list[str] = []
    for path in backups[keep:]:
        path.unlink()
        removed.append(path.name)
    return {"kept": min(len(backups), keep), "removed": removed}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guarded local Agent OS pilot")
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init-secrets")
    initialize.add_argument("--directory", type=Path, required=True)
    bootstrap = commands.add_parser("bootstrap")
    bootstrap.add_argument("--directory", type=Path, required=True)
    status_command = commands.add_parser("status")
    status_command.add_argument("--directory", type=Path, required=True)
    status_command.add_argument("--host-free-bytes", type=int, required=True)
    status_command.add_argument(
        "--minimum-free-bytes", type=int, default=DEFAULT_MINIMUM_FREE_BYTES
    )
    status_command.add_argument(
        "--maximum-database-bytes", type=int, default=DEFAULT_MAXIMUM_DATABASE_BYTES
    )
    rotate = commands.add_parser("rotate-backups")
    rotate.add_argument("--directory", type=Path, required=True)
    rotate.add_argument("--keep", type=int, default=7)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = _parser().parse_args(argv)
    if arguments.command == "init-secrets":
        result = initialize_secrets(arguments.directory)
    elif arguments.command == "bootstrap":
        result = bootstrap_local_pilot(arguments.directory)
    elif arguments.command == "status":
        result = local_status(
            arguments.directory,
            host_free_bytes=arguments.host_free_bytes,
            minimum_free_bytes=arguments.minimum_free_bytes,
            maximum_database_bytes=arguments.maximum_database_bytes,
        )
        if not result["storage_guard"]["allowed"]:
            print(json.dumps(result, sort_keys=True))
            raise SystemExit(2)
    elif arguments.command == "rotate-backups":
        result = rotate_backups(arguments.directory, arguments.keep)
    else:  # pragma: no cover - argparse rejects this path
        raise AssertionError("unreachable local pilot command")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
