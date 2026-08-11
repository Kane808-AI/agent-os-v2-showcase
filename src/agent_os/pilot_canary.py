"""One-shot importer for the PostgreSQL read-only production canary.

The worker consumes an already exported, normalized report file. It has no
Pinterest, Amazon, browser, advertising, messaging, or publishing client.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from .portfolio import (
    AggregatePerformanceService,
    AggregateSnapshot,
    CapabilityPackCatalog,
)
from .postgresql import PostgreSQLPilotError, PostgreSQLPilotStore


MAX_INPUT_BYTES = 256 * 1024
ROOT = Path(
    os.environ.get("AOS_REPOSITORY_ROOT", Path(__file__).resolve().parents[2])
)


class PilotCanaryError(ValueError):
    """Raised when a normalized canary input fails closed."""


def _exact(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise PilotCanaryError(f"{label} fields are invalid")


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PilotCanaryError(f"{label} must be a non-empty trimmed string")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_identifier(value, label))
    except ValueError as error:
        raise PilotCanaryError(f"{label} is not an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PilotCanaryError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PilotCanaryError(f"{label} must be a non-negative integer")
    return value


def validate_report(report: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the exact read-only Pinterest/Amazon contract."""
    if not isinstance(report, dict):
        raise PilotCanaryError("canary input must be an object")
    _exact(
        report,
        {
            "schema_version", "mode", "tenant_id", "business_id",
            "producer_id", "verifier_id", "offer_key", "source_ref",
            "window_start", "window_end", "observed_at", "pinterest", "amazon",
        },
        "canary input",
    )
    if report["schema_version"] != 1 or report["mode"] != "read_only":
        raise PilotCanaryError("canary input must use schema 1 and read_only mode")
    pinterest = report["pinterest"]
    amazon = report["amazon"]
    if not isinstance(pinterest, dict) or not isinstance(amazon, dict):
        raise PilotCanaryError("source aggregates must be objects")
    _exact(
        pinterest,
        {"impressions", "engagements", "content_clicks", "outbound_clicks"},
        "Pinterest aggregate",
    )
    _exact(
        amazon,
        {"conversions", "gross_revenue_minor", "commission_minor"},
        "Amazon aggregate",
    )
    normalized = {
        "schema_version": 1,
        "mode": "read_only",
        **{
            key: _identifier(report[key], key)
            for key in (
                "tenant_id", "business_id", "producer_id", "verifier_id",
                "offer_key", "source_ref",
            )
        },
    }
    normalized.update(
        {
            "window_start": _timestamp(report["window_start"], "window_start"),
            "window_end": _timestamp(report["window_end"], "window_end"),
            "observed_at": _timestamp(report["observed_at"], "observed_at"),
            "pinterest": {
                key: _count(value, f"pinterest.{key}")
                for key, value in pinterest.items()
            },
            "amazon": {
                key: _count(value, f"amazon.{key}")
                for key, value in amazon.items()
            },
        }
    )
    if normalized["producer_id"] == normalized["verifier_id"]:
        raise PilotCanaryError("producer and verifier must be independent")
    return normalized


def read_secret_file(path_value: str | None) -> str:
    if not path_value:
        raise PostgreSQLPilotError("AOS_POSTGRES_DSN_FILE is required")
    path = Path(path_value)
    if not path.is_file() or path.stat().st_size > 64 * 1024:
        raise PostgreSQLPilotError("PostgreSQL secret mount is unavailable")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise PostgreSQLPilotError("PostgreSQL secret mount is unreadable") from error
    if not value:
        raise PostgreSQLPilotError("PostgreSQL secret mount is empty")
    return value


def load_report(path_value: str | None) -> dict[str, Any]:
    if not path_value:
        raise PilotCanaryError("AOS_CANARY_INPUT_FILE is required")
    path = Path(path_value)
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise PilotCanaryError("canary input is missing or exceeds the size limit")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PilotCanaryError("canary input is unreadable") from error
    return validate_report(raw)


def import_report(
    store: PostgreSQLPilotStore,
    report: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    with store.atomic():
        return _import_report(store, report, now=now)


def _import_report(
    store: PostgreSQLPilotStore,
    report: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if report["observed_at"] > current:
        raise PilotCanaryError("canary observation cannot be in the future")
    suffix = uuid4().hex
    pinterest_id = f"pinterest-aggregate-{suffix}"
    amazon_id = f"amazon-aggregate-{suffix}"
    store.insert_evidence(
        evidence_id=pinterest_id,
        tenant_id=report["tenant_id"],
        business_id=report["business_id"],
        source_type="pinterest_aggregate",
        source_ref=f"pinterest:{report['source_ref']}",
        statement="Read-only Pinterest aggregate export.",
        facts=report["pinterest"],
        confidence=Decimal("0.90"),
        observed_at=report["observed_at"],
    )
    store.insert_evidence(
        evidence_id=amazon_id,
        tenant_id=report["tenant_id"],
        business_id=report["business_id"],
        source_type="affiliate_report",
        source_ref=f"amazon:{report['source_ref']}",
        statement="Read-only Amazon aggregate export.",
        facts=report["amazon"],
        confidence=Decimal("0.90"),
        observed_at=report["observed_at"],
    )
    pinterest = report["pinterest"]
    amazon = report["amazon"]
    service = AggregatePerformanceService(store)
    result = service.import_snapshot(
        tenant_id=report["tenant_id"],
        business_id=report["business_id"],
        producer_id=report["producer_id"],
        snapshot=AggregateSnapshot(
            channel="pinterest",
            offer_key=report["offer_key"],
            source_system="pinterest-amazon-readonly",
            source_ref=report["source_ref"],
            window_start=report["window_start"],
            window_end=report["window_end"],
            impressions=pinterest["impressions"],
            engagements=pinterest["engagements"],
            content_clicks=pinterest["content_clicks"],
            outbound_clicks=pinterest["outbound_clicks"],
            conversions=amazon["conversions"],
            gross_revenue_minor=amazon["gross_revenue_minor"],
            commission_minor=amazon["commission_minor"],
            minimum_outbound_clicks=1,
            evidence_refs=(pinterest_id, amazon_id),
        ),
        now=current,
    )
    decision = service.verify(
        snapshot_id=result.snapshot_id,
        verifier_id=report["verifier_id"],
        now=current,
    )
    return {
        "snapshot_id": result.snapshot_id,
        "snapshot_hash": result.snapshot_hash,
        "decision": decision.value,
        "evidence_class": result.evidence_class,
        "external_side_effects_enabled": False,
    }


def main() -> None:
    report = load_report(os.environ.get("AOS_CANARY_INPUT_FILE"))
    dsn = read_secret_file(os.environ.get("AOS_POSTGRES_DSN_FILE"))
    store = PostgreSQLPilotStore(
        dsn,
        tenant_id=report["tenant_id"],
        business_id=report["business_id"],
    )
    status = store.schema_status()
    if not status["migration_valid"]:
        raise PostgreSQLPilotError("PostgreSQL pilot schema check failed")
    CapabilityPackCatalog(ROOT / "departments", ROOT / "agents").evaluate_all(
        store=store
    )
    print(json.dumps(import_report(store, report), sort_keys=True))


if __name__ == "__main__":
    main()
