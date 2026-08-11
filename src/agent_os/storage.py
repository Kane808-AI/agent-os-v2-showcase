"""Persistence adapters for Agent OS v2.

SQLite is the local development adapter. Production persistence will implement
the same behavioral contract with PostgreSQL.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import tempfile
from typing import Any, Iterator

from .contracts import (
    ActionRequest,
    ActorIdentity,
    ActorType,
    ApprovalDecision,
    AuthorityEnvelope,
    AuthorityMode,
    AuthorityRule,
    Business,
    Event,
    EmergencyStopAction,
    MemoryRecord,
    Objective,
    ObjectiveStatus,
    PROHIBITED_FINANCIAL_ACTIONS,
    is_prohibited_financial_action,
    requires_spend_envelope,
    Tenant,
    TenantStatus,
    VerificationStatus,
)


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS businesses (
    business_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    legal_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    base_currency TEXT NOT NULL,
    timezone_name TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

CREATE TABLE IF NOT EXISTS actors (
    actor_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    roles_json TEXT NOT NULL,
    business_ids_json TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

CREATE TABLE IF NOT EXISTS authority_envelopes (
    envelope_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    rules_json TEXT NOT NULL,
    expires_at TEXT,
    UNIQUE (tenant_id, business_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
    FOREIGN KEY (business_id) REFERENCES businesses(business_id)
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    source TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    correlation_id TEXT,
    idempotency_key TEXT,
    received_at TEXT NOT NULL,
    UNIQUE (tenant_id, business_id, source, idempotency_key)
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    action_type TEXT,
    authority_mode TEXT,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);

CREATE TABLE IF NOT EXISTS audit_records (
    audit_id TEXT PRIMARY KEY,
    event_id TEXT,
    run_id TEXT,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS objectives (
    objective_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    statement TEXT NOT NULL,
    metric TEXT NOT NULL,
    target_value TEXT NOT NULL,
    current_value TEXT NOT NULL,
    status TEXT NOT NULL,
    deadline TEXT,
    priority INTEGER NOT NULL,
    review_interval_seconds INTEGER NOT NULL,
    next_review_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
    FOREIGN KEY (business_id) REFERENCES businesses(business_id)
);

CREATE TABLE IF NOT EXISTS work_items (
    work_item_id TEXT PRIMARY KEY,
    work_key TEXT NOT NULL,
    objective_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    title TEXT NOT NULL,
    rationale TEXT NOT NULL,
    action_type TEXT NOT NULL,
    assigned_actor_id TEXT NOT NULL,
    platform TEXT,
    account_id TEXT,
    amount TEXT,
    currency TEXT,
    attributes_json TEXT NOT NULL,
    authority_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    priority_score INTEGER NOT NULL,
    attempt_count INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    available_at TEXT NOT NULL,
    claimed_by TEXT,
    lease_expires_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, business_id, work_key),
    FOREIGN KEY (objective_id) REFERENCES objectives(objective_id),
    FOREIGN KEY (assigned_actor_id) REFERENCES actors(actor_id)
);

CREATE TABLE IF NOT EXISTS capability_definitions (
    capability_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL,
    required_role TEXT NOT NULL,
    action_types_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_capabilities (
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, business_id, actor_id, capability_id),
    FOREIGN KEY (actor_id) REFERENCES actors(actor_id),
    FOREIGN KEY (capability_id) REFERENCES capability_definitions(capability_id)
);

CREATE TABLE IF NOT EXISTS evidence_records (
    evidence_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    statement TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    confidence TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS structured_plans (
    plan_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    objective_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    planner_id TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (objective_id) REFERENCES objectives(objective_id),
    FOREIGN KEY (capability_id) REFERENCES capability_definitions(capability_id)
);

CREATE TABLE IF NOT EXISTS plan_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    evaluator_version TEXT NOT NULL,
    decision TEXT NOT NULL,
    score INTEGER NOT NULL,
    reasons_json TEXT NOT NULL,
    evaluation_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES structured_plans(plan_id)
);

CREATE TABLE IF NOT EXISTS memory_records (
    memory_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    statement TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    confidence TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    expires_at TEXT,
    supersedes_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_tenant_business
    ON events(tenant_id, business_id, received_at);
CREATE INDEX IF NOT EXISTS idx_runs_tenant_business
    ON workflow_runs(tenant_id, business_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_business
    ON audit_records(tenant_id, business_id, created_at);
CREATE INDEX IF NOT EXISTS idx_objectives_due
    ON objectives(status, next_review_at, priority);
CREATE INDEX IF NOT EXISTS idx_work_claim
    ON work_items(status, available_at, lease_expires_at, priority_score);
CREATE INDEX IF NOT EXISTS idx_evidence_scope
    ON evidence_records(tenant_id, business_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_plans_scope
    ON structured_plans(tenant_id, business_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_scope
    ON memory_records(tenant_id, business_id, verification_status, created_at);
"""

SCHEMA_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""

BASELINE_SCHEMA_VERSION = 1
BASELINE_MIGRATION_NAME = "initial_runtime_schema"

EXECUTION_VERIFICATION_SCHEMA = """
CREATE TABLE evidence_receipts (
    receipt_id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL,
    attempt_id TEXT,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    captured_by TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (work_item_id) REFERENCES work_items(work_item_id),
    FOREIGN KEY (captured_by) REFERENCES actors(actor_id)
);

CREATE TABLE execution_attempts (
    attempt_id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    producer_id TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    precondition_receipt_id TEXT,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    observed_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, business_id, idempotency_key),
    FOREIGN KEY (work_item_id) REFERENCES work_items(work_item_id),
    FOREIGN KEY (producer_id) REFERENCES actors(actor_id),
    FOREIGN KEY (precondition_receipt_id)
        REFERENCES evidence_receipts(receipt_id)
);

CREATE TABLE outcome_verifications (
    verification_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    work_item_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    verifier_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    evidence_receipt_ids_json TEXT NOT NULL,
    expected_facts_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    FOREIGN KEY (attempt_id) REFERENCES execution_attempts(attempt_id),
    FOREIGN KEY (work_item_id) REFERENCES work_items(work_item_id),
    FOREIGN KEY (verifier_id) REFERENCES actors(actor_id)
);

CREATE INDEX idx_receipts_work
    ON evidence_receipts(tenant_id, business_id, work_item_id, observed_at);
CREATE INDEX idx_attempts_work
    ON execution_attempts(tenant_id, business_id, work_item_id, attempted_at);
CREATE INDEX idx_verifications_attempt
    ON outcome_verifications(attempt_id, decided_at);
"""

_TENANT_BUSINESS_SCOPED_TABLES = (
    "authority_envelopes",
    "events",
    "event_processing",
    "workflow_runs",
    "audit_records",
    "objectives",
    "work_items",
    "agent_capabilities",
    "evidence_records",
    "structured_plans",
    "plan_evaluations",
    "memory_records",
    "evidence_receipts",
    "evidence_issuers",
    "execution_attempts",
    "outcome_verifications",
)

_ACTOR_SCOPED_COLUMNS = {
    "work_items": "assigned_actor_id",
    "agent_capabilities": "actor_id",
    "evidence_receipts": "captured_by",
    "evidence_issuers": "actor_id",
    "execution_attempts": "producer_id",
    "outcome_verifications": "verifier_id",
}

_GOAL9_TENANT_BUSINESS_SCOPED_TABLES = (
    "approval_requests",
    "approval_events",
    "emergency_stop_events",
    "spend_envelopes",
    "spend_commitments",
)

_GOAL9_ACTOR_SCOPED_COLUMNS = {
    "approval_requests": "requester_id",
    "approval_events": "actor_id",
    "emergency_stop_events": "actor_id",
    "spend_envelopes": "created_by",
}

_GOAL10_TENANT_BUSINESS_SCOPED_TABLES = (
    "provider_credentials",
    "provider_policy_revisions",
    "routing_decisions",
    "model_usage_records",
    "model_health_events",
    "model_circuit_states",
)

_GOAL11_TENANT_BUSINESS_SCOPED_TABLES = (
    "shadow_model_attempts",
    "shadow_model_outcomes",
)

_GOAL12_TENANT_BUSINESS_SCOPED_TABLES = (
    "affiliate_shadow_runs",
    "affiliate_offer_snapshots",
    "affiliate_recommendations",
    "affiliate_content_proposals",
    "affiliate_experiments",
    "affiliate_observations",
    "affiliate_measurements",
    "affiliate_verifications",
    "affiliate_learnings",
)

_GOAL13_TENANT_BUSINESS_SCOPED_TABLES = (
    "aggregate_performance_snapshots",
    "aggregate_performance_verifications",
)

_GOAL14_TENANT_BUSINESS_SCOPED_TABLES = (
    "production_qualifications",
    "legacy_cutover_plans",
    "legacy_cutover_events",
)

_PARENT_SCOPE_LINKS = (
    ("event_processing", "event_id", "events", "event_id"),
    ("workflow_runs", "event_id", "events", "event_id"),
    ("audit_records", "event_id", "events", "event_id"),
    ("work_items", "objective_id", "objectives", "objective_id"),
    ("structured_plans", "objective_id", "objectives", "objective_id"),
    ("plan_evaluations", "plan_id", "structured_plans", "plan_id"),
    ("memory_records", "supersedes_id", "memory_records", "memory_id"),
    ("evidence_receipts", "work_item_id", "work_items", "work_item_id"),
    (
        "evidence_receipts",
        "attempt_id",
        "execution_attempts",
        "attempt_id",
    ),
    ("execution_attempts", "work_item_id", "work_items", "work_item_id"),
    (
        "execution_attempts",
        "precondition_receipt_id",
        "evidence_receipts",
        "receipt_id",
    ),
    (
        "outcome_verifications",
        "attempt_id",
        "execution_attempts",
        "attempt_id",
    ),
    (
        "outcome_verifications",
        "work_item_id",
        "work_items",
        "work_item_id",
    ),
)


def _scope_trigger_sql() -> str:
    statements = []
    for table in _TENANT_BUSINESS_SCOPED_TABLES:
        for operation in ("INSERT", "UPDATE"):
            suffix = operation.lower()
            statements.append(
                f"""
CREATE TRIGGER enforce_{table}_scope_{suffix}
BEFORE {operation} ON {table}
WHEN NOT EXISTS (
    SELECT 1
    FROM businesses
    WHERE tenant_id = NEW.tenant_id
      AND business_id = NEW.business_id
)
BEGIN
    SELECT RAISE(ABORT, 'tenant/business ownership mismatch');
END;
""".strip()
            )
    return "\n\n".join(statements)


def _actor_scope_trigger_sql() -> str:
    statements = []
    for operation in ("INSERT", "UPDATE"):
        suffix = operation.lower()
        statements.append(
            f"""
CREATE TRIGGER enforce_actor_business_membership_{suffix}
BEFORE {operation} ON actors
WHEN EXISTS (
    SELECT 1
    FROM json_each(NEW.business_ids_json) AS claimed
    LEFT JOIN businesses AS business
      ON business.business_id = claimed.value
     AND business.tenant_id = NEW.tenant_id
    WHERE business.business_id IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'actor business membership crosses tenant boundary');
END;
""".strip()
        )
    for table, actor_column in _ACTOR_SCOPED_COLUMNS.items():
        for operation in ("INSERT", "UPDATE"):
            suffix = operation.lower()
            statements.append(
                f"""
CREATE TRIGGER enforce_{table}_actor_scope_{suffix}
BEFORE {operation} ON {table}
WHEN NOT EXISTS (
    SELECT 1
    FROM actors AS actor, json_each(actor.business_ids_json) AS membership
    WHERE actor.actor_id = NEW.{actor_column}
      AND actor.tenant_id = NEW.tenant_id
      AND membership.value = NEW.business_id
)
BEGIN
    SELECT RAISE(ABORT, 'actor crosses tenant/business boundary');
END;
""".strip()
            )
    return "\n\n".join(statements)


def _identity_lifecycle_trigger_sql() -> str:
    business_references = "\n    OR ".join(
        (
            f"EXISTS (SELECT 1 FROM {table} AS scoped "
            "WHERE scoped.business_id = OLD.business_id "
            "AND scoped.tenant_id = OLD.tenant_id)"
        )
        for table in _TENANT_BUSINESS_SCOPED_TABLES
    )
    actor_references = "\n    OR ".join(
        (
            f"EXISTS (SELECT 1 FROM {table} AS scoped "
            f"WHERE scoped.{actor_column} = OLD.actor_id "
            "AND (scoped.tenant_id != NEW.tenant_id "
            "OR NOT EXISTS ("
            "SELECT 1 FROM json_each(NEW.business_ids_json) AS membership "
            "WHERE membership.value = scoped.business_id)))"
        )
        for table, actor_column in _ACTOR_SCOPED_COLUMNS.items()
    )
    return f"""
CREATE TRIGGER prevent_business_tenant_move
BEFORE UPDATE OF tenant_id ON businesses
WHEN OLD.tenant_id != NEW.tenant_id
BEGIN
    SELECT RAISE(ABORT, 'business identity cannot move across tenants');
END;

CREATE TRIGGER prevent_scoped_business_delete
BEFORE DELETE ON businesses
WHEN {business_references}
    OR EXISTS (
        SELECT 1
        FROM actors AS actor, json_each(actor.business_ids_json) AS membership
        WHERE actor.tenant_id = OLD.tenant_id
          AND membership.value = OLD.business_id
    )
BEGIN
    SELECT RAISE(ABORT, 'business is still referenced by scoped state');
END;

CREATE TRIGGER prevent_actor_identity_scope_move
BEFORE UPDATE OF tenant_id, business_ids_json ON actors
WHEN OLD.tenant_id != NEW.tenant_id
    OR {actor_references}
BEGIN
    SELECT RAISE(ABORT, 'actor identity cannot orphan scoped state');
END;
""".strip()


FOUNDATION_HARDENING_SCHEMA = """
CREATE UNIQUE INDEX idx_businesses_tenant_business
    ON businesses(tenant_id, business_id);

ALTER TABLE events ADD COLUMN event_fingerprint TEXT;

CREATE TABLE event_processing (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    status TEXT NOT NULL,
    claimed_by TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);

ALTER TABLE evidence_receipts ADD COLUMN issuer_version TEXT;
ALTER TABLE plan_evaluations ADD COLUMN authority_modes_json TEXT;

CREATE TABLE evidence_issuers (
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    source_system TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    issuer_version TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    PRIMARY KEY (
        tenant_id, business_id, source_system, evidence_kind, actor_id
    ),
    FOREIGN KEY (actor_id) REFERENCES actors(actor_id)
);

ALTER TABLE execution_attempts
    ADD COLUMN reconciliation_attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE execution_attempts
    ADD COLUMN reconciliation_max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE execution_attempts
    ADD COLUMN reconciliation_available_at TEXT;
ALTER TABLE execution_attempts
    ADD COLUMN reconciliation_claimed_by TEXT;
ALTER TABLE execution_attempts
    ADD COLUMN reconciliation_lease_expires_at TEXT;
ALTER TABLE execution_attempts
    ADD COLUMN reconciliation_last_error TEXT;

CREATE INDEX idx_event_processing_claim
    ON event_processing(status, lease_expires_at);
CREATE INDEX idx_attempt_reconciliation
    ON execution_attempts(
        status, reconciliation_available_at,
        reconciliation_lease_expires_at
    );
""" + "\n\n" + _scope_trigger_sql() + "\n\n" + _actor_scope_trigger_sql() + (
    "\n\n" + _identity_lifecycle_trigger_sql()
)


def _parent_scope_trigger_sql() -> str:
    statements = []
    for child, child_column, parent, parent_column in _PARENT_SCOPE_LINKS:
        for operation in ("INSERT", "UPDATE"):
            suffix = operation.lower()
            statements.append(
                f"""
CREATE TRIGGER enforce_{child}_{child_column}_scope_{suffix}
BEFORE {operation} ON {child}
WHEN NEW.{child_column} IS NOT NULL
 AND NOT EXISTS (
    SELECT 1
    FROM {parent} AS parent
    WHERE parent.{parent_column} = NEW.{child_column}
      AND parent.tenant_id = NEW.tenant_id
      AND parent.business_id = NEW.business_id
)
BEGIN
    SELECT RAISE(ABORT, 'parent identity crosses tenant/business boundary');
END;
""".strip()
            )
    return "\n\n".join(statements)


def _immutable_truth_trigger_sql() -> str:
    statements = []
    for table in (
        "schema_migrations",
        "events",
        "workflow_runs",
        "audit_records",
        "evidence_records",
        "plan_evaluations",
        "evidence_receipts",
        "outcome_verifications",
    ):
        for operation in ("UPDATE", "DELETE"):
            suffix = operation.lower()
            statements.append(
                f"""
CREATE TRIGGER prevent_{table}_{suffix}
BEFORE {operation} ON {table}
BEGIN
    SELECT RAISE(ABORT, '{table} is append-only');
END;
""".strip()
            )
    for operation in ("INSERT", "UPDATE"):
        suffix = operation.lower()
        statements.append(
            f"""
CREATE TRIGGER enforce_receipt_attempt_work_identity_{suffix}
BEFORE {operation} ON evidence_receipts
WHEN NEW.attempt_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1
    FROM execution_attempts AS attempt
    WHERE attempt.attempt_id = NEW.attempt_id
      AND attempt.work_item_id = NEW.work_item_id
      AND attempt.tenant_id = NEW.tenant_id
      AND attempt.business_id = NEW.business_id
)
BEGIN
    SELECT RAISE(ABORT, 'receipt does not match its attempted work');
END;
""".strip()
        )
        statements.append(
            f"""
CREATE TRIGGER enforce_attempt_precondition_identity_{suffix}
BEFORE {operation} ON execution_attempts
WHEN NEW.precondition_receipt_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1
    FROM evidence_receipts AS receipt
    WHERE receipt.receipt_id = NEW.precondition_receipt_id
      AND receipt.work_item_id = NEW.work_item_id
      AND receipt.tenant_id = NEW.tenant_id
      AND receipt.business_id = NEW.business_id
      AND receipt.evidence_kind = 'precondition'
      AND receipt.attempt_id IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'attempt precondition does not match its work');
END;
""".strip()
        )
    statements.extend(
        (
            """
CREATE TRIGGER restrict_structured_plan_update
BEFORE UPDATE ON structured_plans
WHEN NOT (
    OLD.status = 'accepted'
    AND NEW.status = 'materialized'
    AND OLD.plan_id IS NEW.plan_id
    AND OLD.tenant_id IS NEW.tenant_id
    AND OLD.business_id IS NEW.business_id
    AND OLD.objective_id IS NEW.objective_id
    AND OLD.capability_id IS NEW.capability_id
    AND OLD.planner_id IS NEW.planner_id
    AND OLD.plan_json IS NEW.plan_json
    AND OLD.plan_hash IS NEW.plan_hash
    AND OLD.created_at IS NEW.created_at
)
BEGIN
    SELECT RAISE(ABORT, 'structured plan content and decision are immutable');
END;
""".strip(),
            """
CREATE TRIGGER prevent_structured_plan_delete
BEFORE DELETE ON structured_plans
BEGIN
    SELECT RAISE(ABORT, 'structured plans are append-only');
END;
""".strip(),
            """
CREATE TRIGGER enforce_verification_attempt_work_identity_insert
BEFORE INSERT ON outcome_verifications
WHEN NOT EXISTS (
    SELECT 1
    FROM execution_attempts AS attempt
    WHERE attempt.attempt_id = NEW.attempt_id
      AND attempt.work_item_id = NEW.work_item_id
      AND attempt.tenant_id = NEW.tenant_id
      AND attempt.business_id = NEW.business_id
)
BEGIN
    SELECT RAISE(ABORT, 'verification does not match its attempted work');
END;
""".strip(),
            """
CREATE TRIGGER enforce_verification_attempt_work_identity_update
BEFORE UPDATE ON outcome_verifications
WHEN NOT EXISTS (
    SELECT 1
    FROM execution_attempts AS attempt
    WHERE attempt.attempt_id = NEW.attempt_id
      AND attempt.work_item_id = NEW.work_item_id
      AND attempt.tenant_id = NEW.tenant_id
      AND attempt.business_id = NEW.business_id
)
BEGIN
    SELECT RAISE(ABORT, 'verification does not match its attempted work');
END;
""".strip(),
            """
CREATE TRIGGER prevent_terminal_attempt_update
BEFORE UPDATE ON execution_attempts
WHEN OLD.status IN ('verified', 'disproved')
BEGIN
    SELECT RAISE(ABORT, 'terminal execution truth is immutable');
END;
""".strip(),
            """
CREATE TRIGGER prevent_terminal_attempt_delete
BEFORE DELETE ON execution_attempts
WHEN OLD.status IN ('verified', 'disproved')
BEGIN
    SELECT RAISE(ABORT, 'terminal execution truth is immutable');
END;
""".strip(),
            """
CREATE TRIGGER prevent_terminal_work_update
BEFORE UPDATE ON work_items
WHEN OLD.status IN ('verified', 'disproved')
BEGIN
    SELECT RAISE(ABORT, 'terminal work truth is immutable');
END;
""".strip(),
            """
CREATE TRIGGER prevent_terminal_work_delete
BEFORE DELETE ON work_items
WHEN OLD.status IN ('verified', 'disproved')
BEGIN
    SELECT RAISE(ABORT, 'terminal work truth is immutable');
END;
""".strip(),
        )
    )
    return "\n\n".join(statements)


DURABLE_TRUST_ATTESTATION_SCHEMA = (
    _parent_scope_trigger_sql()
    + "\n\n"
    + _immutable_truth_trigger_sql()
)


def _parent_scope_lifecycle_trigger_sql() -> str:
    statements = []
    for child, child_column, parent, parent_column in _PARENT_SCOPE_LINKS:
        identity = f"{parent}_{parent_column}_for_{child}_{child_column}"
        statements.extend(
            (
                f"""
CREATE TRIGGER protect_{identity}_scope_update
BEFORE UPDATE OF tenant_id, business_id ON {parent}
WHEN (
    OLD.tenant_id IS NOT NEW.tenant_id
    OR OLD.business_id IS NOT NEW.business_id
)
 AND EXISTS (
    SELECT 1
    FROM {child} AS child
    WHERE child.{child_column} = OLD.{parent_column}
      AND (
        child.tenant_id IS NOT NEW.tenant_id
        OR child.business_id IS NOT NEW.business_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'parent scope change would orphan child identity');
END;
""".strip(),
                f"""
CREATE TRIGGER protect_{identity}_delete
BEFORE DELETE ON {parent}
WHEN EXISTS (
    SELECT 1
    FROM {child} AS child
    WHERE child.{child_column} = OLD.{parent_column}
)
BEGIN
    SELECT RAISE(ABORT, 'parent deletion would orphan child identity');
END;
""".strip(),
            )
        )
    return "\n\n".join(statements)


AUTHENTICATED_COMPLETION_SCHEMA = """
CREATE TABLE completion_attestations (
    verification_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    work_item_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    signature TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (verification_id)
        REFERENCES outcome_verifications(verification_id),
    FOREIGN KEY (attempt_id) REFERENCES execution_attempts(attempt_id),
    FOREIGN KEY (work_item_id) REFERENCES work_items(work_item_id)
);

CREATE TRIGGER enforce_completion_attestations_scope_insert
BEFORE INSERT ON completion_attestations
WHEN NOT EXISTS (
    SELECT 1
    FROM businesses
    WHERE tenant_id = NEW.tenant_id
      AND business_id = NEW.business_id
)
BEGIN
    SELECT RAISE(ABORT, 'tenant/business ownership mismatch');
END;

CREATE TRIGGER enforce_completion_attestation_identity_insert
BEFORE INSERT ON completion_attestations
WHEN NOT EXISTS (
    SELECT 1
    FROM outcome_verifications AS verification
    JOIN execution_attempts AS attempt
      ON attempt.attempt_id = verification.attempt_id
     AND attempt.work_item_id = verification.work_item_id
     AND attempt.tenant_id = verification.tenant_id
     AND attempt.business_id = verification.business_id
    WHERE verification.verification_id = NEW.verification_id
      AND verification.attempt_id = NEW.attempt_id
      AND verification.work_item_id = NEW.work_item_id
      AND verification.tenant_id = NEW.tenant_id
      AND verification.business_id = NEW.business_id
)
BEGIN
    SELECT RAISE(ABORT, 'completion attestation identity mismatch');
END;

CREATE TRIGGER prevent_completion_attestations_update
BEFORE UPDATE ON completion_attestations
BEGIN
    SELECT RAISE(ABORT, 'completion attestations are append-only');
END;

CREATE TRIGGER prevent_completion_attestations_delete
BEFORE DELETE ON completion_attestations
BEGIN
    SELECT RAISE(ABORT, 'completion attestations are append-only');
END;

CREATE TRIGGER require_attempt_terminal_attestation
BEFORE UPDATE OF status ON execution_attempts
WHEN NEW.status IN ('verified', 'disproved')
 AND OLD.status NOT IN ('verified', 'disproved')
 AND NOT EXISTS (
    SELECT 1
    FROM completion_attestations AS attestation
    JOIN outcome_verifications AS verification
      ON verification.verification_id = attestation.verification_id
    WHERE attestation.attempt_id = NEW.attempt_id
      AND attestation.work_item_id = NEW.work_item_id
      AND attestation.tenant_id = NEW.tenant_id
      AND attestation.business_id = NEW.business_id
      AND verification.decision = NEW.status
)
BEGIN
    SELECT RAISE(ABORT, 'terminal attempt requires authenticated attestation');
END;

CREATE TRIGGER require_work_terminal_attestation
BEFORE UPDATE OF status ON work_items
WHEN NEW.status IN ('verified', 'disproved')
 AND OLD.status NOT IN ('verified', 'disproved')
 AND NOT EXISTS (
    SELECT 1
    FROM completion_attestations AS attestation
    JOIN outcome_verifications AS verification
      ON verification.verification_id = attestation.verification_id
    JOIN execution_attempts AS attempt
      ON attempt.attempt_id = attestation.attempt_id
    WHERE attestation.work_item_id = NEW.work_item_id
      AND attestation.tenant_id = NEW.tenant_id
      AND attestation.business_id = NEW.business_id
      AND verification.decision = NEW.status
      AND attempt.status = NEW.status
)
BEGIN
    SELECT RAISE(ABORT, 'terminal work requires authenticated attestation');
END;
""" + "\n\n" + _parent_scope_lifecycle_trigger_sql()

SEMANTIC_COMPLETION_SCHEMA = """
ALTER TABLE completion_attestations
    ADD COLUMN payload_version INTEGER NOT NULL DEFAULT 1;

DROP TRIGGER require_attempt_terminal_attestation;
DROP TRIGGER require_work_terminal_attestation;

CREATE TRIGGER enforce_completion_attestation_version_insert
BEFORE INSERT ON completion_attestations
WHEN NEW.payload_version != 2
BEGIN
    SELECT RAISE(ABORT, 'completion attestation version is unsupported');
END;

CREATE TRIGGER require_attempt_terminal_attestation_insert
BEFORE INSERT ON execution_attempts
WHEN NEW.status IN ('verified', 'disproved')
 AND NOT EXISTS (
    SELECT 1
    FROM completion_attestations AS attestation
    JOIN outcome_verifications AS verification
      ON verification.verification_id = attestation.verification_id
    WHERE attestation.attempt_id = NEW.attempt_id
      AND attestation.work_item_id = NEW.work_item_id
      AND attestation.tenant_id = NEW.tenant_id
      AND attestation.business_id = NEW.business_id
      AND attestation.payload_version = 2
      AND verification.decision = NEW.status
)
BEGIN
    SELECT RAISE(ABORT, 'terminal attempt requires authenticated attestation');
END;

CREATE TRIGGER require_attempt_terminal_attestation_update
BEFORE UPDATE OF status ON execution_attempts
WHEN NEW.status IN ('verified', 'disproved')
 AND OLD.status NOT IN ('verified', 'disproved')
 AND NOT EXISTS (
    SELECT 1
    FROM completion_attestations AS attestation
    JOIN outcome_verifications AS verification
      ON verification.verification_id = attestation.verification_id
    WHERE attestation.attempt_id = NEW.attempt_id
      AND attestation.work_item_id = NEW.work_item_id
      AND attestation.tenant_id = NEW.tenant_id
      AND attestation.business_id = NEW.business_id
      AND attestation.payload_version = 2
      AND verification.decision = NEW.status
)
BEGIN
    SELECT RAISE(ABORT, 'terminal attempt requires authenticated attestation');
END;

CREATE TRIGGER require_work_terminal_attestation_insert
BEFORE INSERT ON work_items
WHEN NEW.status IN ('verified', 'disproved')
 AND NOT EXISTS (
    SELECT 1
    FROM completion_attestations AS attestation
    JOIN outcome_verifications AS verification
      ON verification.verification_id = attestation.verification_id
    JOIN execution_attempts AS attempt
      ON attempt.attempt_id = attestation.attempt_id
    WHERE attestation.work_item_id = NEW.work_item_id
      AND attestation.tenant_id = NEW.tenant_id
      AND attestation.business_id = NEW.business_id
      AND attestation.payload_version = 2
      AND verification.decision = NEW.status
      AND attempt.status = NEW.status
)
BEGIN
    SELECT RAISE(ABORT, 'terminal work requires authenticated attestation');
END;

CREATE TRIGGER require_work_terminal_attestation_update
BEFORE UPDATE OF status ON work_items
WHEN NEW.status IN ('verified', 'disproved')
 AND OLD.status NOT IN ('verified', 'disproved')
 AND NOT EXISTS (
    SELECT 1
    FROM completion_attestations AS attestation
    JOIN outcome_verifications AS verification
      ON verification.verification_id = attestation.verification_id
    JOIN execution_attempts AS attempt
      ON attempt.attempt_id = attestation.attempt_id
    WHERE attestation.work_item_id = NEW.work_item_id
      AND attestation.tenant_id = NEW.tenant_id
      AND attestation.business_id = NEW.business_id
      AND attestation.payload_version = 2
      AND verification.decision = NEW.status
      AND attempt.status = NEW.status
)
BEGIN
    SELECT RAISE(ABORT, 'terminal work requires authenticated attestation');
END;
"""

APPROVAL_AND_EMERGENCY_CONTROL_SCHEMA = """
CREATE TABLE approval_requests (
    approval_id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    requester_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    work_fingerprint TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (work_item_id) REFERENCES work_items(work_item_id),
    FOREIGN KEY (requester_id) REFERENCES actors(actor_id)
);

CREATE TABLE approval_events (
    event_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (
        decision IN ('approved', 'rejected', 'revoked')
    ),
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (approval_id) REFERENCES approval_requests(approval_id),
    FOREIGN KEY (actor_id) REFERENCES actors(actor_id)
);

CREATE TABLE emergency_stop_events (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('activated', 'cleared')),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (actor_id) REFERENCES actors(actor_id)
);

CREATE INDEX idx_approval_events_latest
    ON approval_events(approval_id, created_at, event_id);
CREATE INDEX idx_emergency_stop_events_latest
    ON emergency_stop_events(
        tenant_id, business_id, created_at, event_id
    );

CREATE TRIGGER enforce_approval_request_identity_insert
BEFORE INSERT ON approval_requests
WHEN NOT EXISTS (
    SELECT 1
    FROM work_items AS work
    JOIN actors AS requester
      ON requester.actor_id = NEW.requester_id
     AND requester.tenant_id = NEW.tenant_id
     AND requester.enabled = 1
    JOIN json_each(requester.business_ids_json) AS membership
      ON membership.value = NEW.business_id
    WHERE work.work_item_id = NEW.work_item_id
      AND work.tenant_id = NEW.tenant_id
      AND work.business_id = NEW.business_id
      AND work.assigned_actor_id = NEW.requester_id
      AND work.action_type = NEW.action_type
      AND work.status = 'awaiting_approval'
)
BEGIN
    SELECT RAISE(ABORT, 'approval request does not match held work');
END;

CREATE TRIGGER enforce_approval_event_identity_insert
BEFORE INSERT ON approval_events
WHEN NOT EXISTS (
    SELECT 1
    FROM approval_requests AS request
    JOIN actors AS actor
      ON actor.actor_id = NEW.actor_id
     AND actor.tenant_id = NEW.tenant_id
     AND actor.enabled = 1
    JOIN json_each(actor.business_ids_json) AS membership
      ON membership.value = NEW.business_id
    WHERE request.approval_id = NEW.approval_id
      AND request.tenant_id = NEW.tenant_id
      AND request.business_id = NEW.business_id
      AND actor.actor_type = 'human'
      AND actor.actor_id != request.requester_id
      AND EXISTS (
          SELECT 1
          FROM json_each(actor.roles_json) AS role
          WHERE role.value IN (
              'approver', 'business-owner', 'finance-approver'
          )
      )
)
BEGIN
    SELECT RAISE(
        ABORT,
        'approval event requires a separate authorized human approver'
    );
END;

CREATE TRIGGER enforce_approval_event_sequence_insert
BEFORE INSERT ON approval_events
WHEN (
    NEW.created_at >= (
        SELECT request.expires_at
        FROM approval_requests AS request
        WHERE request.approval_id = NEW.approval_id
    )
    OR (
        NEW.decision IN ('approved', 'rejected')
        AND EXISTS (
            SELECT 1
            FROM approval_events AS prior
            WHERE prior.approval_id = NEW.approval_id
              AND prior.decision IN ('approved', 'rejected')
              AND NOT EXISTS (
                  SELECT 1
                  FROM approval_events AS later
                  WHERE later.approval_id = NEW.approval_id
                    AND later.created_at > prior.created_at
              )
        )
    )
    OR (
        NEW.decision = 'revoked'
        AND COALESCE(
            (
                SELECT prior.decision
                FROM approval_events AS prior
                WHERE prior.approval_id = NEW.approval_id
                ORDER BY prior.created_at DESC, prior.rowid DESC
                LIMIT 1
            ),
            ''
        ) != 'approved'
    )
    OR (
        NEW.decision = 'approved'
        AND COALESCE(
            (
                SELECT stop.action
                FROM emergency_stop_events AS stop
                WHERE stop.tenant_id = NEW.tenant_id
                  AND stop.business_id = NEW.business_id
                ORDER BY stop.created_at DESC, stop.rowid DESC
                LIMIT 1
            ),
            'cleared'
        ) = 'activated'
    )
)
BEGIN
    SELECT RAISE(ABORT, 'approval event is expired or out of sequence');
END;

CREATE TRIGGER enforce_emergency_stop_event_identity_insert
BEFORE INSERT ON emergency_stop_events
WHEN NOT EXISTS (
    SELECT 1
    FROM actors AS actor
    JOIN json_each(actor.business_ids_json) AS membership
      ON membership.value = NEW.business_id
    WHERE actor.actor_id = NEW.actor_id
      AND actor.tenant_id = NEW.tenant_id
      AND actor.enabled = 1
      AND actor.actor_type = 'human'
      AND EXISTS (
          SELECT 1
          FROM json_each(actor.roles_json) AS role
          WHERE role.value IN ('business-owner', 'emergency-admin')
      )
)
BEGIN
    SELECT RAISE(
        ABORT,
        'emergency stop requires an authorized in-scope human'
    );
END;

CREATE TRIGGER enforce_emergency_stop_sequence_insert
BEFORE INSERT ON emergency_stop_events
WHEN NEW.action = 'cleared'
 AND COALESCE(
    (
        SELECT prior.action
        FROM emergency_stop_events AS prior
        WHERE prior.tenant_id = NEW.tenant_id
          AND prior.business_id = NEW.business_id
        ORDER BY prior.created_at DESC, prior.rowid DESC
        LIMIT 1
    ),
    ''
 ) != 'activated'
BEGIN
    SELECT RAISE(ABORT, 'emergency stop is not active');
END;

CREATE TRIGGER protect_work_approval_scope_update
BEFORE UPDATE OF tenant_id, business_id ON work_items
WHEN EXISTS (
    SELECT 1
    FROM approval_requests AS request
    WHERE request.work_item_id = OLD.work_item_id
      AND (
          request.tenant_id IS NOT NEW.tenant_id
          OR request.business_id IS NOT NEW.business_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'work scope change would orphan approval identity');
END;

CREATE TRIGGER protect_work_approval_delete
BEFORE DELETE ON work_items
WHEN EXISTS (
    SELECT 1
    FROM approval_requests AS request
    WHERE request.work_item_id = OLD.work_item_id
)
BEGIN
    SELECT RAISE(ABORT, 'work deletion would orphan approval identity');
END;

CREATE TRIGGER protect_approval_work_semantics_update
BEFORE UPDATE OF
    work_key, objective_id, tenant_id, business_id, title, rationale,
    action_type, assigned_actor_id, platform, account_id, amount, currency,
    attributes_json, authority_mode
ON work_items
WHEN EXISTS (
    SELECT 1
    FROM approval_requests AS request
    WHERE request.work_item_id = OLD.work_item_id
)
AND (
    OLD.work_key IS NOT NEW.work_key
    OR OLD.objective_id IS NOT NEW.objective_id
    OR OLD.tenant_id IS NOT NEW.tenant_id
    OR OLD.business_id IS NOT NEW.business_id
    OR OLD.title IS NOT NEW.title
    OR OLD.rationale IS NOT NEW.rationale
    OR OLD.action_type IS NOT NEW.action_type
    OR OLD.assigned_actor_id IS NOT NEW.assigned_actor_id
    OR OLD.platform IS NOT NEW.platform
    OR OLD.account_id IS NOT NEW.account_id
    OR OLD.amount IS NOT NEW.amount
    OR OLD.currency IS NOT NEW.currency
    OR OLD.attributes_json IS NOT NEW.attributes_json
    OR OLD.authority_mode IS NOT NEW.authority_mode
)
BEGIN
    SELECT RAISE(ABORT, 'approval-bound work semantics are immutable');
END;

CREATE TRIGGER prevent_approval_requests_update
BEFORE UPDATE ON approval_requests
BEGIN
    SELECT RAISE(ABORT, 'approval requests are append-only');
END;

CREATE TRIGGER prevent_approval_requests_delete
BEFORE DELETE ON approval_requests
BEGIN
    SELECT RAISE(ABORT, 'approval requests are append-only');
END;

CREATE TRIGGER prevent_approval_events_update
BEFORE UPDATE ON approval_events
BEGIN
    SELECT RAISE(ABORT, 'approval events are append-only');
END;

CREATE TRIGGER prevent_approval_events_delete
BEFORE DELETE ON approval_events
BEGIN
    SELECT RAISE(ABORT, 'approval events are append-only');
END;

CREATE TRIGGER prevent_emergency_stop_events_update
BEFORE UPDATE ON emergency_stop_events
BEGIN
    SELECT RAISE(ABORT, 'emergency-stop events are append-only');
END;

CREATE TRIGGER prevent_emergency_stop_events_delete
BEFORE DELETE ON emergency_stop_events
BEGIN
    SELECT RAISE(ABORT, 'emergency-stop events are append-only');
END;
"""

FINANCIAL_EXECUTION_GUARD_SCHEMA = """
CREATE TRIGGER prohibit_external_financial_action_insert
BEFORE INSERT ON execution_attempts
WHEN NEW.execution_mode = 'external'
 AND NEW.action_type IN (
    'account.close',
    'account.open',
    'bank.transfer',
    'banking.money-movement',
    'bill.pay',
    'contract.sign',
    'finance.payment.execute',
    'ledger.adjust',
    'payment-method.modify',
    'payout.destination.modify',
    'tax.file',
    'vendor-payment.modify'
 )
BEGIN
    SELECT RAISE(
        ABORT,
        'external money movement and financial commitments are forbidden'
    );
END;
"""

SPEND_ENVELOPE_SCHEMA = """
CREATE TABLE spend_envelopes (
    envelope_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    platform TEXT NOT NULL,
    account_id TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (length(currency) = 3),
    limit_minor INTEGER NOT NULL CHECK (limit_minor > 0),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    created_by TEXT NOT NULL,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (period_start < period_end),
    FOREIGN KEY (business_id) REFERENCES businesses(business_id),
    FOREIGN KEY (created_by) REFERENCES actors(actor_id),
    UNIQUE (
        tenant_id, business_id, action_type, platform, account_id,
        period_start, period_end
    )
);

CREATE TABLE spend_commitments (
    commitment_id TEXT PRIMARY KEY,
    envelope_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL UNIQUE,
    work_item_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL CHECK (length(currency) = 3),
    created_at TEXT NOT NULL,
    FOREIGN KEY (envelope_id) REFERENCES spend_envelopes(envelope_id),
    FOREIGN KEY (work_item_id) REFERENCES work_items(work_item_id)
);

CREATE INDEX idx_spend_envelopes_lookup
    ON spend_envelopes(
        tenant_id, business_id, action_type, platform, account_id,
        period_start, period_end
    );
CREATE INDEX idx_spend_commitments_envelope
    ON spend_commitments(envelope_id, created_at);

CREATE TRIGGER enforce_spend_envelope_identity_insert
BEFORE INSERT ON spend_envelopes
WHEN NEW.action_type NOT LIKE '%.spend'
 OR NOT EXISTS (
    SELECT 1
    FROM businesses AS business
    JOIN actors AS actor
      ON actor.actor_id = NEW.created_by
     AND actor.tenant_id = NEW.tenant_id
     AND actor.actor_type = 'human'
     AND actor.enabled = 1
    JOIN json_each(actor.business_ids_json) AS membership
      ON membership.value = NEW.business_id
    WHERE business.business_id = NEW.business_id
      AND business.tenant_id = NEW.tenant_id
      AND business.base_currency = NEW.currency
      AND EXISTS (
          SELECT 1
          FROM json_each(actor.roles_json) AS role
          WHERE role.value IN ('business-owner', 'finance-approver')
      )
 )
BEGIN
    SELECT RAISE(
        ABORT,
        'spend envelope requires an authorized human and base currency'
    );
END;

CREATE TRIGGER prevent_overlapping_spend_envelope_insert
BEFORE INSERT ON spend_envelopes
WHEN EXISTS (
    SELECT 1
    FROM spend_envelopes AS existing
    WHERE existing.tenant_id = NEW.tenant_id
      AND existing.business_id = NEW.business_id
      AND existing.action_type = NEW.action_type
      AND existing.platform = NEW.platform
      AND existing.account_id = NEW.account_id
      AND NEW.period_start < existing.period_end
      AND NEW.period_end > existing.period_start
)
BEGIN
    SELECT RAISE(ABORT, 'spend envelope period overlaps existing authority');
END;

CREATE TRIGGER enforce_spend_commitment_insert
BEFORE INSERT ON spend_commitments
WHEN NOT EXISTS (
    SELECT 1
    FROM spend_envelopes AS envelope
    JOIN work_items AS work
      ON work.work_item_id = NEW.work_item_id
     AND work.tenant_id = NEW.tenant_id
     AND work.business_id = NEW.business_id
    WHERE envelope.envelope_id = NEW.envelope_id
      AND envelope.tenant_id = NEW.tenant_id
      AND envelope.business_id = NEW.business_id
      AND envelope.action_type = work.action_type
      AND envelope.platform = work.platform
      AND envelope.account_id = work.account_id
      AND envelope.currency = NEW.currency
      AND work.currency = NEW.currency
      AND work.amount IS NOT NULL
      AND ABS(
          CAST(work.amount AS REAL) * 100 - NEW.amount_minor
      ) < 0.000001
      AND work.status = 'claimed'
      AND work.assigned_actor_id != envelope.created_by
      AND NEW.created_at >= envelope.period_start
      AND NEW.created_at < envelope.period_end
)
 OR (
    SELECT COALESCE(SUM(existing.amount_minor), 0) + NEW.amount_minor
    FROM spend_commitments AS existing
    WHERE existing.envelope_id = NEW.envelope_id
 ) > (
    SELECT envelope.limit_minor
    FROM spend_envelopes AS envelope
    WHERE envelope.envelope_id = NEW.envelope_id
 )
BEGIN
    SELECT RAISE(
        ABORT,
        'spend commitment is invalid or exceeds remaining budget'
    );
END;

CREATE TRIGGER require_external_spend_commitment_insert
BEFORE INSERT ON execution_attempts
WHEN NEW.execution_mode = 'external'
 AND NEW.action_type LIKE '%.spend'
 AND NOT EXISTS (
    SELECT 1
    FROM spend_commitments AS commitment
    WHERE commitment.attempt_id = NEW.attempt_id
      AND commitment.work_item_id = NEW.work_item_id
      AND commitment.tenant_id = NEW.tenant_id
      AND commitment.business_id = NEW.business_id
 )
BEGIN
    SELECT RAISE(ABORT, 'external spend requires a durable budget commitment');
END;

CREATE TRIGGER protect_spend_work_semantics_update
BEFORE UPDATE OF
    tenant_id, business_id, action_type, assigned_actor_id, platform,
    account_id, amount, currency
ON work_items
WHEN EXISTS (
    SELECT 1
    FROM spend_commitments AS commitment
    WHERE commitment.work_item_id = OLD.work_item_id
)
AND (
    OLD.tenant_id IS NOT NEW.tenant_id
    OR OLD.business_id IS NOT NEW.business_id
    OR OLD.action_type IS NOT NEW.action_type
    OR OLD.assigned_actor_id IS NOT NEW.assigned_actor_id
    OR OLD.platform IS NOT NEW.platform
    OR OLD.account_id IS NOT NEW.account_id
    OR OLD.amount IS NOT NEW.amount
    OR OLD.currency IS NOT NEW.currency
)
BEGIN
    SELECT RAISE(ABORT, 'committed spend work semantics are immutable');
END;

CREATE TRIGGER protect_spend_work_delete
BEFORE DELETE ON work_items
WHEN EXISTS (
    SELECT 1
    FROM spend_commitments AS commitment
    WHERE commitment.work_item_id = OLD.work_item_id
)
BEGIN
    SELECT RAISE(ABORT, 'work deletion would orphan spend commitment');
END;

CREATE TRIGGER prevent_spend_envelope_update
BEFORE UPDATE ON spend_envelopes
BEGIN
    SELECT RAISE(ABORT, 'spend envelopes are append-only');
END;

CREATE TRIGGER prevent_spend_envelope_delete
BEFORE DELETE ON spend_envelopes
BEGIN
    SELECT RAISE(ABORT, 'spend envelopes are append-only');
END;

CREATE TRIGGER prevent_spend_commitment_update
BEFORE UPDATE ON spend_commitments
BEGIN
    SELECT RAISE(ABORT, 'spend commitments are append-only');
END;

CREATE TRIGGER prevent_spend_commitment_delete
BEFORE DELETE ON spend_commitments
BEGIN
    SELECT RAISE(ABORT, 'spend commitments are append-only');
END;
"""

MODEL_ROUTING_SCHEMA = """
CREATE TABLE model_catalog_versions (
    catalog_version TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE model_catalog_entries (
    catalog_version TEXT NOT NULL,
    model_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    provider_model_ref TEXT NOT NULL CHECK (
        length(trim(provider_model_ref)) > 0
        AND lower(provider_model_ref) NOT LIKE '%latest%'
        AND lower(provider_model_ref) NOT LIKE '%/auto'
        AND lower(provider_model_ref) != 'auto'
    ),
    reasoning_tier TEXT NOT NULL
        CHECK (reasoning_tier IN ('utility', 'standard', 'advanced')),
    tool_use INTEGER NOT NULL CHECK (tool_use IN (0, 1)),
    structured_output INTEGER NOT NULL CHECK (structured_output IN (0, 1)),
    modalities_json TEXT NOT NULL CHECK (
        json_valid(modalities_json)
        AND json_type(modalities_json) = 'array'
    ),
    context_window_tokens INTEGER NOT NULL CHECK (context_window_tokens > 0),
    allowed_data_classes_json TEXT NOT NULL CHECK (
        json_valid(allowed_data_classes_json)
        AND json_type(allowed_data_classes_json) = 'array'
    ),
    input_micros_per_million INTEGER NOT NULL
        CHECK (input_micros_per_million >= 0),
    output_micros_per_million INTEGER NOT NULL
        CHECK (output_micros_per_million >= 0),
    quality_score INTEGER NOT NULL CHECK (
        quality_score >= 0 AND quality_score <= 100
    ),
    evaluation_version TEXT NOT NULL CHECK (
        length(trim(evaluation_version)) > 0
    ),
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    PRIMARY KEY (catalog_version, model_id),
    FOREIGN KEY (catalog_version)
        REFERENCES model_catalog_versions(catalog_version),
    UNIQUE (catalog_version, provider_id, provider_model_ref)
);

CREATE TABLE model_catalog_activation_events (
    activation_id TEXT PRIMARY KEY,
    catalog_version TEXT NOT NULL,
    activated_at TEXT NOT NULL,
    FOREIGN KEY (catalog_version)
        REFERENCES model_catalog_versions(catalog_version)
);

CREATE TABLE provider_credentials (
    credential_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    credential_ref TEXT NOT NULL UNIQUE CHECK (
        (credential_ref LIKE 'vault://%' OR credential_ref LIKE 'env://%')
        AND instr(credential_ref, ' ') = 0
    ),
    created_at TEXT NOT NULL,
    FOREIGN KEY (business_id) REFERENCES businesses(business_id),
    UNIQUE (credential_id, tenant_id, business_id, provider_id)
);

CREATE TABLE provider_policy_revisions (
    policy_revision_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    credential_id TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    allowed_data_classes_json TEXT NOT NULL CHECK (
        json_valid(allowed_data_classes_json)
        AND json_type(allowed_data_classes_json) = 'array'
    ),
    allowed_model_ids_json TEXT NOT NULL CHECK (
        json_valid(allowed_model_ids_json)
        AND json_type(allowed_model_ids_json) = 'array'
    ),
    monthly_budget_micros INTEGER NOT NULL
        CHECK (monthly_budget_micros >= 0),
    created_at TEXT NOT NULL,
    FOREIGN KEY (business_id) REFERENCES businesses(business_id),
    FOREIGN KEY (
        credential_id, tenant_id, business_id, provider_id
    ) REFERENCES provider_credentials(
        credential_id, tenant_id, business_id, provider_id
    ),
    UNIQUE (tenant_id, business_id, provider_id, revision)
);

CREATE TABLE routing_decisions (
    decision_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    catalog_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('selected', 'held')),
    model_id TEXT,
    provider_id TEXT,
    credential_id TEXT,
    policy_revision_id TEXT,
    estimated_cost_micros INTEGER NOT NULL
        CHECK (estimated_cost_micros >= 0),
    candidate_order_json TEXT NOT NULL CHECK (
        json_valid(candidate_order_json)
        AND json_type(candidate_order_json) = 'array'
    ),
    rejection_reasons_json TEXT NOT NULL CHECK (
        json_valid(rejection_reasons_json)
        AND json_type(rejection_reasons_json) = 'object'
    ),
    previous_decision_id TEXT,
    is_circuit_probe INTEGER NOT NULL CHECK (is_circuit_probe IN (0, 1)),
    created_at TEXT NOT NULL,
    FOREIGN KEY (business_id) REFERENCES businesses(business_id),
    FOREIGN KEY (catalog_version)
        REFERENCES model_catalog_versions(catalog_version),
    FOREIGN KEY (credential_id) REFERENCES provider_credentials(credential_id),
    FOREIGN KEY (policy_revision_id)
        REFERENCES provider_policy_revisions(policy_revision_id),
    FOREIGN KEY (previous_decision_id)
        REFERENCES routing_decisions(decision_id),
    UNIQUE (tenant_id, business_id, request_id),
    CHECK (
        (status = 'selected' AND model_id IS NOT NULL
            AND provider_id IS NOT NULL AND credential_id IS NOT NULL
            AND policy_revision_id IS NOT NULL)
        OR
        (status = 'held' AND model_id IS NULL
            AND provider_id IS NULL AND credential_id IS NULL
            AND policy_revision_id IS NULL)
    )
);

CREATE TABLE model_usage_records (
    usage_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    credential_id TEXT NOT NULL,
    input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
    cost_micros INTEGER NOT NULL CHECK (cost_micros >= 0),
    outcome TEXT NOT NULL CHECK (
        outcome IN (
            'success', 'timeout', 'rate_limited', 'auth_error',
            'server_error', 'invalid_response'
        )
    ),
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    created_at TEXT NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES routing_decisions(decision_id),
    FOREIGN KEY (credential_id) REFERENCES provider_credentials(credential_id),
    UNIQUE (decision_id)
);

CREATE TABLE model_health_events (
    health_event_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (
        outcome IN (
            'success', 'timeout', 'rate_limited', 'auth_error',
            'server_error', 'invalid_response'
        )
    ),
    circuit_state TEXT NOT NULL CHECK (
        circuit_state IN ('closed', 'open', 'half_open')
    ),
    consecutive_failures INTEGER NOT NULL CHECK (consecutive_failures >= 0),
    open_until TEXT,
    observed_at TEXT NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES routing_decisions(decision_id),
    UNIQUE (decision_id)
);

CREATE TABLE model_circuit_states (
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    circuit_state TEXT NOT NULL CHECK (
        circuit_state IN ('closed', 'open', 'half_open')
    ),
    consecutive_failures INTEGER NOT NULL CHECK (consecutive_failures >= 0),
    open_until TEXT,
    probe_in_flight INTEGER NOT NULL CHECK (probe_in_flight IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, business_id, provider_id, model_id),
    FOREIGN KEY (business_id) REFERENCES businesses(business_id)
);

CREATE INDEX idx_catalog_entries_provider
    ON model_catalog_entries(catalog_version, provider_id, enabled);
CREATE INDEX idx_policy_current
    ON provider_policy_revisions(
        tenant_id, business_id, provider_id, revision DESC
    );
CREATE INDEX idx_routing_decisions_scope
    ON routing_decisions(tenant_id, business_id, created_at);
CREATE INDEX idx_usage_scope
    ON model_usage_records(tenant_id, business_id, created_at);
CREATE INDEX idx_health_scope
    ON model_health_events(
        tenant_id, business_id, provider_id, model_id, observed_at
    );

CREATE TRIGGER enforce_provider_credential_scope_insert
BEFORE INSERT ON provider_credentials
WHEN NOT EXISTS (
    SELECT 1 FROM businesses AS business
    WHERE business.business_id = NEW.business_id
      AND business.tenant_id = NEW.tenant_id
)
BEGIN
    SELECT RAISE(ABORT, 'credential reference crosses tenant scope');
END;

CREATE TRIGGER enforce_policy_revision_sequence_insert
BEFORE INSERT ON provider_policy_revisions
WHEN NEW.revision != COALESCE(
    (
        SELECT MAX(existing.revision) + 1
        FROM provider_policy_revisions AS existing
        WHERE existing.tenant_id = NEW.tenant_id
          AND existing.business_id = NEW.business_id
          AND existing.provider_id = NEW.provider_id
    ),
    1
)
BEGIN
    SELECT RAISE(ABORT, 'provider policy revision is out of sequence');
END;

CREATE TRIGGER enforce_routing_decision_identity_insert
BEFORE INSERT ON routing_decisions
WHEN NOT EXISTS (
    SELECT 1 FROM businesses AS business
    WHERE business.business_id = NEW.business_id
      AND business.tenant_id = NEW.tenant_id
)
OR (
    NEW.status = 'selected'
    AND (
        NOT EXISTS (
            SELECT 1
            FROM model_catalog_entries AS entry
            WHERE entry.catalog_version = NEW.catalog_version
              AND entry.model_id = NEW.model_id
              AND entry.provider_id = NEW.provider_id
              AND entry.enabled = 1
        )
        OR NOT EXISTS (
            SELECT 1
            FROM provider_credentials AS credential
            JOIN provider_policy_revisions AS policy
              ON policy.policy_revision_id = NEW.policy_revision_id
             AND policy.credential_id = credential.credential_id
             AND policy.tenant_id = credential.tenant_id
             AND policy.business_id = credential.business_id
             AND policy.provider_id = credential.provider_id
            WHERE credential.credential_id = NEW.credential_id
              AND credential.tenant_id = NEW.tenant_id
              AND credential.business_id = NEW.business_id
              AND credential.provider_id = NEW.provider_id
              AND policy.enabled = 1
        )
    )
)
OR (
    NEW.previous_decision_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM routing_decisions AS previous
        WHERE previous.decision_id = NEW.previous_decision_id
          AND previous.tenant_id = NEW.tenant_id
          AND previous.business_id = NEW.business_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'routing decision identity is invalid');
END;

CREATE TRIGGER enforce_model_usage_identity_insert
BEFORE INSERT ON model_usage_records
WHEN NOT EXISTS (
    SELECT 1
    FROM routing_decisions AS decision
    WHERE decision.decision_id = NEW.decision_id
      AND decision.tenant_id = NEW.tenant_id
      AND decision.business_id = NEW.business_id
      AND decision.provider_id = NEW.provider_id
      AND decision.model_id = NEW.model_id
      AND decision.credential_id = NEW.credential_id
      AND decision.status = 'selected'
      AND NEW.created_at >= decision.created_at
)
BEGIN
    SELECT RAISE(ABORT, 'usage record crosses routing decision identity');
END;

CREATE TRIGGER enforce_model_health_identity_insert
BEFORE INSERT ON model_health_events
WHEN NOT EXISTS (
    SELECT 1
    FROM routing_decisions AS decision
    JOIN model_usage_records AS usage
      ON usage.decision_id = decision.decision_id
     AND usage.tenant_id = decision.tenant_id
     AND usage.business_id = decision.business_id
     AND usage.provider_id = decision.provider_id
     AND usage.model_id = decision.model_id
     AND usage.outcome = NEW.outcome
     AND usage.created_at = NEW.observed_at
    WHERE decision.decision_id = NEW.decision_id
      AND decision.tenant_id = NEW.tenant_id
      AND decision.business_id = NEW.business_id
      AND decision.provider_id = NEW.provider_id
      AND decision.model_id = NEW.model_id
      AND decision.status = 'selected'
)
BEGIN
    SELECT RAISE(ABORT, 'health event crosses routing decision identity');
END;

CREATE TRIGGER enforce_circuit_scope_insert
BEFORE INSERT ON model_circuit_states
WHEN NOT EXISTS (
    SELECT 1 FROM businesses AS business
    WHERE business.business_id = NEW.business_id
      AND business.tenant_id = NEW.tenant_id
)
BEGIN
    SELECT RAISE(ABORT, 'circuit state crosses tenant scope');
END;

CREATE TRIGGER protect_circuit_scope_update
BEFORE UPDATE OF tenant_id, business_id, provider_id, model_id
ON model_circuit_states
BEGIN
    SELECT RAISE(ABORT, 'circuit identity is immutable');
END;

CREATE TRIGGER prevent_model_catalog_versions_update
BEFORE UPDATE ON model_catalog_versions
BEGIN SELECT RAISE(ABORT, 'model catalog versions are append-only'); END;
CREATE TRIGGER prevent_model_catalog_versions_delete
BEFORE DELETE ON model_catalog_versions
BEGIN SELECT RAISE(ABORT, 'model catalog versions are append-only'); END;
CREATE TRIGGER prevent_model_catalog_entries_update
BEFORE UPDATE ON model_catalog_entries
BEGIN SELECT RAISE(ABORT, 'model catalog entries are append-only'); END;
CREATE TRIGGER prevent_model_catalog_entries_delete
BEFORE DELETE ON model_catalog_entries
BEGIN SELECT RAISE(ABORT, 'model catalog entries are append-only'); END;
CREATE TRIGGER prevent_model_catalog_activation_events_update
BEFORE UPDATE ON model_catalog_activation_events
BEGIN SELECT RAISE(ABORT, 'catalog activations are append-only'); END;
CREATE TRIGGER prevent_model_catalog_activation_events_delete
BEFORE DELETE ON model_catalog_activation_events
BEGIN SELECT RAISE(ABORT, 'catalog activations are append-only'); END;
CREATE TRIGGER prevent_provider_credentials_update
BEFORE UPDATE ON provider_credentials
BEGIN SELECT RAISE(ABORT, 'credential references are append-only'); END;
CREATE TRIGGER prevent_provider_credentials_delete
BEFORE DELETE ON provider_credentials
BEGIN SELECT RAISE(ABORT, 'credential references are append-only'); END;
CREATE TRIGGER prevent_provider_policy_revisions_update
BEFORE UPDATE ON provider_policy_revisions
BEGIN SELECT RAISE(ABORT, 'provider policies are append-only'); END;
CREATE TRIGGER prevent_provider_policy_revisions_delete
BEFORE DELETE ON provider_policy_revisions
BEGIN SELECT RAISE(ABORT, 'provider policies are append-only'); END;
CREATE TRIGGER prevent_routing_decisions_update
BEFORE UPDATE ON routing_decisions
BEGIN SELECT RAISE(ABORT, 'routing decisions are append-only'); END;
CREATE TRIGGER prevent_routing_decisions_delete
BEFORE DELETE ON routing_decisions
BEGIN SELECT RAISE(ABORT, 'routing decisions are append-only'); END;
CREATE TRIGGER prevent_model_usage_records_update
BEFORE UPDATE ON model_usage_records
BEGIN SELECT RAISE(ABORT, 'model usage is append-only'); END;
CREATE TRIGGER prevent_model_usage_records_delete
BEFORE DELETE ON model_usage_records
BEGIN SELECT RAISE(ABORT, 'model usage is append-only'); END;
CREATE TRIGGER prevent_model_health_events_update
BEFORE UPDATE ON model_health_events
BEGIN SELECT RAISE(ABORT, 'model health events are append-only'); END;
CREATE TRIGGER prevent_model_health_events_delete
BEFORE DELETE ON model_health_events
BEGIN SELECT RAISE(ABORT, 'model health events are append-only'); END;
"""

SHADOW_MODEL_RUNTIME_SCHEMA = """
CREATE TABLE shadow_model_attempts (
    attempt_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    credential_id TEXT NOT NULL,
    attempt_kind TEXT NOT NULL CHECK (
        attempt_kind IN ('proposal', 'canary')
    ),
    prompt_template_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt_hash TEXT NOT NULL CHECK (length(prompt_hash) = 64),
    context_hash TEXT NOT NULL CHECK (length(context_hash) = 64),
    output_schema_hash TEXT NOT NULL CHECK (length(output_schema_hash) = 64),
    input_token_estimate INTEGER NOT NULL CHECK (input_token_estimate > 0),
    max_output_tokens INTEGER NOT NULL CHECK (max_output_tokens > 0),
    created_at TEXT NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES routing_decisions(decision_id),
    FOREIGN KEY (credential_id) REFERENCES provider_credentials(credential_id)
);

CREATE TABLE shadow_model_outcomes (
    outcome_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE,
    decision_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('succeeded', 'failed', 'isolated')
    ),
    provider_outcome TEXT NOT NULL CHECK (
        provider_outcome IN (
            'success', 'timeout', 'rate_limited', 'auth_error',
            'server_error', 'invalid_response'
        )
    ),
    provider_request_id TEXT,
    output_hash TEXT CHECK (output_hash IS NULL OR length(output_hash) = 64),
    validation_version TEXT NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (attempt_id) REFERENCES shadow_model_attempts(attempt_id),
    FOREIGN KEY (decision_id) REFERENCES routing_decisions(decision_id),
    CHECK (
        (status = 'succeeded' AND provider_outcome = 'success'
            AND output_hash IS NOT NULL AND error_code IS NULL)
        OR
        (status = 'failed' AND provider_outcome != 'success'
            AND error_code IS NOT NULL)
        OR
        (status = 'isolated' AND error_code IS NOT NULL)
    )
);

CREATE TABLE model_evaluation_replays (
    replay_id TEXT PRIMARY KEY,
    suite_id TEXT NOT NULL,
    suite_version TEXT NOT NULL,
    fixture_hash TEXT NOT NULL CHECK (length(fixture_hash) = 64),
    evaluator_version TEXT NOT NULL,
    case_count INTEGER NOT NULL CHECK (case_count > 0),
    passed_count INTEGER NOT NULL CHECK (
        passed_count >= 0 AND passed_count <= case_count
    ),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE (suite_id, suite_version, fixture_hash, evaluator_version)
);

CREATE INDEX idx_shadow_attempt_scope
    ON shadow_model_attempts(tenant_id, business_id, created_at);
CREATE INDEX idx_shadow_outcome_scope
    ON shadow_model_outcomes(tenant_id, business_id, created_at);
CREATE INDEX idx_model_evaluation_replays
    ON model_evaluation_replays(suite_id, suite_version, created_at);

CREATE TRIGGER enforce_shadow_attempt_identity_insert
BEFORE INSERT ON shadow_model_attempts
WHEN NOT EXISTS (
    SELECT 1
    FROM routing_decisions AS decision
    WHERE decision.decision_id = NEW.decision_id
      AND decision.tenant_id = NEW.tenant_id
      AND decision.business_id = NEW.business_id
      AND decision.provider_id = NEW.provider_id
      AND decision.model_id = NEW.model_id
      AND decision.credential_id = NEW.credential_id
      AND decision.status = 'selected'
      AND NEW.created_at >= decision.created_at
      AND (
          NEW.attempt_kind != 'canary'
          OR json_extract(decision.request_json, '$.data_class') = 'public'
      )
      AND NOT EXISTS (
          SELECT 1 FROM model_usage_records AS usage
          WHERE usage.decision_id = decision.decision_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'shadow attempt crosses selected route identity');
END;

CREATE TRIGGER enforce_shadow_outcome_identity_insert
BEFORE INSERT ON shadow_model_outcomes
WHEN NOT EXISTS (
    SELECT 1
    FROM shadow_model_attempts AS attempt
    JOIN model_usage_records AS usage
      ON usage.decision_id = attempt.decision_id
     AND usage.tenant_id = attempt.tenant_id
     AND usage.business_id = attempt.business_id
     AND usage.provider_id = attempt.provider_id
     AND usage.model_id = attempt.model_id
    WHERE attempt.attempt_id = NEW.attempt_id
      AND attempt.decision_id = NEW.decision_id
      AND attempt.tenant_id = NEW.tenant_id
      AND attempt.business_id = NEW.business_id
      AND attempt.provider_id = NEW.provider_id
      AND attempt.model_id = NEW.model_id
      AND usage.outcome = NEW.provider_outcome
      AND usage.created_at <= NEW.created_at
)
BEGIN
    SELECT RAISE(ABORT, 'shadow outcome lacks matching usage evidence');
END;

CREATE TRIGGER prevent_shadow_model_attempts_update
BEFORE UPDATE ON shadow_model_attempts
BEGIN SELECT RAISE(ABORT, 'shadow attempts are append-only'); END;
CREATE TRIGGER prevent_shadow_model_attempts_delete
BEFORE DELETE ON shadow_model_attempts
BEGIN SELECT RAISE(ABORT, 'shadow attempts are append-only'); END;
CREATE TRIGGER prevent_shadow_model_outcomes_update
BEFORE UPDATE ON shadow_model_outcomes
BEGIN SELECT RAISE(ABORT, 'shadow outcomes are append-only'); END;
CREATE TRIGGER prevent_shadow_model_outcomes_delete
BEFORE DELETE ON shadow_model_outcomes
BEGIN SELECT RAISE(ABORT, 'shadow outcomes are append-only'); END;
CREATE TRIGGER prevent_model_evaluation_replays_update
BEFORE UPDATE ON model_evaluation_replays
BEGIN SELECT RAISE(ABORT, 'evaluation replays are append-only'); END;
CREATE TRIGGER prevent_model_evaluation_replays_delete
BEFORE DELETE ON model_evaluation_replays
BEGIN SELECT RAISE(ABORT, 'evaluation replays are append-only'); END;
"""

AFFILIATE_SHADOW_LOOP_SCHEMA = """
CREATE TABLE affiliate_shadow_runs (
    run_id TEXT PRIMARY KEY, objective_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL, business_id TEXT NOT NULL, producer_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (objective_id) REFERENCES objectives(objective_id),
    FOREIGN KEY (producer_id) REFERENCES actors(actor_id)
);
CREATE TABLE affiliate_offer_snapshots (
    snapshot_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL, offer_key TEXT NOT NULL,
    source_system TEXT NOT NULL CHECK (source_system LIKE '%-readonly'),
    source_ref TEXT NOT NULL, merchant_name TEXT NOT NULL, channel TEXT NOT NULL,
    destination_url TEXT NOT NULL, currency TEXT NOT NULL,
    commission_rate_bps INTEGER NOT NULL CHECK (commission_rate_bps >= 0),
    expected_order_value_minor INTEGER NOT NULL CHECK (expected_order_value_minor >= 0),
    audience_fit_score INTEGER NOT NULL CHECK (audience_fit_score BETWEEN 0 AND 10000),
    evidence_confidence_bps INTEGER NOT NULL CHECK (evidence_confidence_bps BETWEEN 0 AND 10000),
    destination_healthy INTEGER NOT NULL CHECK (destination_healthy IN (0,1)),
    terms_verified INTEGER NOT NULL CHECK (terms_verified IN (0,1)),
    disclosure_required TEXT NOT NULL, approved_claims_json TEXT NOT NULL CHECK (json_valid(approved_claims_json)),
    evidence_refs_json TEXT NOT NULL CHECK (json_valid(evidence_refs_json)),
    observed_at TEXT NOT NULL, content_hash TEXT NOT NULL CHECK (length(content_hash)=64),
    created_at TEXT NOT NULL, FOREIGN KEY (run_id) REFERENCES affiliate_shadow_runs(run_id),
    UNIQUE (run_id, offer_key, content_hash)
);
CREATE TABLE affiliate_recommendations (
    recommendation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL, business_id TEXT NOT NULL,
    selected_snapshot_id TEXT, status TEXT NOT NULL CHECK (status IN ('selected','held')),
    candidate_order_json TEXT NOT NULL CHECK (json_valid(candidate_order_json)),
    rejection_reasons_json TEXT NOT NULL CHECK (json_valid(rejection_reasons_json)),
    evaluator_version TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES affiliate_shadow_runs(run_id),
    FOREIGN KEY (selected_snapshot_id) REFERENCES affiliate_offer_snapshots(snapshot_id),
    CHECK ((status='selected' AND selected_snapshot_id IS NOT NULL) OR (status='held' AND selected_snapshot_id IS NULL))
);
CREATE TABLE affiliate_content_proposals (
    proposal_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE,
    recommendation_id TEXT NOT NULL UNIQUE, shadow_attempt_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL, business_id TEXT NOT NULL, channel TEXT NOT NULL,
    headline TEXT NOT NULL, body TEXT NOT NULL, disclosure TEXT NOT NULL,
    call_to_action TEXT NOT NULL, destination_url TEXT NOT NULL,
    claims_json TEXT NOT NULL CHECK (json_valid(claims_json)),
    content_hash TEXT NOT NULL CHECK (length(content_hash)=64),
    status TEXT NOT NULL CHECK (status='proposed'), created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES affiliate_shadow_runs(run_id),
    FOREIGN KEY (recommendation_id) REFERENCES affiliate_recommendations(recommendation_id),
    FOREIGN KEY (shadow_attempt_id) REFERENCES shadow_model_attempts(attempt_id)
);
CREATE TABLE affiliate_experiments (
    experiment_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE,
    proposal_id TEXT NOT NULL UNIQUE, tenant_id TEXT NOT NULL, business_id TEXT NOT NULL,
    tracking_key TEXT NOT NULL UNIQUE, mode TEXT NOT NULL CHECK (mode='historical_replay'),
    hypothesis TEXT NOT NULL, window_start TEXT NOT NULL, window_end TEXT NOT NULL,
    minimum_clicks INTEGER NOT NULL CHECK (minimum_clicks > 0),
    status TEXT NOT NULL CHECK (status='shadow'), created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES affiliate_shadow_runs(run_id),
    FOREIGN KEY (proposal_id) REFERENCES affiliate_content_proposals(proposal_id),
    CHECK (window_start < window_end)
);
CREATE TABLE affiliate_observations (
    observation_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL, business_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('impression','click','conversion')),
    subject_key TEXT NOT NULL, click_observation_id TEXT,
    gross_revenue_minor INTEGER NOT NULL CHECK (gross_revenue_minor >= 0),
    commission_minor INTEGER NOT NULL CHECK (commission_minor >= 0 AND commission_minor <= gross_revenue_minor),
    source_system TEXT NOT NULL CHECK (source_system LIKE '%-readonly'), source_ref TEXT NOT NULL,
    payload_hash TEXT NOT NULL CHECK (length(payload_hash)=64),
    occurred_at TEXT NOT NULL, imported_at TEXT NOT NULL,
    FOREIGN KEY (experiment_id) REFERENCES affiliate_experiments(experiment_id),
    FOREIGN KEY (click_observation_id) REFERENCES affiliate_observations(observation_id),
    UNIQUE (experiment_id, source_system, source_ref),
    CHECK ((kind='conversion' AND click_observation_id IS NOT NULL) OR (kind!='conversion' AND click_observation_id IS NULL)),
    CHECK (kind='conversion' OR (gross_revenue_minor=0 AND commission_minor=0))
);
CREATE TABLE affiliate_measurements (
    measurement_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL, business_id TEXT NOT NULL,
    impression_count INTEGER NOT NULL, click_count INTEGER NOT NULL,
    conversion_count INTEGER NOT NULL, conversion_rate_bps INTEGER NOT NULL,
    gross_revenue_minor INTEGER NOT NULL, commission_minor INTEGER NOT NULL,
    sufficient_sample INTEGER NOT NULL CHECK (sufficient_sample IN (0,1)),
    measurement_hash TEXT NOT NULL CHECK (length(measurement_hash)=64), measured_at TEXT NOT NULL,
    FOREIGN KEY (experiment_id) REFERENCES affiliate_experiments(experiment_id)
);
CREATE TABLE affiliate_verifications (
    verification_id TEXT PRIMARY KEY, measurement_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL, business_id TEXT NOT NULL, verifier_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('verified','inconclusive','rejected')),
    recomputed_hash TEXT NOT NULL CHECK (length(recomputed_hash)=64),
    rationale TEXT NOT NULL, verified_at TEXT NOT NULL,
    FOREIGN KEY (measurement_id) REFERENCES affiliate_measurements(measurement_id),
    FOREIGN KEY (verifier_id) REFERENCES actors(actor_id)
);
CREATE TABLE affiliate_learnings (
    learning_id TEXT PRIMARY KEY, verification_id TEXT NOT NULL UNIQUE,
    memory_id TEXT NOT NULL UNIQUE, tenant_id TEXT NOT NULL, business_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('recommend','revise','stop')),
    statement_hash TEXT NOT NULL CHECK (length(statement_hash)=64), created_at TEXT NOT NULL,
    FOREIGN KEY (verification_id) REFERENCES affiliate_verifications(verification_id),
    FOREIGN KEY (memory_id) REFERENCES memory_records(memory_id)
);
CREATE INDEX idx_affiliate_offer_run ON affiliate_offer_snapshots(run_id, observed_at);
CREATE INDEX idx_affiliate_observation_experiment ON affiliate_observations(experiment_id, occurred_at);
CREATE TRIGGER enforce_affiliate_offer_research_insert BEFORE INSERT ON affiliate_offer_snapshots
WHEN EXISTS (SELECT 1 FROM affiliate_recommendations WHERE run_id=NEW.run_id)
BEGIN SELECT RAISE(ABORT, 'affiliate offer research is frozen after recommendation'); END;
CREATE TRIGGER enforce_affiliate_content_shadow_insert BEFORE INSERT ON affiliate_content_proposals
WHEN NOT EXISTS (
  SELECT 1 FROM affiliate_recommendations r
  JOIN affiliate_offer_snapshots o ON o.snapshot_id=r.selected_snapshot_id
  JOIN shadow_model_attempts a ON a.attempt_id=NEW.shadow_attempt_id
  JOIN shadow_model_outcomes z ON z.attempt_id=a.attempt_id AND z.status='succeeded' AND z.output_hash=NEW.content_hash
  WHERE r.recommendation_id=NEW.recommendation_id AND r.run_id=NEW.run_id
    AND r.tenant_id=NEW.tenant_id AND r.business_id=NEW.business_id
    AND o.run_id=NEW.run_id AND o.tenant_id=NEW.tenant_id AND o.business_id=NEW.business_id
    AND a.tenant_id=NEW.tenant_id AND a.business_id=NEW.business_id
    AND NEW.destination_url=o.destination_url AND NEW.channel=o.channel
    AND NEW.disclosure=o.disclosure_required AND json_type(NEW.claims_json)='array'
    AND NOT EXISTS (
      SELECT 1 FROM json_each(NEW.claims_json) claim
      WHERE claim.value NOT IN (SELECT value FROM json_each(o.approved_claims_json))
    )
)
BEGIN SELECT RAISE(ABORT, 'affiliate content lacks selected offer or validated shadow output'); END;
CREATE TRIGGER enforce_affiliate_experiment_shadow_insert BEFORE INSERT ON affiliate_experiments
WHEN NEW.window_end>NEW.created_at OR NOT EXISTS (
  SELECT 1 FROM affiliate_content_proposals p
  WHERE p.proposal_id=NEW.proposal_id AND p.run_id=NEW.run_id
    AND p.tenant_id=NEW.tenant_id AND p.business_id=NEW.business_id
    AND p.status='proposed'
)
BEGIN SELECT RAISE(ABORT, 'affiliate experiment must be same-scope historical replay'); END;
CREATE TRIGGER enforce_affiliate_observation_replay_insert BEFORE INSERT ON affiliate_observations
WHEN EXISTS (SELECT 1 FROM affiliate_measurements WHERE experiment_id=NEW.experiment_id)
  OR NOT EXISTS (
    SELECT 1 FROM affiliate_experiments e WHERE e.experiment_id=NEW.experiment_id
      AND e.tenant_id=NEW.tenant_id AND e.business_id=NEW.business_id
      AND e.mode='historical_replay' AND e.status='shadow'
      AND NEW.occurred_at>=e.window_start AND NEW.occurred_at<e.window_end
  )
BEGIN SELECT RAISE(ABORT, 'affiliate observation is outside the open historical replay'); END;
CREATE TRIGGER enforce_affiliate_conversion_click_insert BEFORE INSERT ON affiliate_observations
WHEN NEW.kind='conversion' AND NOT EXISTS (
  SELECT 1 FROM affiliate_observations c WHERE c.observation_id=NEW.click_observation_id
  AND c.experiment_id=NEW.experiment_id AND c.kind='click' AND c.subject_key=NEW.subject_key
  AND c.occurred_at <= NEW.occurred_at
)
BEGIN SELECT RAISE(ABORT, 'affiliate conversion lacks a prior matching click'); END;
CREATE TRIGGER enforce_affiliate_verifier_insert BEFORE INSERT ON affiliate_verifications
WHEN NOT EXISTS (
  SELECT 1 FROM affiliate_measurements m JOIN affiliate_experiments e ON e.experiment_id=m.experiment_id
  JOIN affiliate_shadow_runs r ON r.run_id=e.run_id JOIN actors a ON a.actor_id=NEW.verifier_id
  JOIN json_each(a.business_ids_json) b ON b.value=NEW.business_id
  WHERE m.measurement_id=NEW.measurement_id AND m.tenant_id=NEW.tenant_id AND m.business_id=NEW.business_id
  AND a.tenant_id=NEW.tenant_id AND a.enabled=1 AND a.actor_id!=r.producer_id
  AND EXISTS (SELECT 1 FROM json_each(a.roles_json) role WHERE role.value IN ('qa','verifier'))
)
BEGIN SELECT RAISE(ABORT, 'affiliate verification requires independent scoped QA'); END;
""" + "\n\n" + "\n".join(
    f"CREATE TRIGGER prevent_{table}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;"
    for table in (
        "affiliate_shadow_runs", "affiliate_offer_snapshots", "affiliate_recommendations",
        "affiliate_content_proposals", "affiliate_experiments", "affiliate_observations",
        "affiliate_measurements", "affiliate_verifications", "affiliate_learnings",
    )
    for operation in ("UPDATE", "DELETE")
)

PORTFOLIO_CAPABILITY_EXPANSION_SCHEMA = """
CREATE TABLE capability_pack_acceptances (
    acceptance_id TEXT PRIMARY KEY, pack_id TEXT NOT NULL,
    pack_version TEXT NOT NULL, pack_hash TEXT NOT NULL CHECK (length(pack_hash)=64),
    evaluator_version TEXT NOT NULL, case_count INTEGER NOT NULL CHECK (case_count>0),
    passed_count INTEGER NOT NULL CHECK (passed_count BETWEEN 0 AND case_count),
    passed INTEGER NOT NULL CHECK (passed IN (0,1)), accepted_at TEXT NOT NULL,
    UNIQUE (pack_id, pack_version, pack_hash, evaluator_version),
    CHECK (passed=(passed_count=case_count))
);
CREATE TABLE aggregate_performance_snapshots (
    snapshot_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, business_id TEXT NOT NULL,
    producer_id TEXT NOT NULL, channel TEXT NOT NULL, offer_key TEXT NOT NULL,
    source_system TEXT NOT NULL CHECK (source_system LIKE '%-readonly'),
    source_ref TEXT NOT NULL, window_start TEXT NOT NULL, window_end TEXT NOT NULL,
    impressions INTEGER NOT NULL CHECK (impressions>=0),
    engagements INTEGER NOT NULL CHECK (engagements BETWEEN 0 AND impressions),
    content_clicks INTEGER NOT NULL CHECK (content_clicks BETWEEN 0 AND engagements),
    outbound_clicks INTEGER NOT NULL CHECK (outbound_clicks BETWEEN 0 AND content_clicks),
    conversions INTEGER NOT NULL CHECK (conversions BETWEEN 0 AND outbound_clicks),
    gross_revenue_minor INTEGER NOT NULL CHECK (gross_revenue_minor>=0),
    commission_minor INTEGER NOT NULL CHECK (commission_minor BETWEEN 0 AND gross_revenue_minor),
    minimum_outbound_clicks INTEGER NOT NULL CHECK (minimum_outbound_clicks>0),
    evidence_refs_json TEXT NOT NULL CHECK (json_valid(evidence_refs_json) AND json_type(evidence_refs_json)='array'),
    evidence_class TEXT NOT NULL CHECK (evidence_class='directional_aggregate'),
    snapshot_hash TEXT NOT NULL CHECK (length(snapshot_hash)=64), imported_at TEXT NOT NULL,
    limitation TEXT NOT NULL CHECK (
      limitation='Aggregate evidence does not identify people or prove incrementality.'
    ),
    FOREIGN KEY (producer_id) REFERENCES actors(actor_id),
    UNIQUE (tenant_id, business_id, source_system, source_ref, window_start, window_end),
    CHECK (window_start<window_end AND window_end<=imported_at)
);
CREATE TABLE aggregate_performance_verifications (
    verification_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL, business_id TEXT NOT NULL, verifier_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('verified','inconclusive','rejected')),
    recomputed_hash TEXT NOT NULL CHECK (length(recomputed_hash)=64),
    rationale TEXT NOT NULL, verified_at TEXT NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES aggregate_performance_snapshots(snapshot_id),
    FOREIGN KEY (verifier_id) REFERENCES actors(actor_id)
);
CREATE INDEX idx_aggregate_performance_scope
    ON aggregate_performance_snapshots(tenant_id, business_id, channel, window_end);
CREATE TRIGGER enforce_aggregate_verifier_insert
BEFORE INSERT ON aggregate_performance_verifications
WHEN NOT EXISTS (
  SELECT 1 FROM aggregate_performance_snapshots snapshot
  JOIN actors verifier ON verifier.actor_id=NEW.verifier_id
  JOIN json_each(verifier.business_ids_json) business ON business.value=NEW.business_id
  WHERE snapshot.snapshot_id=NEW.snapshot_id
    AND snapshot.tenant_id=NEW.tenant_id AND snapshot.business_id=NEW.business_id
    AND verifier.tenant_id=NEW.tenant_id AND verifier.enabled=1
    AND verifier.actor_id!=snapshot.producer_id
    AND EXISTS (
      SELECT 1 FROM json_each(verifier.roles_json) role
      WHERE role.value IN ('qa','verifier','platform-reliability')
    )
)
BEGIN SELECT RAISE(ABORT, 'aggregate verification requires independent scoped QA'); END;
""" + "\n\n" + "\n".join(
    f"CREATE TRIGGER prevent_{table}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;"
    for table in (
        "capability_pack_acceptances", "aggregate_performance_snapshots",
        "aggregate_performance_verifications",
    )
    for operation in ("UPDATE", "DELETE")
)

PRODUCTION_AND_RESALE_HARDENING_SCHEMA = """
CREATE TABLE production_qualifications (
    qualification_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
      'packaging','onboarding','persistence','security','observability',
      'recovery','cost','upgrade'
    )),
    release_version TEXT NOT NULL,
    artifact_hash TEXT NOT NULL CHECK (length(artifact_hash)=64),
    checks_json TEXT NOT NULL CHECK (
      json_valid(checks_json) AND json_type(checks_json)='object'
      AND length(checks_json)>2
    ),
    checks_hash TEXT NOT NULL CHECK (length(checks_hash)=64),
    producer_id TEXT NOT NULL,
    verifier_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('passed','held')),
    external_side_effects_enabled INTEGER NOT NULL DEFAULT 0
      CHECK (external_side_effects_enabled=0),
    qualified_at TEXT NOT NULL,
    FOREIGN KEY (producer_id) REFERENCES actors(actor_id),
    FOREIGN KEY (verifier_id) REFERENCES actors(actor_id),
    CHECK (producer_id!=verifier_id),
    UNIQUE (tenant_id, business_id, kind, release_version, artifact_hash)
);
CREATE TABLE legacy_cutover_plans (
    plan_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    source_system TEXT NOT NULL CHECK (source_system IN (
      'agent-os-v1','openclaw-legacy'
    )),
    capability_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('read_only','proposal','shadow')),
    owner_id TEXT NOT NULL,
    rollback_hash TEXT NOT NULL CHECK (length(rollback_hash)=64),
    legacy_disable_allowed INTEGER NOT NULL DEFAULT 0
      CHECK (legacy_disable_allowed=0),
    external_side_effects_enabled INTEGER NOT NULL DEFAULT 0
      CHECK (external_side_effects_enabled=0),
    created_at TEXT NOT NULL,
    FOREIGN KEY (owner_id) REFERENCES actors(actor_id),
    UNIQUE (tenant_id, business_id, source_system, capability_id)
);
CREATE TABLE legacy_cutover_events (
    event_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN (
      'inventoried','shadow_compared','recovery_verified','approved',
      'canary_observed','rolled_back'
    )),
    actor_id TEXT NOT NULL,
    evidence_hash TEXT NOT NULL CHECK (length(evidence_hash)=64),
    created_at TEXT NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES legacy_cutover_plans(plan_id),
    FOREIGN KEY (actor_id) REFERENCES actors(actor_id)
);
CREATE INDEX idx_production_qualification_scope
  ON production_qualifications(tenant_id,business_id,release_version,kind);
CREATE INDEX idx_legacy_cutover_scope
  ON legacy_cutover_events(tenant_id,business_id,plan_id,created_at);
CREATE TRIGGER enforce_production_qualification_insert
BEFORE INSERT ON production_qualifications
WHEN NOT EXISTS (
  SELECT 1 FROM actors producer, json_each(producer.business_ids_json) pb,
       actors verifier, json_each(verifier.business_ids_json) vb
  WHERE producer.actor_id=NEW.producer_id AND verifier.actor_id=NEW.verifier_id
    AND producer.actor_id!=verifier.actor_id
    AND producer.enabled=1 AND verifier.enabled=1
    AND producer.tenant_id=NEW.tenant_id AND verifier.tenant_id=NEW.tenant_id
    AND pb.value=NEW.business_id AND vb.value=NEW.business_id
    AND EXISTS (SELECT 1 FROM json_each(producer.roles_json) r
                WHERE r.value IN ('platform-reliability','operations'))
    AND EXISTS (SELECT 1 FROM json_each(verifier.roles_json) r
                WHERE r.value IN ('qa','verifier'))
)
BEGIN SELECT RAISE(ABORT, 'production qualification requires independent scoped operations and QA'); END;
CREATE TRIGGER enforce_production_qualification_decision_insert
BEFORE INSERT ON production_qualifications
WHEN (NEW.decision='passed' AND EXISTS (
        SELECT 1 FROM json_each(NEW.checks_json) WHERE value NOT IN (1,'true')
      )) OR (NEW.decision='held' AND NOT EXISTS (
        SELECT 1 FROM json_each(NEW.checks_json) WHERE value NOT IN (1,'true')
      ))
BEGIN SELECT RAISE(ABORT, 'production qualification decision contradicts checks'); END;
CREATE TRIGGER enforce_legacy_cutover_plan_owner_insert
BEFORE INSERT ON legacy_cutover_plans
WHEN NOT EXISTS (
  SELECT 1 FROM actors owner, json_each(owner.business_ids_json) membership
  WHERE owner.actor_id=NEW.owner_id AND owner.tenant_id=NEW.tenant_id
    AND owner.enabled=1 AND membership.value=NEW.business_id
    AND EXISTS (SELECT 1 FROM json_each(owner.roles_json) role
                WHERE role.value IN ('operations','platform-reliability'))
)
BEGIN SELECT RAISE(ABORT, 'legacy cutover owner is outside scoped operations'); END;
CREATE TRIGGER enforce_legacy_cutover_event_scope_insert
BEFORE INSERT ON legacy_cutover_events
WHEN NOT EXISTS (
  SELECT 1 FROM legacy_cutover_plans plan
  JOIN actors actor ON actor.actor_id=NEW.actor_id
  JOIN json_each(actor.business_ids_json) membership ON membership.value=NEW.business_id
  WHERE plan.plan_id=NEW.plan_id AND plan.tenant_id=NEW.tenant_id
    AND plan.business_id=NEW.business_id AND actor.tenant_id=NEW.tenant_id
    AND actor.enabled=1
)
BEGIN SELECT RAISE(ABORT, 'legacy cutover event crosses scope'); END;
CREATE TRIGGER enforce_legacy_cutover_initial_event_insert
BEFORE INSERT ON legacy_cutover_events
WHEN (NEW.stage='inventoried' AND EXISTS (
        SELECT 1 FROM legacy_cutover_events WHERE plan_id=NEW.plan_id
      )) OR (NEW.stage!='inventoried' AND NOT EXISTS (
        SELECT 1 FROM legacy_cutover_events WHERE plan_id=NEW.plan_id
      ))
BEGIN SELECT RAISE(ABORT, 'legacy cutover initial stage is invalid'); END;
CREATE TRIGGER enforce_legacy_cutover_sequence_insert
BEFORE INSERT ON legacy_cutover_events
WHEN NEW.stage!='inventoried' AND NOT (
  (NEW.stage='shadow_compared' AND (SELECT stage FROM legacy_cutover_events
    WHERE plan_id=NEW.plan_id ORDER BY rowid DESC LIMIT 1)='inventoried') OR
  (NEW.stage='recovery_verified' AND (SELECT stage FROM legacy_cutover_events
    WHERE plan_id=NEW.plan_id ORDER BY rowid DESC LIMIT 1)='shadow_compared') OR
  (NEW.stage='approved' AND (SELECT stage FROM legacy_cutover_events
    WHERE plan_id=NEW.plan_id ORDER BY rowid DESC LIMIT 1)='recovery_verified') OR
  (NEW.stage='canary_observed' AND (SELECT stage FROM legacy_cutover_events
    WHERE plan_id=NEW.plan_id ORDER BY rowid DESC LIMIT 1)='approved') OR
  (NEW.stage='rolled_back' AND (SELECT stage FROM legacy_cutover_events
    WHERE plan_id=NEW.plan_id ORDER BY rowid DESC LIMIT 1) IN ('approved','canary_observed'))
)
BEGIN SELECT RAISE(ABORT, 'legacy cutover stage transition is invalid'); END;
CREATE TRIGGER enforce_legacy_cutover_qa_insert
BEFORE INSERT ON legacy_cutover_events
WHEN NEW.stage IN ('shadow_compared','recovery_verified') AND NOT EXISTS (
  SELECT 1 FROM actors actor, json_each(actor.roles_json) role
  WHERE actor.actor_id=NEW.actor_id AND role.value IN ('qa','verifier')
)
BEGIN SELECT RAISE(ABORT, 'legacy comparison and recovery require QA'); END;
CREATE TRIGGER enforce_legacy_cutover_human_approval_insert
BEFORE INSERT ON legacy_cutover_events
WHEN NEW.stage='approved' AND NOT EXISTS (
  SELECT 1 FROM actors actor, json_each(actor.roles_json) role
  WHERE actor.actor_id=NEW.actor_id AND actor.actor_type='human'
    AND role.value IN ('business-owner','operations')
)
BEGIN SELECT RAISE(ABORT, 'legacy cutover approval requires a scoped human owner'); END;
""" + "\n\n" + "\n".join(
    f"CREATE TRIGGER prevent_{table}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;"
    for table in (
        "production_qualifications", "legacy_cutover_plans", "legacy_cutover_events",
    )
    for operation in ("UPDATE", "DELETE")
)

SCHEMA_MIGRATIONS = (
    (
        2,
        "execution_truth_and_outcome_verification",
        EXECUTION_VERIFICATION_SCHEMA,
    ),
    (
        3,
        "foundation_trust_boundary_hardening",
        FOUNDATION_HARDENING_SCHEMA,
    ),
    (
        4,
        "durable_trust_attestation",
        DURABLE_TRUST_ATTESTATION_SCHEMA,
    ),
    (
        5,
        "authenticated_truth_and_serializable_boundaries",
        AUTHENTICATED_COMPLETION_SCHEMA,
    ),
    (
        6,
        "semantic_completion_and_paired_recovery",
        SEMANTIC_COMPLETION_SCHEMA,
    ),
    (
        7,
        "approval_lifecycle_and_emergency_stop",
        APPROVAL_AND_EMERGENCY_CONTROL_SCHEMA,
    ),
    (
        8,
        "forbid_external_financial_execution",
        FINANCIAL_EXECUTION_GUARD_SCHEMA,
    ),
    (
        9,
        "cumulative_spend_envelopes",
        SPEND_ENVELOPE_SCHEMA,
    ),
    (
        10,
        "governed_model_routing",
        MODEL_ROUTING_SCHEMA,
    ),
    (
        11,
        "real_model_shadow_runtime",
        SHADOW_MODEL_RUNTIME_SCHEMA,
    ),
    (
        12,
        "affiliate_marketing_shadow_loop",
        AFFILIATE_SHADOW_LOOP_SCHEMA,
    ),
    (
        13,
        "portfolio_capability_expansion",
        PORTFOLIO_CAPABILITY_EXPANSION_SCHEMA,
    ),
    (
        14,
        "production_and_resale_hardening",
        PRODUCTION_AND_RESALE_HARDENING_SCHEMA,
    ),
)
LATEST_SCHEMA_VERSION = SCHEMA_MIGRATIONS[-1][0]

_SCHEMA_MUTATION_ACTIONS = frozenset(
    action
    for action in (
        getattr(sqlite3, "SQLITE_ALTER_TABLE", None),
        getattr(sqlite3, "SQLITE_ATTACH", None),
        getattr(sqlite3, "SQLITE_CREATE_INDEX", None),
        getattr(sqlite3, "SQLITE_CREATE_TABLE", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_INDEX", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_TABLE", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_VIEW", None),
        getattr(sqlite3, "SQLITE_CREATE_TRIGGER", None),
        getattr(sqlite3, "SQLITE_CREATE_VIEW", None),
        getattr(sqlite3, "SQLITE_CREATE_VTABLE", None),
        getattr(sqlite3, "SQLITE_DETACH", None),
        getattr(sqlite3, "SQLITE_DROP_INDEX", None),
        getattr(sqlite3, "SQLITE_DROP_TABLE", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_INDEX", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_TABLE", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_VIEW", None),
        getattr(sqlite3, "SQLITE_DROP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_DROP_VIEW", None),
        getattr(sqlite3, "SQLITE_DROP_VTABLE", None),
        getattr(sqlite3, "SQLITE_REINDEX", None),
    )
    if action is not None
)


def _deny_schema_mutation(
    action_code: int,
    _parameter_1: str | None,
    _parameter_2: str | None,
    _database_name: str | None,
    _trigger_name: str | None,
) -> int:
    if action_code in _SCHEMA_MUTATION_ACTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _registered_migrations() -> dict[int, tuple[str, str]]:
    return {
        BASELINE_SCHEMA_VERSION: (
            BASELINE_MIGRATION_NAME,
            hashlib.sha256(SCHEMA.encode("utf-8")).hexdigest(),
        ),
        **{
            version: (
                name,
                hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
            for version, name, sql in SCHEMA_MIGRATIONS
        },
    }


def _normalize_schema_sql(sql: str | None) -> str:
    return " ".join((sql or "").split())


def _schema_manifest(
    connection: sqlite3.Connection,
) -> dict[tuple[str, str], tuple[str, str]]:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE type IN ('table', 'index', 'trigger')
          AND name NOT LIKE 'sqlite_%'
          AND sql IS NOT NULL
        ORDER BY type, name
        """
    ).fetchall()
    return {
        (row["type"], row["name"]): (
            row["tbl_name"],
            _normalize_schema_sql(row["sql"]),
        )
        for row in rows
    }


def _expected_schema_manifest(
    version: int,
) -> dict[tuple[str, str], tuple[str, str]]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.executescript(SCHEMA_MIGRATIONS_TABLE)
        connection.executescript(SCHEMA)
        for migration_version, _, sql in SCHEMA_MIGRATIONS:
            if migration_version > version:
                break
            connection.executescript(sql)
        return _schema_manifest(connection)
    finally:
        connection.close()


def _schema_attestation_errors(
    connection: sqlite3.Connection,
    version: int,
) -> list[str]:
    if version < BASELINE_SCHEMA_VERSION or version > LATEST_SCHEMA_VERSION:
        return [f"cannot attest unknown schema version {version}"]
    expected = _expected_schema_manifest(version)
    actual = _schema_manifest(connection)
    errors = []
    for identity in sorted(expected.keys() - actual.keys()):
        errors.append(f"schema object is missing: {identity[0]} {identity[1]}")
    for identity in sorted(actual.keys() - expected.keys()):
        errors.append(f"unexpected schema object: {identity[0]} {identity[1]}")
    for identity in sorted(expected.keys() & actual.keys()):
        if expected[identity] != actual[identity]:
            errors.append(
                f"schema object definition differs: {identity[0]} {identity[1]}"
            )
    return errors


def _execute_sql_script(
    connection: sqlite3.Connection,
    sql: str,
) -> None:
    statement = ""
    for line in sql.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            if statement.strip():
                connection.execute(statement)
            statement = ""
    if statement.strip():
        raise SchemaDriftError("migration SQL contains an incomplete statement")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def _decimal_to_string(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _amount_to_minor(value: Decimal) -> int:
    scaled = value * 100
    integral = scaled.to_integral_value()
    if value <= 0 or scaled != integral:
        raise ValueError(
            "spend amounts must be positive with at most two decimal places"
        )
    return int(integral)


def _event_fingerprint(event: Event) -> str:
    canonical = {
        "actor_id": event.actor_id,
        "business_id": event.business_id,
        "correlation_id": event.correlation_id,
        "idempotency_key": event.idempotency_key,
        "kind": event.kind,
        "occurred_at": _utc_iso(event.occurred_at),
        "payload": dict(event.payload),
        "source": event.source,
        "tenant_id": event.tenant_id,
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _approval_work_fingerprint(
    work: dict[str, Any] | sqlite3.Row,
) -> str:
    attributes = (
        json.loads(work["attributes_json"])
        if "attributes_json" in work.keys()
        else dict(work["attributes"])
    )
    canonical = {
        "account_id": work["account_id"],
        "action_type": work["action_type"],
        "amount": (
            str(work["amount"]) if work["amount"] is not None else None
        ),
        "assigned_actor_id": work["assigned_actor_id"],
        "attributes": attributes,
        "authority_mode": work["authority_mode"],
        "business_id": work["business_id"],
        "currency": work["currency"],
        "objective_id": work["objective_id"],
        "platform": work["platform"],
        "rationale": work["rationale"],
        "tenant_id": work["tenant_id"],
        "title": work["title"],
        "work_item_id": work["work_item_id"],
        "work_key": work["work_key"],
    }
    return hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _evidence_receipt_hash(receipt: dict[str, Any]) -> str:
    canonical = {
        "attempt_id": receipt["attempt_id"],
        "business_id": receipt["business_id"],
        "captured_by": receipt["captured_by"],
        "created_at": _utc_iso(receipt["created_at"]),
        "evidence_kind": receipt["evidence_kind"],
        "issuer_version": receipt["issuer_version"],
        "observed_at": _utc_iso(receipt["observed_at"]),
        "payload": receipt["payload"],
        "receipt_id": receipt["receipt_id"],
        "source_ref": receipt["source_ref"],
        "source_system": receipt["source_system"],
        "tenant_id": receipt["tenant_id"],
        "valid_until": _utc_iso(receipt["valid_until"]),
        "work_item_id": receipt["work_item_id"],
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _iso_text(value: datetime | str) -> str:
    return _utc_iso(value) if isinstance(value, datetime) else value


def _completion_attestation_payload(
    verification: dict[str, Any],
    attempt: dict[str, Any] | sqlite3.Row,
    receipts: list[dict[str, Any]],
    work: dict[str, Any] | sqlite3.Row,
    objective: dict[str, Any] | sqlite3.Row,
) -> bytes:
    attributes = (
        json.loads(work["attributes_json"])
        if "attributes_json" in work.keys()
        else dict(work["attributes"])
    )
    canonical = {
        "attempt": {
            "action_type": attempt["action_type"],
            "attempt_id": attempt["attempt_id"],
            "attempted_at": _iso_text(attempt["attempted_at"]),
            "business_id": attempt["business_id"],
            "execution_mode": attempt["execution_mode"],
            "idempotency_key": attempt["idempotency_key"],
            "observed_at": (
                _iso_text(attempt["observed_at"])
                if attempt["observed_at"] is not None
                else None
            ),
            "precondition_receipt_id": attempt["precondition_receipt_id"],
            "producer_id": attempt["producer_id"],
            "target_ref": attempt["target_ref"],
            "tenant_id": attempt["tenant_id"],
            "work_item_id": attempt["work_item_id"],
        },
        "objective_scope": {
            "business_id": objective["business_id"],
            "objective_id": objective["objective_id"],
            "tenant_id": objective["tenant_id"],
        },
        "receipts": sorted(
            (
                {
                    "content_hash": receipt["content_hash"],
                    "receipt_id": receipt["receipt_id"],
                }
                for receipt in receipts
            ),
            key=lambda item: item["receipt_id"],
        ),
        "verification": {
            "attempt_id": verification["attempt_id"],
            "business_id": verification["business_id"],
            "decided_at": _iso_text(verification["decided_at"]),
            "decision": verification["decision"],
            "evidence_receipt_ids": list(
                verification["evidence_receipt_ids"]
            ),
            "expected_facts": dict(verification["expected_facts"]),
            "policy_version": verification["policy_version"],
            "rationale": verification["rationale"],
            "tenant_id": verification["tenant_id"],
            "verification_id": verification["verification_id"],
            "verifier_id": verification["verifier_id"],
            "work_item_id": verification["work_item_id"],
        },
        "version": 2,
        "work": {
            "account_id": work["account_id"],
            "action_type": work["action_type"],
            "amount": work["amount"],
            "assigned_actor_id": work["assigned_actor_id"],
            "attributes": attributes,
            "authority_mode": work["authority_mode"],
            "business_id": work["business_id"],
            "currency": work["currency"],
            "max_attempts": work["max_attempts"],
            "objective_id": work["objective_id"],
            "platform": work["platform"],
            "rationale": work["rationale"],
            "tenant_id": work["tenant_id"],
            "title": work["title"],
            "work_item_id": work["work_item_id"],
            "work_key": work["work_key"],
        },
    }
    return json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class EventReceipt:
    event_id: str
    inserted: bool


@dataclass(frozen=True, slots=True)
class EventProcessingClaim:
    event_id: str
    inserted: bool
    claimed: bool


class EventIdentityConflict(ValueError):
    """An event ID was reused outside its original identity boundary."""


class EventProcessingInProgress(RuntimeError):
    """An exact replay arrived while its original event is still processing."""


class SchemaDriftError(RuntimeError):
    """An applied migration no longer matches its registered definition."""


class UnledgeredDatabaseError(SchemaDriftError):
    """A non-empty database has no trustworthy migration history."""


class MigrationRequiredError(SchemaDriftError):
    """Existing state requires an explicit backup-first migration."""


class TrustKeyError(SchemaDriftError):
    """The external durable-truth signing key is missing or invalid."""


@dataclass(frozen=True, slots=True)
class ObjectiveRecord:
    objective: Objective
    next_review_at: datetime


class SQLiteStore:
    """Durable local store with one connection per operation."""

    def __init__(
        self,
        path: str | Path,
        *,
        truth_key_path: str | Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.truth_key_path = (
            Path(truth_key_path)
            if truth_key_path is not None
            else self.path.with_name(f"{self.path.name}.truth-key")
        )

    def _load_truth_key(self) -> bytes:
        try:
            mode = stat.S_IMODE(self.truth_key_path.stat().st_mode)
            if mode & 0o077:
                raise TrustKeyError(
                    f"durable-truth key permissions are too broad: "
                    f"{self.truth_key_path}"
                )
            encoded = self.truth_key_path.read_text(encoding="ascii").strip()
            key = bytes.fromhex(encoded)
        except TrustKeyError:
            raise
        except (FileNotFoundError, OSError, UnicodeError, ValueError) as error:
            raise TrustKeyError(
                f"durable-truth key is missing or invalid: "
                f"{self.truth_key_path}"
            ) from error
        if len(key) != 32:
            raise TrustKeyError(
                f"durable-truth key has an invalid length: "
                f"{self.truth_key_path}"
            )
        return key

    def _ensure_truth_key(self) -> bytes:
        try:
            return self._load_truth_key()
        except TrustKeyError:
            pass
        self.truth_key_path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        try:
            descriptor = os.open(
                self.truth_key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return self._load_truth_key()
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(key.hex())
                stream.write("\n")
        except Exception:
            try:
                self.truth_key_path.unlink()
            except FileNotFoundError:
                pass
            raise
        return key

    def _completion_signature(
        self,
        verification: dict[str, Any],
        attempt: dict[str, Any] | sqlite3.Row,
        receipts: list[dict[str, Any]],
        work: dict[str, Any] | sqlite3.Row,
        objective: dict[str, Any] | sqlite3.Row,
    ) -> tuple[str, str]:
        key = self._load_truth_key()
        key_id = hashlib.sha256(key).hexdigest()
        signature = hmac.new(
            key,
            _completion_attestation_payload(
                verification,
                attempt,
                receipts,
                work,
                objective,
            ),
            hashlib.sha256,
        ).hexdigest()
        return key_id, signature

    def _completion_context(
        self,
        connection: sqlite3.Connection,
        work_item_id: str,
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        work = connection.execute(
            """
            SELECT *
            FROM work_items
            WHERE work_item_id = ?
            """,
            (work_item_id,),
        ).fetchone()
        if work is None:
            raise SchemaDriftError(
                "completion attestation references missing work"
            )
        objective = connection.execute(
            """
            SELECT objective_id, tenant_id, business_id
            FROM objectives
            WHERE objective_id = ?
            """,
            (work["objective_id"],),
        ).fetchone()
        if (
            objective is None
            or objective["tenant_id"] != work["tenant_id"]
            or objective["business_id"] != work["business_id"]
        ):
            raise SchemaDriftError(
                "completion work no longer matches its objective scope"
            )
        return work, objective

    def _connect(
        self,
        *,
        allow_schema_changes: bool = False,
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if not allow_schema_changes:
            connection.set_authorizer(_deny_schema_mutation)
        return connection

    @contextmanager
    def _connection(
        self,
        *,
        allow_schema_changes: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        connection = self._connect(
            allow_schema_changes=allow_schema_changes
        )
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def _immediate_connection(
        self,
        *,
        allow_schema_changes: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        connection = self._connect(
            allow_schema_changes=allow_schema_changes
        )
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create new state or attest already-current state without migrating."""
        self._initialize(allow_existing_migrations=False)

    def migrate(self, destination: str | Path) -> Path:
        """Back up and explicitly migrate valid existing state."""
        if not self.path.exists():
            raise FileNotFoundError(
                "database does not exist; initialize new state instead"
            )
        with self._immediate_connection(
            allow_schema_changes=True
        ) as connection:
            status = self.schema_status()
            if not status["migration_valid"]:
                raise SchemaDriftError(
                    "database schema or migration ledger is invalid; "
                    "refusing migration"
                )
            if status["current_version"] >= LATEST_SCHEMA_VERSION:
                raise MigrationRequiredError("database is already current")
            backup_path = self.create_backup(destination)
            self._apply_pending_migrations_locked(
                connection,
                current_version=status["current_version"],
            )
        os.chmod(self.path, 0o600)
        return backup_path

    def _apply_pending_migrations_locked(
        self,
        connection: sqlite3.Connection,
        *,
        current_version: int,
    ) -> None:
        for version, name, sql in SCHEMA_MIGRATIONS:
            if version <= current_version:
                continue
            if version == 3:
                self._assert_tenant_business_integrity(connection)
            if version == 4:
                self._assert_parent_scope_integrity(connection)
            if version == 5:
                self._assert_authenticated_completion_migration_safe(
                    connection
                )
                self._ensure_truth_key()
            if version == 6:
                self._assert_semantic_completion_migration_safe(connection)
            _execute_sql_script(connection, sql)
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
                    _utc_now(),
                ),
            )
        final_errors = _schema_attestation_errors(
            connection,
            LATEST_SCHEMA_VERSION,
        )
        if final_errors:
            raise SchemaDriftError("; ".join(final_errors))
        self._load_truth_key()
        self._assert_tenant_business_integrity(connection)
        self._assert_parent_scope_integrity(connection)
        self._assert_spend_integrity(connection)
        self._assert_routing_integrity(connection)
        self._assert_shadow_runtime_integrity(connection)
        self._assert_affiliate_shadow_integrity(connection)
        self._assert_portfolio_integrity(connection)
        self._assert_production_integrity(connection)
        self._assert_completion_attestation_integrity(connection)

    def _initialize(self, *, allow_existing_migrations: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection(allow_schema_changes=True) as connection:
            existing_tables = {
                row["name"]
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    """
                ).fetchall()
            }
            existing_state = bool(existing_tables)
            if existing_tables and "schema_migrations" not in existing_tables:
                raise UnledgeredDatabaseError(
                    "refusing to stamp a non-empty unledgered database; "
                    "import it into newly initialized state"
                )
            connection.executescript(SCHEMA_MIGRATIONS_TABLE)
            checksum = hashlib.sha256(SCHEMA.encode("utf-8")).hexdigest()
            applied = connection.execute(
                """
                SELECT name, checksum
                FROM schema_migrations
                WHERE version = ?
                """,
                (BASELINE_SCHEMA_VERSION,),
            ).fetchone()
            if applied is not None:
                if (
                    applied["name"] != BASELINE_MIGRATION_NAME
                    or applied["checksum"] != checksum
                ):
                    raise SchemaDriftError(
                        "the applied baseline schema does not match the "
                        "registered migration"
                    )
            else:
                if existing_state:
                    raise UnledgeredDatabaseError(
                        "migration ledger has no baseline entry for existing "
                        "state"
                    )
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
                        checksum,
                        _utc_now(),
                    ),
                )

            ledger_rows = connection.execute(
                """
                SELECT version, name, checksum
                FROM schema_migrations
                ORDER BY version
                """
            ).fetchall()
            observed_versions = [row["version"] for row in ledger_rows]
            if observed_versions != list(
                range(1, max(observed_versions, default=0) + 1)
            ):
                raise SchemaDriftError(
                    "migration ledger contains a version gap"
                )
            registered = _registered_migrations()
            for row in ledger_rows:
                definition = registered.get(row["version"])
                if definition is None:
                    raise SchemaDriftError(
                        f"unknown migration version {row['version']}"
                    )
                if (row["name"], row["checksum"]) != definition:
                    raise SchemaDriftError(
                        f"applied migration {row['version']} does not match "
                        "the registered definition"
                    )
            current_version = max(observed_versions, default=0)
            attestation_errors = _schema_attestation_errors(
                connection,
                current_version,
            )
            if attestation_errors:
                raise SchemaDriftError("; ".join(attestation_errors))
            if (
                existing_state
                and current_version < LATEST_SCHEMA_VERSION
                and not allow_existing_migrations
            ):
                raise MigrationRequiredError(
                    f"schema version {current_version} requires explicit "
                    "backup-first migration"
                )

            for version, name, sql in SCHEMA_MIGRATIONS:
                migration_checksum = hashlib.sha256(
                    sql.encode("utf-8")
                ).hexdigest()
                migration = connection.execute(
                    """
                    SELECT name, checksum
                    FROM schema_migrations
                    WHERE version = ?
                    """,
                    (version,),
                ).fetchone()
                if migration is not None:
                    if (
                        migration["name"] != name
                        or migration["checksum"] != migration_checksum
                    ):
                        raise SchemaDriftError(
                            f"applied migration {version} does not match "
                            "the registered definition"
                        )
                    continue
                if version == 3:
                    self._assert_tenant_business_integrity(connection)
                if version == 4:
                    self._assert_parent_scope_integrity(connection)
                if version == 5:
                    self._assert_authenticated_completion_migration_safe(
                        connection
                    )
                    self._ensure_truth_key()
                if version == 6:
                    self._assert_semantic_completion_migration_safe(connection)
                applied_at = _utc_now()
                quoted_name = name.replace("'", "''")
                quoted_checksum = migration_checksum.replace("'", "''")
                quoted_applied_at = applied_at.replace("'", "''")
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    f"{sql}\n"
                    "INSERT INTO schema_migrations("
                    "version, name, checksum, applied_at"
                    ") VALUES ("
                    f"{version}, '{quoted_name}', '{quoted_checksum}', "
                    f"'{quoted_applied_at}'"
                    ");\n"
                    "COMMIT;"
                )
            final_errors = _schema_attestation_errors(
                connection,
                LATEST_SCHEMA_VERSION,
            )
            if final_errors:
                raise SchemaDriftError("; ".join(final_errors))
            if existing_state:
                self._load_truth_key()
            else:
                self._ensure_truth_key()
            self._assert_tenant_business_integrity(connection)
            self._assert_parent_scope_integrity(connection)
            self._assert_spend_integrity(connection)
            self._assert_routing_integrity(connection)
            self._assert_shadow_runtime_integrity(connection)
            self._assert_affiliate_shadow_integrity(connection)
            self._assert_portfolio_integrity(connection)
            self._assert_production_integrity(connection)
            self._assert_completion_attestation_integrity(connection)
        os.chmod(self.path, 0o600)

    def _assert_tenant_business_integrity(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            ).fetchall()
        }
        for table in (
            *_TENANT_BUSINESS_SCOPED_TABLES,
            *_GOAL9_TENANT_BUSINESS_SCOPED_TABLES,
            *_GOAL10_TENANT_BUSINESS_SCOPED_TABLES,
            *_GOAL11_TENANT_BUSINESS_SCOPED_TABLES,
            *_GOAL12_TENANT_BUSINESS_SCOPED_TABLES,
            *_GOAL13_TENANT_BUSINESS_SCOPED_TABLES,
            *_GOAL14_TENANT_BUSINESS_SCOPED_TABLES,
        ):
            if table not in existing_tables:
                continue
            mismatch = connection.execute(
                f"""
                SELECT 1
                FROM {table} AS scoped
                LEFT JOIN businesses AS business
                  ON business.business_id = scoped.business_id
                 AND business.tenant_id = scoped.tenant_id
                WHERE business.business_id IS NULL
                LIMIT 1
                """
            ).fetchone()
            if mismatch is not None:
                raise SchemaDriftError(
                    f"{table} contains a tenant/business ownership mismatch"
                )
        actor_membership_mismatch = connection.execute(
            """
            SELECT 1
            FROM actors AS actor,
                 json_each(actor.business_ids_json) AS membership
            LEFT JOIN businesses AS business
              ON business.business_id = membership.value
             AND business.tenant_id = actor.tenant_id
            WHERE business.business_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if actor_membership_mismatch is not None:
            raise SchemaDriftError(
                "actors contain cross-tenant business membership"
            )
        actor_scoped_columns = {
            **_ACTOR_SCOPED_COLUMNS,
            **_GOAL9_ACTOR_SCOPED_COLUMNS,
        }
        for table, actor_column in actor_scoped_columns.items():
            if table not in existing_tables:
                continue
            actor_mismatch = connection.execute(
                f"""
                SELECT 1
                FROM {table} AS scoped
                LEFT JOIN actors AS actor
                  ON actor.actor_id = scoped.{actor_column}
                 AND actor.tenant_id = scoped.tenant_id
                LEFT JOIN json_each(actor.business_ids_json) AS membership
                  ON membership.value = scoped.business_id
                WHERE actor.actor_id IS NULL OR membership.value IS NULL
                LIMIT 1
                """
            ).fetchone()
            if actor_mismatch is not None:
                raise SchemaDriftError(
                    f"{table} contains an actor identity scope mismatch"
                )

    def _assert_parent_scope_integrity(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        for child, child_column, parent, parent_column in _PARENT_SCOPE_LINKS:
            mismatch = connection.execute(
                f"""
                SELECT 1
                FROM {child} AS child
                LEFT JOIN {parent} AS parent
                  ON parent.{parent_column} = child.{child_column}
                 AND parent.tenant_id = child.tenant_id
                 AND parent.business_id = child.business_id
                WHERE child.{child_column} IS NOT NULL
                  AND parent.{parent_column} IS NULL
                LIMIT 1
                """
            ).fetchone()
            if mismatch is not None:
                raise SchemaDriftError(
                    f"{child}.{child_column} crosses its parent identity scope"
                )
        existing_tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        goal9_links = (
            (
                "approval_requests",
                "work_item_id",
                "work_items",
                "work_item_id",
            ),
            (
                "approval_events",
                "approval_id",
                "approval_requests",
                "approval_id",
            ),
            (
                "spend_commitments",
                "envelope_id",
                "spend_envelopes",
                "envelope_id",
            ),
            (
                "spend_commitments",
                "work_item_id",
                "work_items",
                "work_item_id",
            ),
        )
        for child, child_column, parent, parent_column in goal9_links:
            if child not in existing_tables or parent not in existing_tables:
                continue
            mismatch = connection.execute(
                f"""
                SELECT 1
                FROM {child} AS child
                LEFT JOIN {parent} AS parent
                  ON parent.{parent_column} = child.{child_column}
                 AND parent.tenant_id = child.tenant_id
                 AND parent.business_id = child.business_id
                WHERE parent.{parent_column} IS NULL
                LIMIT 1
                """
            ).fetchone()
            if mismatch is not None:
                raise SchemaDriftError(
                    f"{child}.{child_column} crosses its parent identity scope"
                )
        verification_mismatch = connection.execute(
            """
            SELECT 1
            FROM outcome_verifications AS verification
            LEFT JOIN execution_attempts AS attempt
              ON attempt.attempt_id = verification.attempt_id
             AND attempt.work_item_id = verification.work_item_id
             AND attempt.tenant_id = verification.tenant_id
             AND attempt.business_id = verification.business_id
            WHERE attempt.attempt_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if verification_mismatch is not None:
            raise SchemaDriftError(
                "outcome verification does not match its attempted work"
            )
        receipt_mismatch = connection.execute(
            """
            SELECT 1
            FROM evidence_receipts AS receipt
            LEFT JOIN execution_attempts AS attempt
              ON attempt.attempt_id = receipt.attempt_id
             AND attempt.work_item_id = receipt.work_item_id
             AND attempt.tenant_id = receipt.tenant_id
             AND attempt.business_id = receipt.business_id
            WHERE receipt.attempt_id IS NOT NULL
              AND attempt.attempt_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if receipt_mismatch is not None:
            raise SchemaDriftError(
                "evidence receipt does not match its attempted work"
            )
        precondition_mismatch = connection.execute(
            """
            SELECT 1
            FROM execution_attempts AS attempt
            LEFT JOIN evidence_receipts AS receipt
              ON receipt.receipt_id = attempt.precondition_receipt_id
             AND receipt.work_item_id = attempt.work_item_id
             AND receipt.tenant_id = attempt.tenant_id
             AND receipt.business_id = attempt.business_id
             AND receipt.evidence_kind = 'precondition'
             AND receipt.attempt_id IS NULL
            WHERE attempt.precondition_receipt_id IS NOT NULL
              AND receipt.receipt_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if precondition_mismatch is not None:
            raise SchemaDriftError(
                "execution attempt has a mismatched precondition receipt"
            )

    def _assert_authenticated_completion_migration_safe(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        prior_truth = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM outcome_verifications)
              + (SELECT COUNT(*) FROM execution_attempts
                 WHERE status IN ('verified', 'disproved'))
              + (SELECT COUNT(*) FROM work_items
                 WHERE status IN ('verified', 'disproved'))
            """
        ).fetchone()[0]
        if prior_truth:
            raise SchemaDriftError(
                "existing terminal truth requires independent re-verification "
                "before authenticated-attestation migration"
            )

    def _assert_semantic_completion_migration_safe(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        prior_truth = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM completion_attestations)
              + (SELECT COUNT(*) FROM outcome_verifications)
              + (SELECT COUNT(*) FROM execution_attempts
                 WHERE status IN ('verified', 'disproved'))
              + (SELECT COUNT(*) FROM work_items
                 WHERE status IN ('verified', 'disproved'))
            """
        ).fetchone()[0]
        if prior_truth:
            raise SchemaDriftError(
                "existing terminal truth requires independent re-verification "
                "before semantic-attestation migration"
            )

    def _assert_routing_integrity(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "routing_decisions" not in existing_tables:
            return
        for version in connection.execute(
            """
            SELECT catalog_version, content_hash
            FROM model_catalog_versions
            """
        ).fetchall():
            entries = []
            for entry in connection.execute(
                """
                SELECT * FROM model_catalog_entries
                WHERE catalog_version = ?
                ORDER BY model_id
                """,
                (version["catalog_version"],),
            ).fetchall():
                entries.append(
                    {
                        "allowed_data_classes": sorted(
                            json.loads(entry["allowed_data_classes_json"])
                        ),
                        "context_window_tokens": entry[
                            "context_window_tokens"
                        ],
                        "enabled": bool(entry["enabled"]),
                        "evaluation_version": entry["evaluation_version"],
                        "input_micros_per_million": entry[
                            "input_micros_per_million"
                        ],
                        "modalities": sorted(
                            json.loads(entry["modalities_json"])
                        ),
                        "model_id": entry["model_id"],
                        "output_micros_per_million": entry[
                            "output_micros_per_million"
                        ],
                        "provider_id": entry["provider_id"],
                        "provider_model_ref": entry["provider_model_ref"],
                        "quality_score": entry["quality_score"],
                        "reasoning_tier": entry["reasoning_tier"],
                        "structured_output": bool(
                            entry["structured_output"]
                        ),
                        "tool_use": bool(entry["tool_use"]),
                    }
                )
            canonical = json.dumps(
                entries,
                sort_keys=True,
                separators=(",", ":"),
            )
            observed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(observed, version["content_hash"]):
                raise SchemaDriftError(
                    "model catalog content hash does not match"
                )
        invalid_decision = connection.execute(
            """
            SELECT 1
            FROM routing_decisions AS decision
            LEFT JOIN model_catalog_activation_events AS activation
              ON activation.catalog_version = decision.catalog_version
             AND activation.activated_at <= decision.created_at
            LEFT JOIN model_catalog_entries AS entry
              ON entry.catalog_version = decision.catalog_version
             AND entry.model_id = decision.model_id
             AND entry.provider_id = decision.provider_id
            LEFT JOIN provider_credentials AS credential
              ON credential.credential_id = decision.credential_id
             AND credential.tenant_id = decision.tenant_id
             AND credential.business_id = decision.business_id
             AND credential.provider_id = decision.provider_id
            LEFT JOIN provider_policy_revisions AS policy
              ON policy.policy_revision_id = decision.policy_revision_id
             AND policy.credential_id = decision.credential_id
             AND policy.tenant_id = decision.tenant_id
             AND policy.business_id = decision.business_id
             AND policy.provider_id = decision.provider_id
            WHERE activation.activation_id IS NULL
               OR (
                    decision.status = 'selected'
                    AND (
                        entry.model_id IS NULL
                        OR entry.enabled != 1
                        OR credential.credential_id IS NULL
                        OR policy.policy_revision_id IS NULL
                    )
               )
            LIMIT 1
            """
        ).fetchone()
        if invalid_decision is not None:
            raise SchemaDriftError(
                "routing decision lacks activated catalog or scoped policy"
            )
        for row in connection.execute(
            "SELECT rowid AS decision_rowid, * FROM routing_decisions"
        ).fetchall():
            canonical = json.dumps(
                json.loads(row["request_json"]),
                sort_keys=True,
                separators=(",", ":"),
            )
            if canonical != row["request_json"]:
                raise SchemaDriftError(
                    "routing decision request is not canonically encoded"
                )
            observed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(observed, row["request_hash"]):
                raise SchemaDriftError(
                    "routing decision request hash does not match"
                )
            if row["status"] != "selected":
                continue
            request = json.loads(canonical)
            entry = connection.execute(
                """
                SELECT * FROM model_catalog_entries
                WHERE catalog_version = ? AND model_id = ?
                  AND provider_id = ?
                """,
                (
                    row["catalog_version"],
                    row["model_id"],
                    row["provider_id"],
                ),
            ).fetchone()
            policy = connection.execute(
                """
                SELECT * FROM provider_policy_revisions
                WHERE policy_revision_id = ?
                """,
                (row["policy_revision_id"],),
            ).fetchone()
            if entry is None or policy is None:
                raise SchemaDriftError(
                    "selected route lacks catalog or policy evidence"
                )
            reasoning_rank = {
                "utility": 0,
                "standard": 1,
                "advanced": 2,
            }
            incompatible = (
                reasoning_rank[entry["reasoning_tier"]]
                < reasoning_rank[request["reasoning_tier"]]
                or (
                    request["requires_tool_use"]
                    and not entry["tool_use"]
                )
                or (
                    request["requires_structured_output"]
                    and not entry["structured_output"]
                )
                or not set(request["required_modalities"]) <= set(
                    json.loads(entry["modalities_json"])
                )
                or request["required_context_tokens"]
                > entry["context_window_tokens"]
                or request["data_class"]
                not in json.loads(entry["allowed_data_classes_json"])
                or request["data_class"]
                not in json.loads(policy["allowed_data_classes_json"])
                or (
                    json.loads(policy["allowed_model_ids_json"])
                    and row["model_id"]
                    not in json.loads(policy["allowed_model_ids_json"])
                )
                or (
                    request["independent_from_provider_id"] is not None
                    and row["provider_id"]
                    == request["independent_from_provider_id"]
                )
                or row["model_id"] in request["excluded_model_ids"]
                or not policy["enabled"]
            )
            expected_cost = (
                (
                    request["estimated_input_tokens"]
                    * entry["input_micros_per_million"]
                    + 999999
                )
                // 1000000
                + (
                    request["estimated_output_tokens"]
                    * entry["output_micros_per_million"]
                    + 999999
                )
                // 1000000
            )
            if (
                incompatible
                or row["estimated_cost_micros"] != expected_cost
                or (
                    request["max_cost_micros"] is not None
                    and expected_cost > request["max_cost_micros"]
                )
            ):
                raise SchemaDriftError(
                    "selected route is incompatible with its bound request"
                )
            decision_time = datetime.fromisoformat(row["created_at"])
            month_start = decision_time.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            prior_cost = connection.execute(
                """
                SELECT COALESCE(SUM(
                    CASE
                        WHEN usage.created_at <= :decision_time
                            THEN usage.cost_micros
                        ELSE prior.estimated_cost_micros
                    END
                ), 0)
                FROM routing_decisions AS prior
                LEFT JOIN model_usage_records AS usage
                  ON usage.decision_id = prior.decision_id
                WHERE prior.tenant_id = :tenant_id
                  AND prior.business_id = :business_id
                  AND prior.provider_id = :provider_id
                  AND prior.status = 'selected'
                  AND prior.created_at >= :month_start
                  AND (
                      prior.created_at < :decision_time
                      OR (
                          prior.created_at = :decision_time
                          AND prior.rowid < :decision_rowid
                      )
                  )
                """,
                {
                    "tenant_id": row["tenant_id"],
                    "business_id": row["business_id"],
                    "provider_id": row["provider_id"],
                    "month_start": month_start,
                    "decision_time": row["created_at"],
                    "decision_rowid": row["decision_rowid"],
                },
            ).fetchone()[0]
            if (
                prior_cost + expected_cost
                > policy["monthly_budget_micros"]
            ):
                raise SchemaDriftError(
                    "selected route exceeds its provider budget"
                )
        invalid_usage = connection.execute(
            """
            SELECT 1
            FROM model_usage_records AS usage
            LEFT JOIN routing_decisions AS decision
              ON decision.decision_id = usage.decision_id
             AND decision.tenant_id = usage.tenant_id
             AND decision.business_id = usage.business_id
             AND decision.provider_id = usage.provider_id
             AND decision.model_id = usage.model_id
             AND decision.credential_id = usage.credential_id
            LEFT JOIN model_catalog_entries AS entry
              ON entry.catalog_version = decision.catalog_version
             AND entry.model_id = usage.model_id
             AND entry.provider_id = usage.provider_id
            WHERE decision.decision_id IS NULL
               OR entry.model_id IS NULL
               OR usage.cost_micros != (
                    (
                        usage.input_tokens
                        * entry.input_micros_per_million
                        + 999999
                    ) / 1000000
                    +
                    (
                        usage.output_tokens
                        * entry.output_micros_per_million
                        + 999999
                    ) / 1000000
               )
            LIMIT 1
            """
        ).fetchone()
        if invalid_usage is not None:
            raise SchemaDriftError(
                "model usage cost or routing identity is invalid"
            )
        invalid_health = connection.execute(
            """
            SELECT 1
            FROM model_health_events AS health
            LEFT JOIN model_usage_records AS usage
              ON usage.decision_id = health.decision_id
             AND usage.tenant_id = health.tenant_id
             AND usage.business_id = health.business_id
             AND usage.provider_id = health.provider_id
             AND usage.model_id = health.model_id
             AND usage.outcome = health.outcome
             AND usage.created_at = health.observed_at
            WHERE usage.usage_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if invalid_health is not None:
            raise SchemaDriftError(
                "model health event lacks matching usage evidence"
            )
        for circuit in connection.execute(
            "SELECT * FROM model_circuit_states"
        ).fetchall():
            state = "closed"
            failures = 0
            open_until = None
            latest_observed = None
            events = connection.execute(
                """
                SELECT health.*, decision.is_circuit_probe
                FROM model_health_events AS health
                JOIN routing_decisions AS decision
                  ON decision.decision_id = health.decision_id
                WHERE health.tenant_id = ? AND health.business_id = ?
                  AND health.provider_id = ? AND health.model_id = ?
                ORDER BY health.observed_at, health.rowid
                """,
                (
                    circuit["tenant_id"],
                    circuit["business_id"],
                    circuit["provider_id"],
                    circuit["model_id"],
                ),
            ).fetchall()
            if not events:
                raise SchemaDriftError(
                    "model circuit lacks append-only health evidence"
                )
            for event in events:
                latest_observed = event["observed_at"]
                if event["outcome"] == "success":
                    state = "closed"
                    failures = 0
                    open_until = None
                else:
                    failures += 1
                    must_open = (
                        event["outcome"] == "auth_error"
                        or bool(event["is_circuit_probe"])
                        or failures >= 3
                    )
                    state = "open" if must_open else "closed"
                    open_until = (
                        (
                            datetime.fromisoformat(event["observed_at"])
                            + timedelta(minutes=5)
                        ).isoformat()
                        if must_open
                        else None
                    )
                if (
                    event["circuit_state"] != state
                    or event["consecutive_failures"] != failures
                    or event["open_until"] != open_until
                ):
                    raise SchemaDriftError(
                        "model health event has an invalid circuit transition"
                    )
            outstanding_probe = connection.execute(
                """
                SELECT 1
                FROM routing_decisions AS decision
                LEFT JOIN model_usage_records AS usage
                  ON usage.decision_id = decision.decision_id
                WHERE decision.tenant_id = ? AND decision.business_id = ?
                  AND decision.provider_id = ? AND decision.model_id = ?
                  AND decision.is_circuit_probe = 1
                  AND decision.created_at >= ?
                  AND usage.usage_id IS NULL
                LIMIT 1
                """,
                (
                    circuit["tenant_id"],
                    circuit["business_id"],
                    circuit["provider_id"],
                    circuit["model_id"],
                    latest_observed,
                ),
            ).fetchone()
            if outstanding_probe is not None:
                state = "half_open"
                probe_in_flight = 1
            else:
                probe_in_flight = 0
            if (
                circuit["circuit_state"] != state
                or circuit["consecutive_failures"] != failures
                or circuit["open_until"] != open_until
                or circuit["probe_in_flight"] != probe_in_flight
            ):
                raise SchemaDriftError(
                    "model circuit state does not match health evidence"
                )

    def _assert_shadow_runtime_integrity(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        existing = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'shadow_model_attempts'
            """
        ).fetchone()
        if existing is None:
            return
        invalid_attempt = connection.execute(
            """
            SELECT 1
            FROM shadow_model_attempts AS attempt
            LEFT JOIN routing_decisions AS decision
              ON decision.decision_id = attempt.decision_id
             AND decision.tenant_id = attempt.tenant_id
             AND decision.business_id = attempt.business_id
             AND decision.provider_id = attempt.provider_id
             AND decision.model_id = attempt.model_id
             AND decision.credential_id = attempt.credential_id
             AND decision.status = 'selected'
            WHERE decision.decision_id IS NULL
               OR attempt.created_at < decision.created_at
               OR (
                    attempt.attempt_kind = 'canary'
                    AND json_extract(
                        decision.request_json, '$.data_class'
                    ) != 'public'
               )
            LIMIT 1
            """
        ).fetchone()
        if invalid_attempt is not None:
            raise SchemaDriftError(
                "shadow attempt does not match its selected route"
            )
        invalid_outcome = connection.execute(
            """
            SELECT 1
            FROM shadow_model_outcomes AS outcome
            LEFT JOIN shadow_model_attempts AS attempt
              ON attempt.attempt_id = outcome.attempt_id
             AND attempt.decision_id = outcome.decision_id
             AND attempt.tenant_id = outcome.tenant_id
             AND attempt.business_id = outcome.business_id
             AND attempt.provider_id = outcome.provider_id
             AND attempt.model_id = outcome.model_id
            LEFT JOIN model_usage_records AS usage
              ON usage.decision_id = outcome.decision_id
             AND usage.tenant_id = outcome.tenant_id
             AND usage.business_id = outcome.business_id
             AND usage.provider_id = outcome.provider_id
             AND usage.model_id = outcome.model_id
             AND usage.outcome = outcome.provider_outcome
            WHERE attempt.attempt_id IS NULL
               OR usage.usage_id IS NULL
               OR usage.created_at > outcome.created_at
            LIMIT 1
            """
        ).fetchone()
        if invalid_outcome is not None:
            raise SchemaDriftError(
                "shadow outcome lacks matching attempt and usage evidence"
            )
        unbound_usage = connection.execute(
            """
            SELECT 1
            FROM shadow_model_attempts AS attempt
            JOIN model_usage_records AS usage
              ON usage.decision_id = attempt.decision_id
            LEFT JOIN shadow_model_outcomes AS outcome
              ON outcome.attempt_id = attempt.attempt_id
            WHERE outcome.outcome_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if unbound_usage is not None:
            raise SchemaDriftError(
                "shadow usage lacks terminal outcome evidence"
            )
        invalid_replay = connection.execute(
            """
            SELECT 1 FROM model_evaluation_replays
            WHERE passed != (passed_count = case_count)
            LIMIT 1
            """
        ).fetchone()
        if invalid_replay is not None:
            raise SchemaDriftError(
                "evaluation replay pass state does not match case counts"
            )

    def _assert_affiliate_shadow_integrity(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        existing = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='affiliate_shadow_runs'"
        ).fetchone()
        if existing is None:
            return
        invalid_content = connection.execute(
            """
            SELECT 1 FROM affiliate_content_proposals p
            LEFT JOIN affiliate_recommendations r
              ON r.recommendation_id=p.recommendation_id AND r.run_id=p.run_id
             AND r.tenant_id=p.tenant_id AND r.business_id=p.business_id
            LEFT JOIN affiliate_offer_snapshots o ON o.snapshot_id=r.selected_snapshot_id
            LEFT JOIN shadow_model_attempts a
              ON a.attempt_id=p.shadow_attempt_id AND a.tenant_id=p.tenant_id
             AND a.business_id=p.business_id
            LEFT JOIN shadow_model_outcomes z
              ON z.attempt_id=a.attempt_id AND z.status='succeeded'
             AND z.output_hash=p.content_hash
            WHERE r.status!='selected' OR o.snapshot_id IS NULL
               OR o.run_id!=p.run_id OR o.tenant_id!=p.tenant_id
               OR o.business_id!=p.business_id
               OR p.destination_url!=o.destination_url OR p.channel!=o.channel
               OR p.disclosure!=o.disclosure_required OR z.outcome_id IS NULL
               OR json_type(p.claims_json)!='array'
               OR EXISTS (
                 SELECT 1 FROM json_each(p.claims_json) claim
                 WHERE claim.value NOT IN (
                   SELECT value FROM json_each(o.approved_claims_json)
                 )
               )
            LIMIT 1
            """
        ).fetchone()
        if invalid_content is not None:
            raise SchemaDriftError("affiliate content lacks selected offer and Goal 11 evidence")
        invalid_chain = connection.execute(
            """
            SELECT 1 FROM affiliate_experiments experiment
            LEFT JOIN affiliate_content_proposals proposal
              ON proposal.proposal_id=experiment.proposal_id
             AND proposal.run_id=experiment.run_id
             AND proposal.tenant_id=experiment.tenant_id
             AND proposal.business_id=experiment.business_id
            WHERE proposal.proposal_id IS NULL OR experiment.mode!='historical_replay'
               OR experiment.status!='shadow' OR experiment.window_end>experiment.created_at
            LIMIT 1
            """
        ).fetchone()
        if invalid_chain is not None:
            raise SchemaDriftError("affiliate experiment is not same-scope historical replay")
        invalid_conversion = connection.execute(
            """
            SELECT 1 FROM affiliate_observations conversion
            LEFT JOIN affiliate_observations click
              ON click.observation_id=conversion.click_observation_id
             AND click.experiment_id=conversion.experiment_id
             AND click.kind='click' AND click.subject_key=conversion.subject_key
             AND click.occurred_at<=conversion.occurred_at
            WHERE conversion.kind='conversion' AND click.observation_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if invalid_conversion is not None:
            raise SchemaDriftError("affiliate conversion lacks matching click evidence")
        for measurement in connection.execute(
            """
            SELECT m.*, e.minimum_clicks FROM affiliate_measurements m
            JOIN affiliate_experiments e ON e.experiment_id=m.experiment_id
            """
        ).fetchall():
            observed = connection.execute(
                """
                SELECT
                  SUM(CASE WHEN kind='impression' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN kind='click' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN kind='conversion' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN kind='conversion' THEN gross_revenue_minor ELSE 0 END),
                  SUM(CASE WHEN kind='conversion' THEN commission_minor ELSE 0 END)
                FROM affiliate_observations WHERE experiment_id=?
                """,
                (measurement["experiment_id"],),
            ).fetchone()
            impressions, clicks, conversions = (
                int(observed[0] or 0), int(observed[1] or 0), int(observed[2] or 0)
            )
            payload = {
                "impression_count": impressions,
                "click_count": clicks,
                "conversion_count": conversions,
                "conversion_rate_bps": conversions * 10000 // clicks if clicks else 0,
                "gross_revenue_minor": int(observed[3] or 0),
                "commission_minor": int(observed[4] or 0),
                "sufficient_sample": clicks >= measurement["minimum_clicks"],
            }
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            if (
                digest != measurement["measurement_hash"]
                or payload["impression_count"] != measurement["impression_count"]
                or payload["click_count"] != measurement["click_count"]
                or payload["conversion_count"] != measurement["conversion_count"]
                or payload["conversion_rate_bps"] != measurement["conversion_rate_bps"]
                or payload["gross_revenue_minor"] != measurement["gross_revenue_minor"]
                or payload["commission_minor"] != measurement["commission_minor"]
                or int(payload["sufficient_sample"]) != measurement["sufficient_sample"]
            ):
                raise SchemaDriftError("affiliate measurement differs from observation replay")
        invalid_learning = connection.execute(
            """
            SELECT 1 FROM affiliate_learnings learning
            LEFT JOIN affiliate_verifications verification
              ON verification.verification_id=learning.verification_id
             AND verification.decision='verified'
            LEFT JOIN memory_records memory
              ON memory.memory_id=learning.memory_id
             AND memory.tenant_id=learning.tenant_id
             AND memory.business_id=learning.business_id
             AND memory.verification_status='candidate'
            WHERE verification.verification_id IS NULL OR memory.memory_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if invalid_learning is not None:
            raise SchemaDriftError("affiliate learning lacks verified candidate memory")
        for learning in connection.execute(
            """
            SELECT learning.statement_hash, memory.statement
            FROM affiliate_learnings learning
            JOIN memory_records memory ON memory.memory_id=learning.memory_id
            """
        ).fetchall():
            if hashlib.sha256(learning["statement"].encode()).hexdigest() != learning["statement_hash"]:
                raise SchemaDriftError("affiliate learning statement hash does not match")

    def _assert_portfolio_integrity(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        existing = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='capability_pack_acceptances'"
        ).fetchone()
        if existing is None:
            return
        invalid_acceptance = connection.execute(
            """
            SELECT 1 FROM capability_pack_acceptances
            WHERE passed!=(passed_count=case_count) OR passed_count>case_count
            LIMIT 1
            """
        ).fetchone()
        if invalid_acceptance is not None:
            raise SchemaDriftError("capability pack acceptance counts are inconsistent")
        invalid_snapshot = connection.execute(
            """
            SELECT 1 FROM aggregate_performance_snapshots snapshot
            LEFT JOIN actors producer ON producer.actor_id=snapshot.producer_id
              AND producer.tenant_id=snapshot.tenant_id AND producer.enabled=1
            WHERE producer.actor_id IS NULL
               OR snapshot.source_system NOT LIKE '%-readonly'
               OR snapshot.evidence_class!='directional_aggregate'
               OR snapshot.conversions>snapshot.outbound_clicks
               OR snapshot.outbound_clicks>snapshot.content_clicks
               OR snapshot.content_clicks>snapshot.engagements
               OR snapshot.engagements>snapshot.impressions
               OR json_array_length(snapshot.evidence_refs_json)=0
               OR NOT EXISTS (
                 SELECT 1 FROM json_each(producer.business_ids_json) business
                 WHERE business.value=snapshot.business_id
               )
               OR NOT EXISTS (
                 SELECT 1 FROM json_each(producer.roles_json) role
                 WHERE role.value IN ('commerce','marketing','research','operations')
               )
               OR EXISTS (
                 SELECT 1 FROM json_each(snapshot.evidence_refs_json) reference
                 LEFT JOIN evidence_records evidence
                   ON evidence.evidence_id=reference.value
                  AND evidence.tenant_id=snapshot.tenant_id
                  AND evidence.business_id=snapshot.business_id
                 WHERE evidence.evidence_id IS NULL
               )
            LIMIT 1
            """
        ).fetchone()
        if invalid_snapshot is not None:
            raise SchemaDriftError("aggregate performance evidence is invalid or crosses scope")
        for snapshot in connection.execute(
            "SELECT * FROM aggregate_performance_snapshots"
        ).fetchall():
            evidence_rows = connection.execute(
                """
                SELECT evidence.facts_json, evidence.confidence,
                       evidence.observed_at
                FROM json_each(?) reference
                JOIN evidence_records evidence ON evidence.evidence_id=reference.value
                 AND evidence.tenant_id=? AND evidence.business_id=?
                """,
                (
                    snapshot["evidence_refs_json"], snapshot["tenant_id"],
                    snapshot["business_id"],
                ),
            ).fetchall()
            expected_facts = {
                "commission_minor": int(snapshot["commission_minor"]),
                "content_clicks": int(snapshot["content_clicks"]),
                "conversions": int(snapshot["conversions"]),
                "engagements": int(snapshot["engagements"]),
                "gross_revenue_minor": int(snapshot["gross_revenue_minor"]),
                "impressions": int(snapshot["impressions"]),
                "outbound_clicks": int(snapshot["outbound_clicks"]),
            }
            observed_facts: dict[str, Any] = {}
            for evidence in evidence_rows:
                if (
                    Decimal(evidence["confidence"]) < Decimal("0.70")
                    or datetime.fromisoformat(evidence["observed_at"])
                    < datetime.fromisoformat(snapshot["window_end"])
                    or datetime.fromisoformat(evidence["observed_at"])
                    > datetime.fromisoformat(snapshot["imported_at"])
                ):
                    raise SchemaDriftError(
                        "aggregate performance evidence is stale, future, or weak"
                    )
                facts = json.loads(evidence["facts_json"])
                for key in expected_facts:
                    if key not in facts:
                        continue
                    if key in observed_facts and observed_facts[key] != facts[key]:
                        raise SchemaDriftError(
                            "aggregate performance evidence facts conflict"
                        )
                    observed_facts[key] = facts[key]
            if observed_facts != expected_facts:
                raise SchemaDriftError(
                    "aggregate performance differs from normalized evidence"
                )
            impressions = int(snapshot["impressions"])
            engagements = int(snapshot["engagements"])
            outbound = int(snapshot["outbound_clicks"])
            conversions = int(snapshot["conversions"])
            metrics = {
                "commission_minor": int(snapshot["commission_minor"]),
                "content_clicks": int(snapshot["content_clicks"]),
                "conversion_rate_bps": (
                    conversions * 10000 // outbound if outbound else 0
                ),
                "conversions": conversions,
                "engagement_rate_bps": (
                    engagements * 10000 // impressions if impressions else 0
                ),
                "engagements": engagements,
                "gross_revenue_minor": int(snapshot["gross_revenue_minor"]),
                "impressions": impressions,
                "outbound_click_rate_bps": (
                    outbound * 10000 // impressions if impressions else 0
                ),
                "outbound_clicks": outbound,
                "sufficient_sample": (
                    outbound >= snapshot["minimum_outbound_clicks"]
                ),
            }
            payload = {
                "channel": snapshot["channel"],
                "evidence_class": snapshot["evidence_class"],
                "evidence_refs": list(json.loads(snapshot["evidence_refs_json"])),
                "metrics": metrics,
                "minimum_outbound_clicks": snapshot["minimum_outbound_clicks"],
                "offer_key": snapshot["offer_key"],
                "source_ref": snapshot["source_ref"],
                "source_system": snapshot["source_system"],
                "window_end": snapshot["window_end"],
                "window_start": snapshot["window_start"],
            }
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if digest != snapshot["snapshot_hash"]:
                raise SchemaDriftError("aggregate performance hash does not match")
            verification = connection.execute(
                """
                SELECT * FROM aggregate_performance_verifications
                WHERE snapshot_id=?
                """,
                (snapshot["snapshot_id"],),
            ).fetchone()
            if verification is None:
                continue
            expected = (
                "rejected"
                if verification["recomputed_hash"] != digest
                else "verified"
                if metrics["sufficient_sample"]
                else "inconclusive"
            )
            if verification["decision"] != expected:
                raise SchemaDriftError("aggregate verification decision is inconsistent")

    def _assert_production_integrity(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        existing = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_qualifications'"
        ).fetchone()
        if existing is None:
            return
        for row in connection.execute(
            "SELECT * FROM production_qualifications"
        ).fetchall():
            try:
                checks = json.loads(row["checks_json"])
            except (TypeError, json.JSONDecodeError) as error:
                raise SchemaDriftError(
                    "production qualification checks are invalid"
                ) from error
            canonical = json.dumps(checks, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            if (
                not isinstance(checks, dict)
                or not checks
                or any(type(value) is not bool for value in checks.values())
                or digest != row["checks_hash"]
                or len(row["artifact_hash"]) != 64
                or not set(row["artifact_hash"]) <= set("0123456789abcdef")
                or "latest" in row["release_version"].lower()
                or (row["decision"] == "passed") != all(checks.values())
                or row["external_side_effects_enabled"] != 0
            ):
                raise SchemaDriftError(
                    "production qualification evidence is inconsistent"
                )
        invalid_qualification = connection.execute(
            """
            SELECT 1 FROM production_qualifications qualification
            LEFT JOIN actors producer ON producer.actor_id=qualification.producer_id
              AND producer.tenant_id=qualification.tenant_id AND producer.enabled=1
            LEFT JOIN actors verifier ON verifier.actor_id=qualification.verifier_id
              AND verifier.tenant_id=qualification.tenant_id AND verifier.enabled=1
            WHERE producer.actor_id IS NULL OR verifier.actor_id IS NULL
               OR producer.actor_id=verifier.actor_id
               OR NOT EXISTS (SELECT 1 FROM json_each(producer.business_ids_json) b
                              WHERE b.value=qualification.business_id)
               OR NOT EXISTS (SELECT 1 FROM json_each(verifier.business_ids_json) b
                              WHERE b.value=qualification.business_id)
               OR NOT EXISTS (SELECT 1 FROM json_each(producer.roles_json) r
                              WHERE r.value IN ('platform-reliability','operations'))
               OR NOT EXISTS (SELECT 1 FROM json_each(verifier.roles_json) r
                              WHERE r.value IN ('qa','verifier'))
            LIMIT 1
            """
        ).fetchone()
        if invalid_qualification is not None:
            raise SchemaDriftError(
                "production qualification lacks independent scoped evidence"
            )
        invalid_plan = connection.execute(
            """
            SELECT 1 FROM legacy_cutover_plans plan
            LEFT JOIN actors owner ON owner.actor_id=plan.owner_id
              AND owner.tenant_id=plan.tenant_id AND owner.enabled=1
            WHERE owner.actor_id IS NULL OR plan.legacy_disable_allowed!=0
               OR plan.external_side_effects_enabled!=0
               OR plan.mode NOT IN ('read_only','proposal','shadow')
               OR NOT EXISTS (SELECT 1 FROM json_each(owner.business_ids_json) b
                              WHERE b.value=plan.business_id)
            LIMIT 1
            """
        ).fetchone()
        if invalid_plan is not None:
            raise SchemaDriftError(
                "legacy cutover plan crosses its non-executing boundary"
            )
        expected_next = {
            "inventoried": {"shadow_compared"},
            "shadow_compared": {"recovery_verified"},
            "recovery_verified": {"approved"},
            "approved": {"canary_observed", "rolled_back"},
            "canary_observed": {"rolled_back"},
            "rolled_back": set(),
        }
        for plan in connection.execute(
            "SELECT * FROM legacy_cutover_plans"
        ).fetchall():
            if (
                len(plan["rollback_hash"]) != 64
                or not set(plan["rollback_hash"]) <= set("0123456789abcdef")
            ):
                raise SchemaDriftError("legacy cutover rollback hash is invalid")
            events = connection.execute(
                "SELECT * FROM legacy_cutover_events WHERE plan_id=? ORDER BY rowid",
                (plan["plan_id"],),
            ).fetchall()
            if not events or events[0]["stage"] != "inventoried":
                raise SchemaDriftError(
                    "legacy cutover plan lacks its inventory event"
                )
            for previous, current in zip(events, events[1:]):
                if current["stage"] not in expected_next[previous["stage"]]:
                    raise SchemaDriftError(
                        "legacy cutover event sequence is invalid"
                    )
            for event in events:
                if (
                    event["tenant_id"] != plan["tenant_id"]
                    or event["business_id"] != plan["business_id"]
                    or len(event["evidence_hash"]) != 64
                    or not set(event["evidence_hash"]) <= set("0123456789abcdef")
                ):
                    raise SchemaDriftError(
                        "legacy cutover event differs from its plan scope or evidence"
                    )
                actor = connection.execute(
                    "SELECT * FROM actors WHERE actor_id=?", (event["actor_id"],)
                ).fetchone()
                if actor is None or actor["tenant_id"] != event["tenant_id"]:
                    raise SchemaDriftError(
                        "legacy cutover event actor crosses scope"
                    )
                roles = set(json.loads(actor["roles_json"]))
                memberships = set(json.loads(actor["business_ids_json"]))
                if event["business_id"] not in memberships:
                    raise SchemaDriftError(
                        "legacy cutover event actor crosses business scope"
                    )
                if (
                    event["stage"] in {"shadow_compared", "recovery_verified"}
                    and not roles & {"qa", "verifier"}
                ):
                    raise SchemaDriftError(
                        "legacy cutover verification lacks QA"
                    )
                if event["stage"] == "approved" and (
                    actor["actor_type"] != "human"
                    or not roles & {"business-owner", "operations"}
                ):
                    raise SchemaDriftError(
                        "legacy cutover approval lacks a human owner"
                    )

    def _assert_completion_attestation_integrity(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        self._load_truth_key()
        existing = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'completion_attestations'
            """
        ).fetchone()
        if existing is None:
            raise SchemaDriftError(
                "authenticated completion attestation table is missing"
            )
        terminal_mismatch = connection.execute(
            """
            SELECT 1
            FROM execution_attempts AS attempt
            LEFT JOIN completion_attestations AS attestation
              ON attestation.attempt_id = attempt.attempt_id
             AND attestation.work_item_id = attempt.work_item_id
             AND attestation.tenant_id = attempt.tenant_id
             AND attestation.business_id = attempt.business_id
            LEFT JOIN outcome_verifications AS verification
              ON verification.verification_id = attestation.verification_id
             AND verification.decision = attempt.status
            WHERE attempt.status IN ('verified', 'disproved')
              AND verification.verification_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if terminal_mismatch is not None:
            raise SchemaDriftError(
                "terminal execution truth lacks an authenticated attestation"
            )
        work_mismatch = connection.execute(
            """
            SELECT 1
            FROM work_items AS work
            LEFT JOIN completion_attestations AS attestation
              ON attestation.work_item_id = work.work_item_id
             AND attestation.tenant_id = work.tenant_id
             AND attestation.business_id = work.business_id
            LEFT JOIN outcome_verifications AS verification
              ON verification.verification_id = attestation.verification_id
             AND verification.decision = work.status
            WHERE work.status IN ('verified', 'disproved')
              AND verification.verification_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if work_mismatch is not None:
            raise SchemaDriftError(
                "terminal work truth lacks an authenticated attestation"
            )
        rows = connection.execute(
            """
            SELECT verification.*, attestation.key_id, attestation.signature,
                   attestation.payload_version
            FROM outcome_verifications AS verification
            LEFT JOIN completion_attestations AS attestation
              ON attestation.verification_id = verification.verification_id
            ORDER BY verification.verification_id
            """
        ).fetchall()
        for row in rows:
            if row["signature"] is None or row["payload_version"] != 2:
                raise SchemaDriftError(
                    "verification decision lacks a current authenticated "
                    "attestation"
                )
            attempt = connection.execute(
                """
                SELECT *
                FROM execution_attempts
                WHERE attempt_id = ?
                """,
                (row["attempt_id"],),
            ).fetchone()
            receipt_ids = json.loads(row["evidence_receipt_ids_json"])
            placeholders = ",".join("?" for _ in receipt_ids)
            receipt_rows = (
                connection.execute(
                    f"""
                    SELECT *
                    FROM evidence_receipts
                    WHERE receipt_id IN ({placeholders})
                    """,
                    receipt_ids,
                ).fetchall()
                if receipt_ids
                else []
            )
            if attempt is None or len(receipt_rows) != len(receipt_ids):
                raise SchemaDriftError(
                    "completion attestation references missing durable truth"
                )
            receipts = [
                self._evidence_receipt_row_to_dict(receipt)
                for receipt in receipt_rows
            ]
            work, objective = self._completion_context(
                connection,
                row["work_item_id"],
            )
            verification = {
                "verification_id": row["verification_id"],
                "attempt_id": row["attempt_id"],
                "work_item_id": row["work_item_id"],
                "tenant_id": row["tenant_id"],
                "business_id": row["business_id"],
                "verifier_id": row["verifier_id"],
                "decision": row["decision"],
                "evidence_receipt_ids": receipt_ids,
                "expected_facts": json.loads(row["expected_facts_json"]),
                "rationale": row["rationale"],
                "policy_version": row["policy_version"],
                "decided_at": row["decided_at"],
            }
            key_id, signature = self._completion_signature(
                verification,
                attempt,
                receipts,
                work,
                objective,
            )
            if (
                not hmac.compare_digest(row["key_id"], key_id)
                or not hmac.compare_digest(row["signature"], signature)
            ):
                raise SchemaDriftError(
                    "completion attestation signature is invalid"
                )

    def _assert_spend_integrity(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not {
            "spend_envelopes",
            "spend_commitments",
        } <= existing_tables:
            return
        overlap = connection.execute(
            """
            SELECT 1
            FROM spend_envelopes AS first
            JOIN spend_envelopes AS second
              ON second.rowid > first.rowid
             AND second.tenant_id = first.tenant_id
             AND second.business_id = first.business_id
             AND second.action_type = first.action_type
             AND second.platform = first.platform
             AND second.account_id = first.account_id
             AND second.period_start < first.period_end
             AND second.period_end > first.period_start
            LIMIT 1
            """
        ).fetchone()
        if overlap is not None:
            raise SchemaDriftError("spend envelope periods overlap")
        invalid_commitment = connection.execute(
            """
            SELECT 1
            FROM spend_commitments AS commitment
            LEFT JOIN spend_envelopes AS envelope
              ON envelope.envelope_id = commitment.envelope_id
             AND envelope.tenant_id = commitment.tenant_id
             AND envelope.business_id = commitment.business_id
            LEFT JOIN work_items AS work
              ON work.work_item_id = commitment.work_item_id
             AND work.tenant_id = commitment.tenant_id
             AND work.business_id = commitment.business_id
            LEFT JOIN execution_attempts AS attempt
              ON attempt.attempt_id = commitment.attempt_id
             AND attempt.work_item_id = commitment.work_item_id
             AND attempt.tenant_id = commitment.tenant_id
             AND attempt.business_id = commitment.business_id
             AND attempt.action_type = work.action_type
             AND attempt.execution_mode = 'external'
            WHERE envelope.envelope_id IS NULL
               OR work.work_item_id IS NULL
               OR attempt.attempt_id IS NULL
               OR envelope.action_type != work.action_type
               OR envelope.platform != work.platform
               OR envelope.account_id != work.account_id
               OR envelope.currency != commitment.currency
               OR work.currency != commitment.currency
               OR work.amount IS NULL
               OR ABS(
                   CAST(work.amount AS REAL) * 100
                   - commitment.amount_minor
               ) >= 0.000001
               OR work.assigned_actor_id = envelope.created_by
               OR commitment.created_at < envelope.period_start
               OR commitment.created_at >= envelope.period_end
            LIMIT 1
            """
        ).fetchone()
        if invalid_commitment is not None:
            raise SchemaDriftError(
                "spend commitment does not match its budgeted execution"
            )
        exceeded = connection.execute(
            """
            SELECT 1
            FROM spend_envelopes AS envelope
            JOIN spend_commitments AS commitment
              ON commitment.envelope_id = envelope.envelope_id
            GROUP BY envelope.envelope_id, envelope.limit_minor
            HAVING SUM(commitment.amount_minor) > envelope.limit_minor
            LIMIT 1
            """
        ).fetchone()
        if exceeded is not None:
            raise SchemaDriftError(
                "spend commitments exceed their durable envelope"
            )
        uncommitted_attempt = connection.execute(
            """
            SELECT 1
            FROM execution_attempts AS attempt
            LEFT JOIN spend_commitments AS commitment
              ON commitment.attempt_id = attempt.attempt_id
             AND commitment.work_item_id = attempt.work_item_id
             AND commitment.tenant_id = attempt.tenant_id
             AND commitment.business_id = attempt.business_id
            WHERE attempt.execution_mode = 'external'
              AND attempt.action_type LIKE '%.spend'
              AND commitment.commitment_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if uncommitted_attempt is not None:
            raise SchemaDriftError(
                "external spend attempt lacks a durable commitment"
            )
        placeholders = ",".join(
            "?" for _ in PROHIBITED_FINANCIAL_ACTIONS
        )
        prohibited_attempt = connection.execute(
            f"""
            SELECT 1
            FROM execution_attempts
            WHERE execution_mode = 'external'
              AND action_type IN ({placeholders})
            LIMIT 1
            """,
            tuple(sorted(PROHIBITED_FINANCIAL_ACTIONS)),
        ).fetchone()
        if prohibited_attempt is not None:
            raise SchemaDriftError(
                "external financial execution is present in durable state"
            )

    def _require_business_scope(
        self,
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        business_id: str,
    ) -> None:
        row = connection.execute(
            """
            SELECT 1
            FROM businesses
            WHERE tenant_id = ? AND business_id = ?
            """,
            (tenant_id, business_id),
        ).fetchone()
        if row is None:
            raise ValueError(
                "business is missing or outside the tenant boundary"
            )

    def schema_status(self) -> dict[str, Any]:
        """Return a non-mutating schema and integrity report."""
        if not self.path.exists():
            return {
                "database": str(self.path),
                "exists": False,
                "integrity": "missing",
                "current_version": 0,
                "expected_version": LATEST_SCHEMA_VERSION,
                "migration_valid": False,
                "migration_errors": ["database is missing"],
                "migrations": [],
                "truth_key": str(self.truth_key_path),
            }
        with self._connection() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            table_exists = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'schema_migrations'
                """
            ).fetchone()
            rows = (
                connection.execute(
                    """
                    SELECT version, name, checksum, applied_at
                    FROM schema_migrations
                    ORDER BY version
                    """
                ).fetchall()
                if table_exists is not None
                else []
            )
            attestation_errors: list[str] = []
            data_integrity_errors: list[str] = []
            if rows:
                declared_version = rows[-1]["version"]
                attestation_errors = _schema_attestation_errors(
                    connection,
                    declared_version,
                )
                if (
                    declared_version == LATEST_SCHEMA_VERSION
                    and not attestation_errors
                ):
                    try:
                        self._assert_tenant_business_integrity(connection)
                        self._assert_parent_scope_integrity(connection)
                        self._assert_spend_integrity(connection)
                        self._assert_routing_integrity(connection)
                        self._assert_shadow_runtime_integrity(connection)
                        self._assert_affiliate_shadow_integrity(connection)
                        self._assert_portfolio_integrity(connection)
                        self._assert_production_integrity(connection)
                        self._assert_completion_attestation_integrity(
                            connection
                        )
                    except (
                        SchemaDriftError,
                        KeyError,
                        TypeError,
                        ValueError,
                        sqlite3.Error,
                    ) as error:
                        data_integrity_errors.append(
                            f"durable data attestation failed: {error}"
                        )
        migrations = [dict(row) for row in rows]
        current_version = migrations[-1]["version"] if migrations else 0
        expected = _registered_migrations()
        migration_errors: list[str] = []
        if table_exists is None:
            migration_errors.append("migration ledger is missing")
        elif not migrations:
            migration_errors.append("migration ledger is empty")
        observed_versions = [row["version"] for row in migrations]
        if observed_versions and observed_versions != list(
            range(1, max(observed_versions) + 1)
        ):
            migration_errors.append("migration ledger contains a version gap")
        for migration in migrations:
            definition = expected.get(migration["version"])
            if definition is None:
                migration_errors.append(
                    f"unknown migration version {migration['version']}"
                )
            elif (
                migration["name"] != definition[0]
                or migration["checksum"] != definition[1]
            ):
                migration_errors.append(
                    f"migration {migration['version']} checksum or name differs"
                )
        migration_errors.extend(attestation_errors)
        migration_errors.extend(data_integrity_errors)
        return {
            "database": str(self.path),
            "exists": True,
            "integrity": integrity,
            "current_version": current_version,
            "expected_version": LATEST_SCHEMA_VERSION,
            "migration_valid": not migration_errors,
            "migration_errors": migration_errors,
            "migrations": migrations,
            "truth_key": str(self.truth_key_path),
        }

    def create_backup(self, destination: str | Path) -> Path:
        """Stage, verify, and publish a point-in-time database/key pair."""
        if not self.path.exists():
            raise FileNotFoundError(f"database does not exist: {self.path}")
        status = self.schema_status()
        key: bytes | None = None
        if status["current_version"] >= 5:
            key = self._load_truth_key()
        if not status["migration_valid"]:
            raise SchemaDriftError(
                "database schema or durable truth is invalid; refusing backup"
            )
        destination_path = Path(destination)
        if self.path.resolve() == destination_path.resolve():
            raise ValueError("backup destination must differ from the source database")
        if destination_path.exists():
            raise FileExistsError(
                f"backup destination already exists: {destination_path}"
            )
        backup_key_path = destination_path.with_name(
            f"{destination_path.name}.truth-key"
        )
        if backup_key_path.exists():
            raise FileExistsError(
                f"backup truth-key destination already exists: "
                f"{backup_key_path}"
            )
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, staged_name = tempfile.mkstemp(
            prefix=f".{destination_path.name}.",
            suffix=".staged",
            dir=destination_path.parent,
        )
        os.close(descriptor)
        staged_path = Path(staged_name)
        staged_key_path = staged_path.with_name(
            f"{staged_path.name}.truth-key"
        )
        published_key = False
        published_database = False
        try:
            source = self._connect()
            backup = sqlite3.connect(staged_path)
            try:
                source.backup(backup)
                result = backup.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]
                if result != "ok":
                    raise RuntimeError(
                        f"backup integrity check failed: {result}"
                    )
            finally:
                backup.close()
                source.close()
            if key is not None:
                key_descriptor = os.open(
                    staged_key_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                with os.fdopen(
                    key_descriptor,
                    "w",
                    encoding="ascii",
                ) as stream:
                    stream.write(key.hex())
                    stream.write("\n")
            staged = SQLiteStore(
                staged_path,
                truth_key_path=staged_key_path,
            )
            staged_status = staged.schema_status()
            if not staged_status["migration_valid"]:
                raise RuntimeError(
                    "staged backup database/key validation failed: "
                    + "; ".join(staged_status["migration_errors"])
                )
            if key is not None:
                os.replace(staged_key_path, backup_key_path)
                published_key = True
            os.replace(staged_path, destination_path)
            published_database = True
            published = SQLiteStore(
                destination_path,
                truth_key_path=backup_key_path,
            )
            published_status = published.schema_status()
            if not published_status["migration_valid"]:
                raise RuntimeError(
                    "published backup database/key validation failed: "
                    + "; ".join(published_status["migration_errors"])
                )
        except Exception:
            cleanup_paths = [staged_path, staged_key_path]
            if published_database:
                cleanup_paths.append(destination_path)
            if published_key:
                cleanup_paths.append(backup_key_path)
            for path in cleanup_paths:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            raise
        return destination_path

    def upsert_tenant(self, tenant: Tenant) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO tenants(tenant_id, display_name, status)
                VALUES (?, ?, ?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    status=excluded.status
                """,
                (tenant.tenant_id, tenant.display_name, tenant.status.value),
            )

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
        if row is None:
            return None
        return Tenant(
            tenant_id=row["tenant_id"],
            display_name=row["display_name"],
            status=TenantStatus(row["status"]),
        )

    def upsert_business(self, business: Business) -> None:
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT tenant_id FROM businesses WHERE business_id = ?",
                (business.business_id,),
            ).fetchone()
            if (
                existing is not None
                and existing["tenant_id"] != business.tenant_id
            ):
                raise ValueError(
                    "business identity cannot move across a tenant boundary"
                )
            connection.execute(
                """
                INSERT INTO businesses(
                    business_id, tenant_id, legal_name, display_name,
                    base_currency, timezone_name
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(business_id) DO UPDATE SET
                    tenant_id=excluded.tenant_id,
                    legal_name=excluded.legal_name,
                    display_name=excluded.display_name,
                    base_currency=excluded.base_currency,
                    timezone_name=excluded.timezone_name
                """,
                (
                    business.business_id,
                    business.tenant_id,
                    business.legal_name,
                    business.display_name,
                    business.base_currency,
                    business.timezone_name,
                ),
            )

    def get_business(self, business_id: str) -> Business | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM businesses WHERE business_id = ?", (business_id,)
            ).fetchone()
        if row is None:
            return None
        return Business(
            business_id=row["business_id"],
            tenant_id=row["tenant_id"],
            legal_name=row["legal_name"],
            display_name=row["display_name"],
            base_currency=row["base_currency"],
            timezone_name=row["timezone_name"],
        )

    def upsert_actor(self, actor: ActorIdentity) -> None:
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT tenant_id FROM actors WHERE actor_id = ?",
                (actor.actor_id,),
            ).fetchone()
            if (
                existing is not None
                and existing["tenant_id"] != actor.tenant_id
            ):
                raise ValueError(
                    "actor identity cannot move across a tenant boundary"
                )
            if actor.business_ids:
                placeholders = ",".join("?" for _ in actor.business_ids)
                rows = connection.execute(
                    f"""
                    SELECT business_id
                    FROM businesses
                    WHERE tenant_id = ?
                      AND business_id IN ({placeholders})
                    """,
                    (actor.tenant_id, *sorted(actor.business_ids)),
                ).fetchall()
                if {row["business_id"] for row in rows} != set(
                    actor.business_ids
                ):
                    raise ValueError(
                        "actor business scope crosses a tenant boundary"
                    )
            existing = connection.execute(
                "SELECT tenant_id FROM actors WHERE actor_id = ?",
                (actor.actor_id,),
            ).fetchone()
            if (
                existing is not None
                and existing["tenant_id"] != actor.tenant_id
            ):
                raise ValueError(
                    "actor identity cannot move across a tenant boundary"
                )
            connection.execute(
                """
                INSERT INTO actors(
                    actor_id, tenant_id, actor_type, roles_json,
                    business_ids_json, enabled
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(actor_id) DO UPDATE SET
                    tenant_id=excluded.tenant_id,
                    actor_type=excluded.actor_type,
                    roles_json=excluded.roles_json,
                    business_ids_json=excluded.business_ids_json,
                    enabled=excluded.enabled
                """,
                (
                    actor.actor_id,
                    actor.tenant_id,
                    actor.actor_type.value,
                    json.dumps(sorted(actor.roles)),
                    json.dumps(sorted(actor.business_ids)),
                    int(actor.enabled),
                ),
            )

    def get_actor(self, actor_id: str) -> ActorIdentity | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM actors WHERE actor_id = ?", (actor_id,)
            ).fetchone()
        if row is None:
            return None
        return ActorIdentity(
            actor_id=row["actor_id"],
            tenant_id=row["tenant_id"],
            actor_type=ActorType(row["actor_type"]),
            roles=frozenset(json.loads(row["roles_json"])),
            business_ids=frozenset(json.loads(row["business_ids_json"])),
            enabled=bool(row["enabled"]),
        )

    def list_agents_for_business(
        self,
        *,
        tenant_id: str,
        business_id: str,
    ) -> list[ActorIdentity]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM actors
                WHERE tenant_id = ?
                  AND actor_type = ?
                  AND enabled = 1
                ORDER BY actor_id
                """,
                (tenant_id, ActorType.AGENT.value),
            ).fetchall()
        actors = [
            ActorIdentity(
                actor_id=row["actor_id"],
                tenant_id=row["tenant_id"],
                actor_type=ActorType(row["actor_type"]),
                roles=frozenset(json.loads(row["roles_json"])),
                business_ids=frozenset(json.loads(row["business_ids_json"])),
                enabled=bool(row["enabled"]),
            )
            for row in rows
        ]
        return [
            actor
            for actor in actors
            if actor.can_access(
                tenant_id=tenant_id,
                business_id=business_id,
            )
        ]

    def upsert_authority_envelope(self, envelope: AuthorityEnvelope) -> None:
        rules = [
            {
                "action_type": rule.action_type,
                "mode": rule.mode.value,
                "platforms": sorted(rule.platforms),
                "accounts": sorted(rule.accounts),
                "actor_ids": sorted(rule.actor_ids),
                "roles": sorted(rule.roles),
                "capability_ids": sorted(rule.capability_ids),
                "max_amount": _decimal_to_string(rule.max_amount),
                "currency": rule.currency,
            }
            for rule in envelope.rules
        ]
        with self._connection() as connection:
            self._require_business_scope(
                connection,
                tenant_id=envelope.tenant_id,
                business_id=envelope.business_id,
            )
            actor_ids = {
                actor_id
                for rule in envelope.rules
                for actor_id in rule.actor_ids
            }
            for actor_id in actor_ids:
                actor = connection.execute(
                    """
                    SELECT tenant_id, business_ids_json
                    FROM actors
                    WHERE actor_id = ?
                    """,
                    (actor_id,),
                ).fetchone()
                if (
                    actor is None
                    or actor["tenant_id"] != envelope.tenant_id
                    or envelope.business_id
                    not in json.loads(actor["business_ids_json"])
                ):
                    raise ValueError(
                        "authority rule actor crosses its identity boundary"
                    )
            connection.execute(
                """
                INSERT INTO authority_envelopes(
                    envelope_id, tenant_id, business_id, rules_json, expires_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, business_id) DO UPDATE SET
                    envelope_id=excluded.envelope_id,
                    rules_json=excluded.rules_json,
                    expires_at=excluded.expires_at
                """,
                (
                    envelope.envelope_id,
                    envelope.tenant_id,
                    envelope.business_id,
                    json.dumps(rules, sort_keys=True),
                    envelope.expires_at.isoformat() if envelope.expires_at else None,
                ),
            )

    def get_authority_envelope(
        self, tenant_id: str, business_id: str
    ) -> AuthorityEnvelope | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM authority_envelopes
                WHERE tenant_id = ? AND business_id = ?
                """,
                (tenant_id, business_id),
            ).fetchone()
        if row is None:
            return None
        rules = []
        for raw_rule in json.loads(row["rules_json"]):
            rules.append(
                AuthorityRule(
                    action_type=raw_rule["action_type"],
                    mode=AuthorityMode(raw_rule["mode"]),
                    platforms=frozenset(raw_rule["platforms"]),
                    accounts=frozenset(raw_rule["accounts"]),
                    actor_ids=frozenset(raw_rule.get("actor_ids", ())),
                    roles=frozenset(raw_rule.get("roles", ())),
                    capability_ids=frozenset(
                        raw_rule.get("capability_ids", ())
                    ),
                    max_amount=(
                        Decimal(raw_rule["max_amount"])
                        if raw_rule["max_amount"] is not None
                        else None
                    ),
                    currency=raw_rule["currency"],
                )
            )
        return AuthorityEnvelope(
            envelope_id=row["envelope_id"],
            tenant_id=row["tenant_id"],
            business_id=row["business_id"],
            rules=tuple(rules),
            expires_at=(
                datetime.fromisoformat(row["expires_at"])
                if row["expires_at"]
                else None
            ),
        )

    def decide_authority(
        self,
        request: ActionRequest,
        *,
        now: datetime | None = None,
    ) -> AuthorityMode:
        with self._connection() as connection:
            return self._decide_authority_in_connection(
                connection,
                request,
                now=now,
            )

    def _decide_authority_in_connection(
        self,
        connection: sqlite3.Connection,
        request: ActionRequest,
        *,
        now: datetime | None = None,
    ) -> AuthorityMode:
        actor_row = connection.execute(
            "SELECT * FROM actors WHERE actor_id = ?",
            (request.actor_id,),
        ).fetchone()
        if actor_row is None:
            return AuthorityMode.FORBIDDEN
        actor = ActorIdentity(
            actor_id=actor_row["actor_id"],
            tenant_id=actor_row["tenant_id"],
            actor_type=ActorType(actor_row["actor_type"]),
            roles=frozenset(json.loads(actor_row["roles_json"])),
            business_ids=frozenset(
                json.loads(actor_row["business_ids_json"])
            ),
            enabled=bool(actor_row["enabled"]),
        )
        if not actor.can_access(
            tenant_id=request.tenant_id,
            business_id=request.business_id,
        ):
            return AuthorityMode.FORBIDDEN
        capability_rows = connection.execute(
            """
            SELECT capability_id
            FROM agent_capabilities
            WHERE tenant_id = ? AND business_id = ?
              AND actor_id = ? AND enabled = 1
            """,
            (
                request.tenant_id,
                request.business_id,
                request.actor_id,
            ),
        ).fetchall()
        envelope_row = connection.execute(
            """
            SELECT *
            FROM authority_envelopes
            WHERE tenant_id = ? AND business_id = ?
            """,
            (request.tenant_id, request.business_id),
        ).fetchone()
        if envelope_row is None:
            return AuthorityMode.FORBIDDEN
        rules = tuple(
            AuthorityRule(
                action_type=raw["action_type"],
                mode=AuthorityMode(raw["mode"]),
                platforms=frozenset(raw["platforms"]),
                accounts=frozenset(raw["accounts"]),
                actor_ids=frozenset(raw.get("actor_ids", ())),
                roles=frozenset(raw.get("roles", ())),
                capability_ids=frozenset(
                    raw.get("capability_ids", ())
                ),
                max_amount=(
                    Decimal(raw["max_amount"])
                    if raw["max_amount"] is not None
                    else None
                ),
                currency=raw["currency"],
            )
            for raw in json.loads(envelope_row["rules_json"])
        )
        envelope = AuthorityEnvelope(
            envelope_id=envelope_row["envelope_id"],
            tenant_id=envelope_row["tenant_id"],
            business_id=envelope_row["business_id"],
            rules=rules,
            expires_at=(
                datetime.fromisoformat(envelope_row["expires_at"])
                if envelope_row["expires_at"]
                else None
            ),
        )
        enriched = ActionRequest(
            action_type=request.action_type,
            tenant_id=request.tenant_id,
            business_id=request.business_id,
            actor_id=request.actor_id,
            platform=request.platform,
            account_id=request.account_id,
            amount=request.amount,
            currency=request.currency,
            actor_roles=actor.roles,
            capability_ids=frozenset(
                row["capability_id"] for row in capability_rows
            ),
            attributes=request.attributes,
        )
        return envelope.decide(enriched, now=now)

    def create_spend_envelope(
        self,
        *,
        envelope_id: str,
        tenant_id: str,
        business_id: str,
        action_type: str,
        platform: str,
        account_id: str,
        currency: str,
        limit: Decimal,
        period_start: datetime,
        period_end: datetime,
        created_by: str,
        rationale: str,
        now: datetime,
    ) -> bool:
        if not all(
            value.strip()
            for value in (
                envelope_id,
                tenant_id,
                business_id,
                action_type,
                platform,
                account_id,
                currency,
                created_by,
                rationale,
            )
        ):
            raise ValueError("spend envelope fields cannot be empty")
        if not requires_spend_envelope(action_type):
            raise ValueError("spend envelope action must end with .spend")
        if period_end <= period_start:
            raise ValueError("spend envelope period must be positive")
        limit_minor = _amount_to_minor(limit)
        normalized_currency = currency.upper()
        created_at = _utc_iso(now)
        start = _utc_iso(period_start)
        end = _utc_iso(period_end)
        with self._immediate_connection() as connection:
            existing = connection.execute(
                "SELECT * FROM spend_envelopes WHERE envelope_id = ?",
                (envelope_id,),
            ).fetchone()
            if existing is not None:
                exact = (
                    existing["tenant_id"] == tenant_id
                    and existing["business_id"] == business_id
                    and existing["action_type"] == action_type
                    and existing["platform"] == platform
                    and existing["account_id"] == account_id
                    and existing["currency"] == normalized_currency
                    and existing["limit_minor"] == limit_minor
                    and existing["period_start"] == start
                    and existing["period_end"] == end
                    and existing["created_by"] == created_by
                    and existing["rationale"] == rationale
                    and existing["created_at"] == created_at
                )
                if not exact:
                    raise ValueError(
                        "spend envelope ID was reused with new content"
                    )
                return False
            actor = connection.execute(
                """
                SELECT actor_type, tenant_id, roles_json, business_ids_json,
                       enabled
                FROM actors
                WHERE actor_id = ?
                """,
                (created_by,),
            ).fetchone()
            business = connection.execute(
                """
                SELECT tenant_id, base_currency
                FROM businesses
                WHERE business_id = ?
                """,
                (business_id,),
            ).fetchone()
            if (
                business is None
                or business["tenant_id"] != tenant_id
                or business["base_currency"] != normalized_currency
            ):
                raise ValueError(
                    "spend envelope must use the business base currency"
                )
            if (
                actor is None
                or not actor["enabled"]
                or actor["actor_type"] != ActorType.HUMAN.value
                or actor["tenant_id"] != tenant_id
                or business_id not in json.loads(actor["business_ids_json"])
                or not (
                    {"business-owner", "finance-approver"}
                    & set(json.loads(actor["roles_json"]))
                )
            ):
                raise ValueError(
                    "spend envelope requires an authorized in-scope human"
                )
            overlap = connection.execute(
                """
                SELECT 1
                FROM spend_envelopes
                WHERE tenant_id = ? AND business_id = ?
                  AND action_type = ? AND platform = ? AND account_id = ?
                  AND ? < period_end AND ? > period_start
                LIMIT 1
                """,
                (
                    tenant_id,
                    business_id,
                    action_type,
                    platform,
                    account_id,
                    start,
                    end,
                ),
            ).fetchone()
            if overlap is not None:
                raise ValueError(
                    "spend envelope period overlaps existing authority"
                )
            connection.execute(
                """
                INSERT INTO spend_envelopes(
                    envelope_id, tenant_id, business_id, action_type,
                    platform, account_id, currency, limit_minor,
                    period_start, period_end, created_by, rationale, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope_id,
                    tenant_id,
                    business_id,
                    action_type,
                    platform,
                    account_id,
                    normalized_currency,
                    limit_minor,
                    start,
                    end,
                    created_by,
                    rationale,
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_records(
                    audit_id, event_id, run_id, tenant_id, business_id,
                    record_type, details_json, created_at
                )
                VALUES (?, NULL, ?, ?, ?, 'budget.created', ?, ?)
                """,
                (
                    f"audit-{envelope_id}",
                    envelope_id,
                    tenant_id,
                    business_id,
                    json.dumps(
                        {
                            "account_id": account_id,
                            "action_type": action_type,
                            "created_by": created_by,
                            "currency": normalized_currency,
                            "limit": str(limit),
                            "period_end": end,
                            "period_start": start,
                            "platform": platform,
                            "rationale": rationale,
                        },
                        sort_keys=True,
                    ),
                    created_at,
                ),
            )
        return True

    def get_spend_envelope(
        self,
        envelope_id: str,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT envelope.*,
                       COALESCE(SUM(commitment.amount_minor), 0)
                           AS committed_minor
                FROM spend_envelopes AS envelope
                LEFT JOIN spend_commitments AS commitment
                  ON commitment.envelope_id = envelope.envelope_id
                WHERE envelope.envelope_id = ?
                GROUP BY envelope.envelope_id
                """,
                (envelope_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["limit"] = Decimal(result.pop("limit_minor")) / 100
        result["committed"] = (
            Decimal(result.pop("committed_minor")) / 100
        )
        result["remaining"] = result["limit"] - result["committed"]
        return result

    def _reserve_spend_commitment_in_connection(
        self,
        connection: sqlite3.Connection,
        *,
        work: sqlite3.Row,
        attempt_id: str,
        now: datetime,
    ) -> str:
        if (
            work["amount"] is None
            or work["currency"] is None
            or work["platform"] is None
            or work["account_id"] is None
        ):
            raise ValueError(
                "external spend requires amount, currency, platform, "
                "and account"
            )
        amount = Decimal(work["amount"])
        amount_minor = _amount_to_minor(amount)
        timestamp = _utc_iso(now)
        envelopes = connection.execute(
            """
            SELECT *
            FROM spend_envelopes
            WHERE tenant_id = ? AND business_id = ?
              AND action_type = ? AND platform = ? AND account_id = ?
              AND currency = ?
              AND period_start <= ? AND period_end > ?
            """,
            (
                work["tenant_id"],
                work["business_id"],
                work["action_type"],
                work["platform"],
                work["account_id"],
                work["currency"],
                timestamp,
                timestamp,
            ),
        ).fetchall()
        if len(envelopes) != 1:
            raise ValueError(
                "external spend requires exactly one active spend envelope"
            )
        envelope = envelopes[0]
        committed_minor = connection.execute(
            """
            SELECT COALESCE(SUM(amount_minor), 0)
            FROM spend_commitments
            WHERE envelope_id = ?
            """,
            (envelope["envelope_id"],),
        ).fetchone()[0]
        if committed_minor + amount_minor > envelope["limit_minor"]:
            raise ValueError("external spend exceeds remaining budget")
        commitment_id = f"commitment-{attempt_id}"
        connection.execute(
            """
            INSERT INTO spend_commitments(
                commitment_id, envelope_id, attempt_id, work_item_id,
                tenant_id, business_id, amount_minor, currency, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                commitment_id,
                envelope["envelope_id"],
                attempt_id,
                work["work_item_id"],
                work["tenant_id"],
                work["business_id"],
                amount_minor,
                work["currency"],
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO audit_records(
                audit_id, event_id, run_id, tenant_id, business_id,
                record_type, details_json, created_at
            )
            VALUES (?, NULL, ?, ?, ?, 'budget.committed', ?, ?)
            """,
            (
                f"audit-{commitment_id}",
                work["work_item_id"],
                work["tenant_id"],
                work["business_id"],
                json.dumps(
                    {
                        "amount": str(amount),
                        "attempt_id": attempt_id,
                        "currency": work["currency"],
                        "envelope_id": envelope["envelope_id"],
                    },
                    sort_keys=True,
                ),
                timestamp,
            ),
        )
        return commitment_id

    def _is_emergency_stop_active_in_connection(
        self,
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        business_id: str,
    ) -> bool:
        row = connection.execute(
            """
            SELECT action
            FROM emergency_stop_events
            WHERE tenant_id = ? AND business_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (tenant_id, business_id),
        ).fetchone()
        return bool(
            row is not None
            and row["action"] == EmergencyStopAction.ACTIVATED.value
        )

    def is_emergency_stop_active(
        self,
        *,
        tenant_id: str,
        business_id: str,
    ) -> bool:
        with self._connection() as connection:
            return self._is_emergency_stop_active_in_connection(
                connection,
                tenant_id=tenant_id,
                business_id=business_id,
            )

    def record_emergency_stop(
        self,
        *,
        event_id: str,
        tenant_id: str,
        business_id: str,
        actor_id: str,
        action: EmergencyStopAction,
        reason: str,
        now: datetime,
    ) -> bool:
        if not reason.strip():
            raise ValueError("emergency-stop reason is required")
        timestamp = _utc_iso(now)
        with self._immediate_connection() as connection:
            existing = connection.execute(
                """
                SELECT tenant_id, business_id, actor_id, action, reason,
                       created_at
                FROM emergency_stop_events
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
            if existing is not None:
                exact = (
                    existing["tenant_id"] == tenant_id
                    and existing["business_id"] == business_id
                    and existing["actor_id"] == actor_id
                    and existing["action"] == action.value
                    and existing["reason"] == reason
                    and existing["created_at"] == timestamp
                )
                if not exact:
                    raise ValueError(
                        "emergency-stop event ID was reused with new content"
                    )
                return False
            actor = connection.execute(
                """
                SELECT actor_type, tenant_id, roles_json, business_ids_json,
                       enabled
                FROM actors
                WHERE actor_id = ?
                """,
                (actor_id,),
            ).fetchone()
            if (
                actor is None
                or not actor["enabled"]
                or actor["actor_type"] != ActorType.HUMAN.value
                or actor["tenant_id"] != tenant_id
                or business_id not in json.loads(actor["business_ids_json"])
                or not (
                    {"business-owner", "emergency-admin"}
                    & set(json.loads(actor["roles_json"]))
                )
            ):
                raise ValueError(
                    "emergency stop requires an authorized in-scope human"
                )
            active = self._is_emergency_stop_active_in_connection(
                connection,
                tenant_id=tenant_id,
                business_id=business_id,
            )
            if action is EmergencyStopAction.CLEARED and not active:
                raise ValueError("emergency stop is not active")
            connection.execute(
                """
                INSERT INTO emergency_stop_events(
                    event_id, tenant_id, business_id, actor_id, action,
                    reason, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    tenant_id,
                    business_id,
                    actor_id,
                    action.value,
                    reason,
                    timestamp,
                ),
            )
            if action is EmergencyStopAction.ACTIVATED:
                connection.execute(
                    """
                    UPDATE work_items
                    SET status = 'ready', claimed_by = NULL,
                        lease_expires_at = NULL,
                        last_error = 'released by emergency stop',
                        updated_at = ?
                    WHERE tenant_id = ? AND business_id = ?
                      AND status = 'claimed'
                    """,
                    (timestamp, tenant_id, business_id),
                )
            connection.execute(
                """
                INSERT INTO audit_records(
                    audit_id, event_id, run_id, tenant_id, business_id,
                    record_type, details_json, created_at
                )
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"audit-{event_id}",
                    event_id,
                    tenant_id,
                    business_id,
                    f"emergency_stop.{action.value}",
                    json.dumps(
                        {"actor_id": actor_id, "reason": reason},
                        sort_keys=True,
                    ),
                    timestamp,
                ),
            )
        return True

    def _insert_approval_request(
        self,
        connection: sqlite3.Connection,
        *,
        work: sqlite3.Row,
        requested_at: datetime,
        expires_at: datetime,
    ) -> str:
        if expires_at <= requested_at:
            raise ValueError("approval expiry must follow its request")
        approval_id = f"approval-{work['work_item_id']}"
        connection.execute(
            """
            INSERT INTO approval_requests(
                approval_id, work_item_id, tenant_id, business_id,
                requester_id, action_type, work_fingerprint, requested_at,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                work["work_item_id"],
                work["tenant_id"],
                work["business_id"],
                work["assigned_actor_id"],
                work["action_type"],
                _approval_work_fingerprint(work),
                _utc_iso(requested_at),
                _utc_iso(expires_at),
            ),
        )
        connection.execute(
            """
            INSERT INTO audit_records(
                audit_id, event_id, run_id, tenant_id, business_id,
                record_type, details_json, created_at
            )
            VALUES (?, NULL, ?, ?, ?, 'approval.requested', ?, ?)
            """,
            (
                f"audit-{approval_id}",
                work["work_item_id"],
                work["tenant_id"],
                work["business_id"],
                json.dumps(
                    {
                        "action_type": work["action_type"],
                        "approval_id": approval_id,
                        "expires_at": _utc_iso(expires_at),
                        "requester_id": work["assigned_actor_id"],
                    },
                    sort_keys=True,
                ),
                _utc_iso(requested_at),
            ),
        )
        return approval_id

    def get_work_approval(
        self,
        work_item_id: str,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT request.*,
                       (
                           SELECT decision
                           FROM approval_events AS event
                           WHERE event.approval_id = request.approval_id
                           ORDER BY event.created_at DESC, event.rowid DESC
                           LIMIT 1
                       ) AS latest_decision
                FROM approval_requests AS request
                WHERE request.work_item_id = ?
                """,
                (work_item_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def _has_valid_work_approval_in_connection(
        self,
        connection: sqlite3.Connection,
        *,
        work: dict[str, Any] | sqlite3.Row,
        now: datetime,
    ) -> bool:
        row = connection.execute(
            """
            SELECT request.work_fingerprint, request.expires_at,
                   (
                       SELECT decision
                       FROM approval_events AS event
                       WHERE event.approval_id = request.approval_id
                       ORDER BY event.created_at DESC, event.rowid DESC
                       LIMIT 1
                   ) AS latest_decision
            FROM approval_requests AS request
            WHERE request.work_item_id = ?
              AND request.tenant_id = ?
              AND request.business_id = ?
            """,
            (
                work["work_item_id"],
                work["tenant_id"],
                work["business_id"],
            ),
        ).fetchone()
        return bool(
            row is not None
            and row["latest_decision"] == ApprovalDecision.APPROVED.value
            and row["expires_at"] > _utc_iso(now)
            and row["work_fingerprint"] == _approval_work_fingerprint(work)
        )

    def has_valid_work_approval(
        self,
        *,
        work_item_id: str,
        now: datetime,
    ) -> bool:
        with self._connection() as connection:
            work = connection.execute(
                "SELECT * FROM work_items WHERE work_item_id = ?",
                (work_item_id,),
            ).fetchone()
            return bool(
                work is not None
                and self._has_valid_work_approval_in_connection(
                    connection,
                    work=work,
                    now=now,
                )
            )

    def expire_work_approvals(self, *, now: datetime) -> int:
        timestamp = _utc_iso(now)
        with self._immediate_connection() as connection:
            rows = connection.execute(
                """
                SELECT request.approval_id, request.work_item_id,
                       request.tenant_id, request.business_id
                FROM approval_requests AS request
                JOIN work_items AS work
                  ON work.work_item_id = request.work_item_id
                WHERE request.expires_at <= ?
                  AND work.status IN (
                      'awaiting_approval', 'ready', 'claimed'
                  )
                """,
                (timestamp,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE work_items
                    SET status = 'approval_expired', claimed_by = NULL,
                        lease_expires_at = NULL, updated_at = ?
                    WHERE work_item_id = ?
                      AND status IN (
                          'awaiting_approval', 'ready', 'claimed'
                      )
                    """,
                    (timestamp, row["work_item_id"]),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO audit_records(
                        audit_id, event_id, run_id, tenant_id, business_id,
                        record_type, details_json, created_at
                    )
                    VALUES (?, NULL, ?, ?, ?, 'approval.expired', ?, ?)
                    """,
                    (
                        f"audit-expired-{row['approval_id']}",
                        row["work_item_id"],
                        row["tenant_id"],
                        row["business_id"],
                        json.dumps(
                            {"approval_id": row["approval_id"]},
                            sort_keys=True,
                        ),
                        timestamp,
                    ),
                )
        return len(rows)

    def decide_work_approval(
        self,
        *,
        approval_id: str,
        event_id: str,
        approver_id: str,
        decision: ApprovalDecision,
        rationale: str,
        now: datetime,
    ) -> bool:
        if not rationale.strip():
            raise ValueError("approval rationale is required")
        timestamp = _utc_iso(now)
        with self._immediate_connection() as connection:
            existing = connection.execute(
                """
                SELECT approval_id, actor_id, decision, rationale, created_at
                FROM approval_events
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
            if existing is not None:
                exact = (
                    existing["approval_id"] == approval_id
                    and existing["actor_id"] == approver_id
                    and existing["decision"] == decision.value
                    and existing["rationale"] == rationale
                    and existing["created_at"] == timestamp
                )
                if not exact:
                    raise ValueError(
                        "approval event ID was reused with new content"
                    )
                return False
            request = connection.execute(
                """
                SELECT request.approval_id, request.work_item_id,
                       request.tenant_id, request.business_id,
                       request.requester_id, request.action_type,
                       request.work_fingerprint, request.expires_at,
                       work.*
                FROM approval_requests AS request
                JOIN work_items AS work
                  ON work.work_item_id = request.work_item_id
                 AND work.tenant_id = request.tenant_id
                 AND work.business_id = request.business_id
                WHERE request.approval_id = ?
                """,
                (approval_id,),
            ).fetchone()
            if request is None:
                raise ValueError("approval request does not exist")
            if request["expires_at"] <= timestamp:
                raise ValueError("approval request has expired")
            approver = connection.execute(
                """
                SELECT actor_type, tenant_id, roles_json, business_ids_json,
                       enabled
                FROM actors
                WHERE actor_id = ?
                """,
                (approver_id,),
            ).fetchone()
            if (
                approver is None
                or not approver["enabled"]
                or approver["actor_type"] != ActorType.HUMAN.value
                or approver["tenant_id"] != request["tenant_id"]
                or request["business_id"]
                not in json.loads(approver["business_ids_json"])
                or not (
                    {"approver", "business-owner", "finance-approver"}
                    & set(json.loads(approver["roles_json"]))
                )
                or approver_id == request["requester_id"]
                or approver_id == request["assigned_actor_id"]
            ):
                raise ValueError(
                    "approval requires a separate authorized approver"
                )
            latest = connection.execute(
                """
                SELECT decision
                FROM approval_events
                WHERE approval_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (approval_id,),
            ).fetchone()
            latest_decision = latest["decision"] if latest else None
            if decision is ApprovalDecision.REVOKED:
                if latest_decision != ApprovalDecision.APPROVED.value:
                    raise ValueError("only an active approval can be revoked")
                next_status = "awaiting_approval"
            else:
                if latest_decision in {
                    ApprovalDecision.APPROVED.value,
                    ApprovalDecision.REJECTED.value,
                }:
                    raise ValueError("approval request already has a decision")
                next_status = (
                    "ready"
                    if decision is ApprovalDecision.APPROVED
                    else "rejected"
                )
            if (
                decision is ApprovalDecision.APPROVED
                and self._is_emergency_stop_active_in_connection(
                    connection,
                    tenant_id=request["tenant_id"],
                    business_id=request["business_id"],
                )
            ):
                raise ValueError(
                    "cannot approve work while emergency stop is active"
                )
            if request["work_fingerprint"] != _approval_work_fingerprint(
                request
            ):
                raise ValueError("approval-bound work semantics changed")
            if decision is ApprovalDecision.APPROVED:
                authority = self._decide_authority_in_connection(
                    connection,
                    ActionRequest(
                        action_type=request["action_type"],
                        tenant_id=request["tenant_id"],
                        business_id=request["business_id"],
                        actor_id=request["assigned_actor_id"],
                        platform=request["platform"],
                        account_id=request["account_id"],
                        amount=(
                            Decimal(request["amount"])
                            if request["amount"] is not None
                            else None
                        ),
                        currency=request["currency"],
                        attributes=json.loads(request["attributes_json"]),
                    ),
                    now=now,
                )
                if authority is not AuthorityMode.APPROVE:
                    raise ValueError(
                        "approval policy is no longer current or applicable"
                    )
            connection.execute(
                """
                INSERT INTO approval_events(
                    event_id, approval_id, tenant_id, business_id, actor_id,
                    decision, rationale, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    approval_id,
                    request["tenant_id"],
                    request["business_id"],
                    approver_id,
                    decision.value,
                    rationale,
                    timestamp,
                ),
            )
            connection.execute(
                """
                UPDATE work_items
                SET status = ?, claimed_by = NULL, lease_expires_at = NULL,
                    updated_at = ?
                WHERE work_item_id = ?
                """,
                (next_status, timestamp, request["work_item_id"]),
            )
            connection.execute(
                """
                INSERT INTO audit_records(
                    audit_id, event_id, run_id, tenant_id, business_id,
                    record_type, details_json, created_at
                )
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"audit-{event_id}",
                    request["work_item_id"],
                    request["tenant_id"],
                    request["business_id"],
                    f"approval.{decision.value}",
                    json.dumps(
                        {
                            "approval_id": approval_id,
                            "approver_id": approver_id,
                            "rationale": rationale,
                        },
                        sort_keys=True,
                    ),
                    timestamp,
                ),
            )
        return True

    def insert_event(self, event: Event) -> EventReceipt:
        fingerprint = _event_fingerprint(event)
        try:
            with self._connection() as connection:
                existing_id = connection.execute(
                    """
                    SELECT tenant_id, business_id
                    FROM events
                    WHERE event_id = ?
                    """,
                    (event.event_id,),
                ).fetchone()
                if existing_id is not None and (
                    existing_id["tenant_id"] != event.tenant_id
                    or existing_id["business_id"] != event.business_id
                ):
                    raise EventIdentityConflict(
                        "event ID conflicts with another identity boundary"
                    )
                self._require_business_scope(
                    connection,
                    tenant_id=event.tenant_id,
                    business_id=event.business_id,
                )
                connection.execute(
                    """
                    INSERT INTO events(
                        event_id, tenant_id, business_id, source, actor_id, kind,
                        occurred_at, payload_json, correlation_id,
                        idempotency_key, received_at, event_fingerprint
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.tenant_id,
                        event.business_id,
                        event.source,
                        event.actor_id,
                        event.kind,
                        event.occurred_at.isoformat(),
                        json.dumps(dict(event.payload), sort_keys=True),
                        event.correlation_id,
                        event.idempotency_key,
                        _utc_now(),
                        fingerprint,
                    ),
                )
            return EventReceipt(event_id=event.event_id, inserted=True)
        except sqlite3.IntegrityError:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM events
                    WHERE event_id = ?
                       OR (
                           tenant_id = ? AND business_id = ?
                           AND source = ? AND idempotency_key IS NOT NULL
                           AND idempotency_key = ?
                       )
                    LIMIT 1
                    """,
                    (
                        event.event_id,
                        event.tenant_id,
                        event.business_id,
                        event.source,
                        event.idempotency_key,
                    ),
                ).fetchone()
            if (
                row is None
                or row["tenant_id"] != event.tenant_id
                or row["business_id"] != event.business_id
            ):
                raise EventIdentityConflict(
                    "event ID conflicts with another identity boundary"
                ) from None
            stored_fingerprint = row["event_fingerprint"]
            if stored_fingerprint is None:
                stored_fingerprint = _event_fingerprint(
                    Event(
                        event_id=row["event_id"],
                        tenant_id=row["tenant_id"],
                        business_id=row["business_id"],
                        source=row["source"],
                        actor_id=row["actor_id"],
                        kind=row["kind"],
                        occurred_at=datetime.fromisoformat(
                            row["occurred_at"]
                        ),
                        payload=json.loads(row["payload_json"]),
                        correlation_id=row["correlation_id"],
                        idempotency_key=row["idempotency_key"],
                    )
                )
            if stored_fingerprint != fingerprint:
                raise EventIdentityConflict(
                    "event or idempotency key was reused with different content"
                ) from None
            return EventReceipt(event_id=row["event_id"], inserted=False)

    def claim_event_processing(
        self,
        event: Event,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int = 300,
    ) -> EventProcessingClaim:
        if lease_seconds < 1:
            raise ValueError("event processing lease must be positive")
        receipt = self.insert_event(event)
        current = _utc_iso(now)
        lease_expires = _utc_iso(
            datetime.fromtimestamp(
                now.timestamp() + lease_seconds,
                tz=timezone.utc,
            )
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT 1 FROM workflow_runs WHERE event_id = ?",
                (receipt.event_id,),
            ).fetchone()
            if run is not None:
                connection.commit()
                return EventProcessingClaim(
                    event_id=receipt.event_id,
                    inserted=receipt.inserted,
                    claimed=False,
                )
            processing = connection.execute(
                """
                SELECT status, claimed_by, lease_expires_at
                FROM event_processing
                WHERE event_id = ?
                """,
                (receipt.event_id,),
            ).fetchone()
            claimed = False
            if processing is None:
                connection.execute(
                    """
                    INSERT INTO event_processing(
                        event_id, tenant_id, business_id, status, claimed_by,
                        lease_expires_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'processing', ?, ?, ?, ?)
                    """,
                    (
                        receipt.event_id,
                        event.tenant_id,
                        event.business_id,
                        worker_id,
                        lease_expires,
                        current,
                        current,
                    ),
                )
                claimed = True
            elif (
                processing["status"] == "processing"
                and processing["lease_expires_at"] <= current
            ):
                connection.execute(
                    """
                    UPDATE event_processing
                    SET claimed_by = ?, lease_expires_at = ?, updated_at = ?
                    WHERE event_id = ? AND status = 'processing'
                      AND lease_expires_at <= ?
                    """,
                    (
                        worker_id,
                        lease_expires,
                        current,
                        receipt.event_id,
                        current,
                    ),
                )
                claimed = True
            connection.commit()
        return EventProcessingClaim(
            event_id=receipt.event_id,
            inserted=receipt.inserted,
            claimed=claimed,
        )

    def get_event(
        self,
        event_id: str,
        *,
        tenant_id: str | None = None,
        business_id: str | None = None,
    ) -> Event | None:
        query = "SELECT * FROM events WHERE event_id = ?"
        parameters: list[str] = [event_id]
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            parameters.append(tenant_id)
        if business_id is not None:
            query += " AND business_id = ?"
            parameters.append(business_id)
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        if row is None:
            return None
        return Event(
            event_id=row["event_id"],
            tenant_id=row["tenant_id"],
            business_id=row["business_id"],
            source=row["source"],
            actor_id=row["actor_id"],
            kind=row["kind"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            payload=json.loads(row["payload_json"]),
            correlation_id=row["correlation_id"],
            idempotency_key=row["idempotency_key"],
        )

    def get_run_for_event(self, event_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE event_id = ?", (event_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def append_audit(
        self,
        *,
        audit_id: str,
        event_id: str | None,
        run_id: str | None,
        tenant_id: str,
        business_id: str,
        record_type: str,
        details: dict[str, Any],
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO audit_records(
                    audit_id, event_id, run_id, tenant_id, business_id,
                    record_type, details_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    event_id,
                    run_id,
                    tenant_id,
                    business_id,
                    record_type,
                    json.dumps(details, sort_keys=True),
                    _utc_now(),
                ),
            )

    def record_outcome(
        self,
        *,
        run_id: str,
        event_id: str,
        tenant_id: str,
        business_id: str,
        action_type: str | None,
        authority_mode: str,
        status: str,
        summary: str,
        audit_id: str,
        audit_type: str,
        audit_details: dict[str, Any],
        processing_worker_id: str,
    ) -> None:
        """Commit a terminal run and its audit record as one transaction."""
        timestamp = _utc_now()
        with self._connection() as connection:
            self._require_business_scope(
                connection,
                tenant_id=tenant_id,
                business_id=business_id,
            )
            claim = connection.execute(
                """
                SELECT 1
                FROM event_processing AS processing
                JOIN events AS event
                  ON event.event_id = processing.event_id
                 AND event.tenant_id = processing.tenant_id
                 AND event.business_id = processing.business_id
                WHERE processing.event_id = ?
                  AND processing.tenant_id = ?
                  AND processing.business_id = ?
                  AND processing.status = 'processing'
                  AND processing.claimed_by = ?
                  AND processing.lease_expires_at > ?
                """,
                (
                    event_id,
                    tenant_id,
                    business_id,
                    processing_worker_id,
                    timestamp,
                ),
            ).fetchone()
            if claim is None:
                raise EventProcessingInProgress(
                    "event processing lease is missing or no longer held"
                )
            connection.execute(
                """
                INSERT INTO workflow_runs(
                    run_id, event_id, tenant_id, business_id, action_type,
                    authority_mode, status, summary, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    event_id,
                    tenant_id,
                    business_id,
                    action_type,
                    authority_mode,
                    status,
                    summary,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                UPDATE event_processing
                SET status = 'completed', updated_at = ?
                WHERE event_id = ? AND claimed_by = ?
                """,
                (timestamp, event_id, processing_worker_id),
            )
            connection.execute(
                """
                INSERT INTO audit_records(
                    audit_id, event_id, run_id, tenant_id, business_id,
                    record_type, details_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    event_id,
                    run_id,
                    tenant_id,
                    business_id,
                    audit_type,
                    json.dumps(audit_details, sort_keys=True),
                    timestamp,
                ),
            )

    def upsert_objective(
        self,
        objective: Objective,
        *,
        next_review_at: datetime | None = None,
    ) -> None:
        review_at = next_review_at or datetime.now(timezone.utc)
        review_at_iso = _utc_iso(review_at)
        timestamp = _utc_now()
        with self._connection() as connection:
            business_row = connection.execute(
                "SELECT tenant_id FROM businesses WHERE business_id = ?",
                (objective.business_id,),
            ).fetchone()
            if (
                business_row is None
                or business_row["tenant_id"] != objective.tenant_id
            ):
                raise ValueError("objective business is outside the tenant")
            existing = connection.execute(
                """
                SELECT tenant_id, business_id
                FROM objectives
                WHERE objective_id = ?
                """,
                (objective.objective_id,),
            ).fetchone()
            if existing is not None and (
                existing["tenant_id"] != objective.tenant_id
                or existing["business_id"] != objective.business_id
            ):
                raise ValueError(
                    "objective identity cannot move across a boundary"
                )
            connection.execute(
                """
                INSERT INTO objectives(
                    objective_id, tenant_id, business_id, statement, metric,
                    target_value, current_value, status, deadline, priority,
                    review_interval_seconds, next_review_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(objective_id) DO UPDATE SET
                    tenant_id=excluded.tenant_id,
                    business_id=excluded.business_id,
                    statement=excluded.statement,
                    metric=excluded.metric,
                    target_value=excluded.target_value,
                    current_value=excluded.current_value,
                    status=excluded.status,
                    deadline=excluded.deadline,
                    priority=excluded.priority,
                    review_interval_seconds=excluded.review_interval_seconds,
                    next_review_at=excluded.next_review_at,
                    updated_at=excluded.updated_at
                """,
                (
                    objective.objective_id,
                    objective.tenant_id,
                    objective.business_id,
                    objective.statement,
                    objective.metric,
                    str(objective.target),
                    str(objective.current_value),
                    objective.status.value,
                    objective.deadline.isoformat() if objective.deadline else None,
                    objective.priority,
                    objective.review_interval_seconds,
                    review_at_iso,
                    timestamp,
                    timestamp,
                ),
            )

    def _objective_from_row(self, row: sqlite3.Row) -> ObjectiveRecord:
        return ObjectiveRecord(
            objective=Objective(
                objective_id=row["objective_id"],
                tenant_id=row["tenant_id"],
                business_id=row["business_id"],
                statement=row["statement"],
                metric=row["metric"],
                target=Decimal(row["target_value"]),
                current_value=Decimal(row["current_value"]),
                status=ObjectiveStatus(row["status"]),
                deadline=(
                    datetime.fromisoformat(row["deadline"])
                    if row["deadline"]
                    else None
                ),
                priority=row["priority"],
                review_interval_seconds=row["review_interval_seconds"],
            ),
            next_review_at=datetime.fromisoformat(row["next_review_at"]),
        )

    def get_objective(self, objective_id: str) -> ObjectiveRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM objectives WHERE objective_id = ?",
                (objective_id,),
            ).fetchone()
        return self._objective_from_row(row) if row is not None else None

    def list_due_objectives(
        self,
        *,
        now: datetime,
        tenant_id: str | None = None,
        business_id: str | None = None,
        limit: int = 50,
    ) -> list[ObjectiveRecord]:
        query = """
            SELECT o.*
            FROM objectives AS o
            WHERE o.status = ?
              AND o.next_review_at <= ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM work_items AS w
                  WHERE w.objective_id = o.objective_id
                    AND w.status IN ('ready', 'claimed', 'awaiting_approval')
              )
        """
        parameters: list[Any] = [
            ObjectiveStatus.ACTIVE.value,
            _utc_iso(now),
        ]
        if tenant_id is not None:
            query += " AND o.tenant_id = ?"
            parameters.append(tenant_id)
        if business_id is not None:
            query += " AND o.business_id = ?"
            parameters.append(business_id)
        query += " ORDER BY o.priority ASC, o.next_review_at ASC LIMIT ?"
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._objective_from_row(row) for row in rows]

    def defer_objective(
        self,
        *,
        objective_id: str,
        tenant_id: str,
        business_id: str,
        next_review_at: datetime,
        record_type: str,
        details: dict[str, Any],
        audit_id: str,
        now: datetime,
    ) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE objectives
                SET next_review_at = ?, updated_at = ?
                WHERE objective_id = ? AND tenant_id = ? AND business_id = ?
                """,
                (
                    _utc_iso(next_review_at),
                    _utc_iso(now),
                    objective_id,
                    tenant_id,
                    business_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("objective does not match its identity boundary")
            connection.execute(
                """
                INSERT INTO audit_records(
                    audit_id, event_id, run_id, tenant_id, business_id,
                    record_type, details_json, created_at
                )
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    objective_id,
                    tenant_id,
                    business_id,
                    record_type,
                    json.dumps(details, sort_keys=True),
                    _utc_iso(now),
                ),
            )

    def enqueue_work_item(
        self,
        *,
        work_item_id: str,
        work_key: str,
        objective_id: str,
        tenant_id: str,
        business_id: str,
        title: str,
        rationale: str,
        action_type: str,
        assigned_actor_id: str,
        platform: str | None,
        account_id: str | None,
        amount: Decimal | None,
        currency: str | None,
        attributes: dict[str, Any],
        authority_mode: str,
        status: str,
        priority_score: int,
        max_attempts: int,
        available_at: datetime,
        next_review_at: datetime,
        audit_id: str,
        approval_expires_at: datetime | None = None,
    ) -> bool:
        """Insert one discovery result and advance its objective atomically."""
        timestamp = _utc_iso(available_at)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO work_items(
                    work_item_id, work_key, objective_id, tenant_id, business_id,
                    title, rationale, action_type, assigned_actor_id, platform,
                    account_id, amount, currency, attributes_json,
                    authority_mode, status, priority_score, attempt_count,
                    max_attempts, available_at, claimed_by, lease_expires_at,
                    last_error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
                        ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    work_item_id,
                    work_key,
                    objective_id,
                    tenant_id,
                    business_id,
                    title,
                    rationale,
                    action_type,
                    assigned_actor_id,
                    platform,
                    account_id,
                    str(amount) if amount is not None else None,
                    currency,
                    json.dumps(attributes, sort_keys=True),
                    authority_mode,
                    status,
                    priority_score,
                    max_attempts,
                    _utc_iso(available_at),
                    timestamp,
                    timestamp,
                ),
            )
            if cursor.rowcount != 1:
                return False
            objective_cursor = connection.execute(
                """
                UPDATE objectives
                SET next_review_at = ?, updated_at = ?
                WHERE objective_id = ? AND tenant_id = ? AND business_id = ?
                """,
                (
                    _utc_iso(next_review_at),
                    timestamp,
                    objective_id,
                    tenant_id,
                    business_id,
                ),
            )
            if objective_cursor.rowcount != 1:
                raise ValueError(
                    "work item objective does not match its identity boundary"
                )
            connection.execute(
                """
                INSERT INTO audit_records(
                    audit_id, event_id, run_id, tenant_id, business_id,
                    record_type, details_json, created_at
                )
                VALUES (?, NULL, ?, ?, ?, 'work.discovered', ?, ?)
                """,
                (
                    audit_id,
                    work_item_id,
                    tenant_id,
                    business_id,
                    json.dumps(
                        {
                            "action_type": action_type,
                            "assigned_actor_id": assigned_actor_id,
                            "authority_mode": authority_mode,
                            "objective_id": objective_id,
                            "status": status,
                            "title": title,
                        },
                        sort_keys=True,
                    ),
                    timestamp,
                ),
            )
            if status == "awaiting_approval":
                work = connection.execute(
                    "SELECT * FROM work_items WHERE work_item_id = ?",
                    (work_item_id,),
                ).fetchone()
                if work is None:
                    raise ValueError("approval-held work was not persisted")
                self._insert_approval_request(
                    connection,
                    work=work,
                    requested_at=available_at,
                    expires_at=(
                        approval_expires_at
                        or available_at + timedelta(hours=24)
                    ),
                )
        return True

    def claim_next_work(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        tenant_id: str | None = None,
        business_id: str | None = None,
        objective_id: str | None = None,
    ) -> dict[str, Any] | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self.expire_work_approvals(now=now)
        lease_expires_at = datetime.fromtimestamp(
            now.timestamp() + lease_seconds,
            tz=timezone.utc,
        )
        now_iso = _utc_iso(now)
        filters = [
            "attempt_count < max_attempts",
            "available_at <= ?",
            "(status = 'ready' OR "
            "(status = 'claimed' AND lease_expires_at <= ?))",
            """
            NOT EXISTS (
                SELECT 1
                FROM emergency_stop_events AS stop
                WHERE stop.tenant_id = work_items.tenant_id
                  AND stop.business_id = work_items.business_id
                  AND stop.rowid = (
                      SELECT latest.rowid
                      FROM emergency_stop_events AS latest
                      WHERE latest.tenant_id = work_items.tenant_id
                        AND latest.business_id = work_items.business_id
                      ORDER BY latest.created_at DESC, latest.rowid DESC
                      LIMIT 1
                  )
                  AND stop.action = 'activated'
            )
            """,
            """
            (
                authority_mode != 'approve'
                OR EXISTS (
                    SELECT 1
                    FROM approval_requests AS request
                    JOIN approval_events AS event
                      ON event.approval_id = request.approval_id
                    WHERE request.work_item_id = work_items.work_item_id
                      AND request.expires_at > ?
                      AND event.rowid = (
                          SELECT latest.rowid
                          FROM approval_events AS latest
                          WHERE latest.approval_id = request.approval_id
                          ORDER BY latest.created_at DESC, latest.rowid DESC
                          LIMIT 1
                      )
                      AND event.decision = 'approved'
                )
            )
            """,
        ]
        parameters: list[Any] = [now_iso, now_iso, now_iso]
        if tenant_id is not None:
            filters.append("tenant_id = ?")
            parameters.append(tenant_id)
        if business_id is not None:
            filters.append("business_id = ?")
            parameters.append(business_id)
        if objective_id is not None:
            filters.append("objective_id = ?")
            parameters.append(objective_id)
        query = (
            "SELECT * FROM work_items WHERE "
            + " AND ".join(filters)
            + " ORDER BY priority_score DESC, created_at ASC LIMIT 1"
        )
        exhausted_filters = [
            "status = 'claimed'",
            "lease_expires_at <= ?",
            "attempt_count >= max_attempts",
        ]
        exhausted_parameters: list[Any] = [now_iso]
        if tenant_id is not None:
            exhausted_filters.append("tenant_id = ?")
            exhausted_parameters.append(tenant_id)
        if business_id is not None:
            exhausted_filters.append("business_id = ?")
            exhausted_parameters.append(business_id)
        exhausted_query = (
            """
            SELECT work_item_id, tenant_id, business_id, attempt_count
            FROM work_items
            WHERE
            """
            + " AND ".join(exhausted_filters)
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            exhausted = connection.execute(
                exhausted_query,
                exhausted_parameters,
            ).fetchall()
            for expired in exhausted:
                connection.execute(
                    """
                    UPDATE work_items
                    SET status = 'failed', claimed_by = NULL,
                        lease_expires_at = NULL,
                        last_error = 'lease expired after final attempt',
                        updated_at = ?
                    WHERE work_item_id = ? AND status = 'claimed'
                    """,
                    (now_iso, expired["work_item_id"]),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO audit_records(
                        audit_id, event_id, run_id, tenant_id, business_id,
                        record_type, details_json, created_at
                    )
                    VALUES (?, NULL, ?, ?, ?, 'work.failed', ?, ?)
                    """,
                    (
                        (
                            f"audit-lease-{expired['work_item_id']}-"
                            f"{expired['attempt_count']}"
                        ),
                        expired["work_item_id"],
                        expired["tenant_id"],
                        expired["business_id"],
                        json.dumps(
                            {
                                "attempt_count": expired["attempt_count"],
                                "error": "lease expired after final attempt",
                                "status": "failed",
                            },
                            sort_keys=True,
                        ),
                        now_iso,
                    ),
                )
            row = connection.execute(query, parameters).fetchone()
            if row is None:
                connection.commit()
                return None
            cursor = connection.execute(
                """
                UPDATE work_items
                SET status = 'claimed',
                    claimed_by = ?,
                    lease_expires_at = ?,
                    attempt_count = attempt_count + 1,
                    updated_at = ?
                WHERE work_item_id = ?
                  AND (
                      status = 'ready'
                      OR (status = 'claimed' AND lease_expires_at <= ?)
                  )
                """,
                (
                    worker_id,
                    _utc_iso(lease_expires_at),
                    now_iso,
                    row["work_item_id"],
                    now_iso,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            claimed = connection.execute(
                "SELECT * FROM work_items WHERE work_item_id = ?",
                (row["work_item_id"],),
            ).fetchone()
            if row["status"] == "claimed":
                connection.execute(
                    """
                    INSERT INTO audit_records(
                        audit_id, event_id, run_id, tenant_id, business_id,
                        record_type, details_json, created_at
                    )
                    VALUES (?, NULL, ?, ?, ?, 'work.lease_recovered', ?, ?)
                    """,
                    (
                        (
                            f"audit-recovery-{row['work_item_id']}-"
                            f"{claimed['attempt_count']}"
                        ),
                        row["work_item_id"],
                        row["tenant_id"],
                        row["business_id"],
                        json.dumps(
                            {
                                "attempt_count": claimed["attempt_count"],
                                "worker_id": worker_id,
                            },
                            sort_keys=True,
                        ),
                        now_iso,
                    ),
                )
            connection.commit()
            return self._work_row_to_dict(claimed)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def resolve_claimed_work(
        self,
        *,
        work_item_id: str,
        worker_id: str,
        status: str,
        authority_mode: str,
        record_type: str,
        details: dict[str, Any],
        audit_id: str,
        now: datetime,
    ) -> bool:
        """Resolve a lease and write its audit record in one transaction."""
        allowed_statuses = {
            "awaiting_approval",
            "rejected",
            "simulated",
        }
        if status not in allowed_statuses:
            raise ValueError(
                "generic work resolution cannot assert execution or "
                "verification truth"
            )
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM work_items
                WHERE work_item_id = ?
                  AND status = 'claimed'
                  AND claimed_by = ?
                  AND lease_expires_at > ?
                """,
                (work_item_id, worker_id, _utc_iso(now)),
            ).fetchone()
            if row is None:
                return False
            if status == "simulated":
                if self._is_emergency_stop_active_in_connection(
                    connection,
                    tenant_id=row["tenant_id"],
                    business_id=row["business_id"],
                ):
                    raise ValueError(
                        "emergency stop blocks work resolution"
                    )
                if (
                    row["authority_mode"] == AuthorityMode.APPROVE.value
                    and not self._has_valid_work_approval_in_connection(
                        connection,
                        work=row,
                        now=now,
                    )
                ):
                    raise ValueError(
                        "approval-required work lacks a current approval"
                    )
            connection.execute(
                """
                UPDATE work_items
                SET status = ?, authority_mode = ?, claimed_by = NULL,
                    lease_expires_at = NULL, last_error = NULL, updated_at = ?
                WHERE work_item_id = ?
                """,
                (status, authority_mode, _utc_iso(now), work_item_id),
            )
            connection.execute(
                """
                INSERT INTO audit_records(
                    audit_id, event_id, run_id, tenant_id, business_id,
                    record_type, details_json, created_at
                )
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    work_item_id,
                    row["tenant_id"],
                    row["business_id"],
                    record_type,
                    json.dumps(details, sort_keys=True),
                    _utc_iso(now),
                ),
            )
        return True

    def fail_claimed_work(
        self,
        *,
        work_item_id: str,
        worker_id: str,
        error: str,
        retry_at: datetime,
        audit_id: str,
        now: datetime,
    ) -> str:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT tenant_id, business_id, attempt_count, max_attempts
                FROM work_items
                WHERE work_item_id = ?
                  AND status = 'claimed'
                  AND claimed_by = ?
                  AND lease_expires_at > ?
                """,
                (work_item_id, worker_id, _utc_iso(now)),
            ).fetchone()
            if row is None:
                raise ValueError("worker does not hold the work-item lease")
            next_status = (
                "failed"
                if row["attempt_count"] >= row["max_attempts"]
                else "ready"
            )
            connection.execute(
                """
                UPDATE work_items
                SET status = ?, available_at = ?, claimed_by = NULL,
                    lease_expires_at = NULL, last_error = ?, updated_at = ?
                WHERE work_item_id = ?
                """,
                (
                    next_status,
                    _utc_iso(retry_at),
                    error,
                    _utc_iso(now),
                    work_item_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_records(
                    audit_id, event_id, run_id, tenant_id, business_id,
                    record_type, details_json, created_at
                )
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    work_item_id,
                    row["tenant_id"],
                    row["business_id"],
                    (
                        "work.failed"
                        if next_status == "failed"
                        else "work.retry_scheduled"
                    ),
                    json.dumps(
                        {
                            "attempt_count": row["attempt_count"],
                            "error": error,
                            "max_attempts": row["max_attempts"],
                            "status": next_status,
                        },
                        sort_keys=True,
                    ),
                    _utc_iso(now),
                ),
            )
        return next_status

    def _work_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["attributes"] = json.loads(result.pop("attributes_json"))
        result["amount"] = (
            Decimal(result["amount"]) if result["amount"] is not None else None
        )
        return result

    def get_work_item(self, work_item_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM work_items WHERE work_item_id = ?",
                (work_item_id,),
            ).fetchone()
        return self._work_row_to_dict(row) if row is not None else None

    def _insert_evidence_receipt(
        self,
        connection: sqlite3.Connection,
        receipt: dict[str, Any],
    ) -> bool:
        computed_hash = _evidence_receipt_hash(receipt)
        if computed_hash != receipt["content_hash"]:
            raise ValueError(
                "evidence receipt content hash does not match its payload"
            )
        if receipt["evidence_kind"] in {
            "precondition",
            "external_readback",
            "machine_check",
        }:
            issuer = connection.execute(
                """
                SELECT 1
                FROM evidence_issuers AS issuer
                JOIN actors AS actor
                  ON actor.actor_id = issuer.actor_id
                 AND actor.tenant_id = issuer.tenant_id
                 AND actor.enabled = 1
                JOIN json_each(actor.business_ids_json) AS membership
                  ON membership.value = issuer.business_id
                WHERE issuer.tenant_id = ? AND issuer.business_id = ?
                  AND issuer.source_system = ? AND issuer.evidence_kind = ?
                  AND issuer.actor_id = ? AND issuer.issuer_version = ?
                  AND issuer.enabled = 1
                """,
                (
                    receipt["tenant_id"],
                    receipt["business_id"],
                    receipt["source_system"],
                    receipt["evidence_kind"],
                    receipt["captured_by"],
                    receipt["issuer_version"],
                ),
            ).fetchone()
            if issuer is None:
                raise ValueError(
                    "evidence issuer is not registered for this scope and kind"
                )
        existing = connection.execute(
            """
            SELECT receipt_id, work_item_id, attempt_id, tenant_id,
                   business_id, content_hash
            FROM evidence_receipts
            WHERE receipt_id = ?
            """,
            (receipt["receipt_id"],),
        ).fetchone()
        if existing is not None:
            same_identity = (
                existing["work_item_id"] == receipt["work_item_id"]
                and existing["attempt_id"] == receipt["attempt_id"]
                and existing["tenant_id"] == receipt["tenant_id"]
                and existing["business_id"] == receipt["business_id"]
                and existing["content_hash"] == receipt["content_hash"]
            )
            if not same_identity:
                raise ValueError(
                    "evidence receipt ID was reused with different content"
                )
            return False
        connection.execute(
            """
            INSERT INTO evidence_receipts(
                receipt_id, work_item_id, attempt_id, tenant_id, business_id,
                evidence_kind, source_system, source_ref, captured_by,
                observed_at, valid_until, payload_json, content_hash,
                created_at, issuer_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt["receipt_id"],
                receipt["work_item_id"],
                receipt["attempt_id"],
                receipt["tenant_id"],
                receipt["business_id"],
                receipt["evidence_kind"],
                receipt["source_system"],
                receipt["source_ref"],
                receipt["captured_by"],
                _utc_iso(receipt["observed_at"]),
                _utc_iso(receipt["valid_until"]),
                json.dumps(receipt["payload"], sort_keys=True),
                receipt["content_hash"],
                _utc_iso(receipt["created_at"]),
                receipt["issuer_version"],
            ),
        )
        return True

    def register_evidence_issuer(
        self,
        *,
        tenant_id: str,
        business_id: str,
        source_system: str,
        evidence_kind: str,
        actor_id: str,
        issuer_version: str,
        enabled: bool = True,
    ) -> None:
        if evidence_kind not in {
            "precondition",
            "external_readback",
            "machine_check",
        }:
            raise ValueError("issuer kind is not authoritative evidence")
        if not issuer_version.strip():
            raise ValueError("issuer version is required")
        with self._connection() as connection:
            self._require_business_scope(
                connection,
                tenant_id=tenant_id,
                business_id=business_id,
            )
            actor = connection.execute(
                """
                SELECT tenant_id, business_ids_json, enabled
                FROM actors
                WHERE actor_id = ?
                """,
                (actor_id,),
            ).fetchone()
            if (
                actor is None
                or actor["tenant_id"] != tenant_id
                or business_id
                not in json.loads(actor["business_ids_json"])
                or not actor["enabled"]
            ):
                raise ValueError("evidence issuer actor is outside the scope")
            connection.execute(
                """
                INSERT INTO evidence_issuers(
                    tenant_id, business_id, source_system, evidence_kind,
                    actor_id, issuer_version, enabled
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    tenant_id, business_id, source_system,
                    evidence_kind, actor_id
                ) DO UPDATE SET
                    issuer_version = excluded.issuer_version,
                    enabled = excluded.enabled
                """,
                (
                    tenant_id,
                    business_id,
                    source_system,
                    evidence_kind,
                    actor_id,
                    issuer_version,
                    int(enabled),
                ),
            )

    def record_precondition_receipt(
        self,
        receipt: dict[str, Any],
    ) -> bool:
        """Persist an immutable pre-execution state observation."""
        if (
            receipt["evidence_kind"] != "precondition"
            or receipt["attempt_id"] is not None
        ):
            raise ValueError(
                "precondition evidence must precede and not reference an attempt"
            )
        with self._connection() as connection:
            work = connection.execute(
                """
                SELECT 1
                FROM work_items
                WHERE work_item_id = ? AND tenant_id = ? AND business_id = ?
                """,
                (
                    receipt["work_item_id"],
                    receipt["tenant_id"],
                    receipt["business_id"],
                ),
            ).fetchone()
            if work is None:
                raise ValueError("receipt is outside the work identity boundary")
            return self._insert_evidence_receipt(connection, receipt)

    def get_evidence_receipt(
        self,
        receipt_id: str,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM evidence_receipts WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
        if row is None:
            return None
        return self._evidence_receipt_row_to_dict(row)

    def _evidence_receipt_row_to_dict(
        self,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        for field in ("observed_at", "valid_until", "created_at"):
            result[field] = datetime.fromisoformat(result[field])
        return result

    def _assert_evidence_receipt_integrity(
        self,
        receipt: dict[str, Any],
    ) -> None:
        if _evidence_receipt_hash(receipt) != receipt["content_hash"]:
            raise ValueError(
                "evidence receipt content hash does not match its payload"
            )

    def begin_execution_attempt(
        self,
        *,
        attempt: dict[str, Any],
        worker_id: str,
    ) -> bool:
        """Move live claimed work to attempted exactly once."""
        attempted_at = _utc_iso(attempt["attempted_at"])
        with self._immediate_connection() as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM execution_attempts
                WHERE attempt_id = ?
                   OR (
                       tenant_id = ? AND business_id = ?
                       AND idempotency_key = ?
                   )
                """,
                (
                    attempt["attempt_id"],
                    attempt["tenant_id"],
                    attempt["business_id"],
                    attempt["idempotency_key"],
                ),
            ).fetchone()
            if existing is not None:
                exact_replay = (
                    existing["attempt_id"] == attempt["attempt_id"]
                    and existing["work_item_id"] == attempt["work_item_id"]
                    and existing["producer_id"] == attempt["producer_id"]
                    and existing["execution_mode"] == attempt["execution_mode"]
                    and existing["action_type"] == attempt["action_type"]
                    and existing["target_ref"] == attempt["target_ref"]
                    and existing["precondition_receipt_id"]
                    == attempt["precondition_receipt_id"]
                    and existing["summary"] == attempt["summary"]
                    and existing["attempted_at"] == attempted_at
                )
                if not exact_replay:
                    raise ValueError(
                        "execution attempt identity or idempotency key conflict"
                    )
                return False

            if attempt["execution_mode"] == "external":
                precondition_row = connection.execute(
                    """
                    SELECT *
                    FROM evidence_receipts
                    WHERE receipt_id = ?
                    """,
                    (attempt["precondition_receipt_id"],),
                ).fetchone()
                if precondition_row is None:
                    raise ValueError(
                        "external attempt requires a precondition receipt"
                    )
                precondition = self._evidence_receipt_row_to_dict(
                    precondition_row
                )
                self._assert_evidence_receipt_integrity(precondition)
                if (
                    precondition["evidence_kind"] != "precondition"
                    or precondition["attempt_id"] is not None
                    or precondition["work_item_id"]
                    != attempt["work_item_id"]
                    or precondition["tenant_id"] != attempt["tenant_id"]
                    or precondition["business_id"]
                    != attempt["business_id"]
                    or precondition["source_ref"] != attempt["target_ref"]
                    or precondition["observed_at"]
                    > attempt["attempted_at"]
                    or precondition["valid_until"]
                    <= attempt["attempted_at"]
                ):
                    raise ValueError(
                        "external attempt precondition is stale or mismatched"
                    )
                issuer = connection.execute(
                    """
                    SELECT 1
                    FROM evidence_issuers AS issuer
                    JOIN actors AS actor
                      ON actor.actor_id = issuer.actor_id
                     AND actor.tenant_id = issuer.tenant_id
                     AND actor.enabled = 1
                    JOIN json_each(actor.business_ids_json) AS membership
                      ON membership.value = issuer.business_id
                    WHERE issuer.tenant_id = ? AND issuer.business_id = ?
                      AND issuer.source_system = ?
                      AND issuer.evidence_kind = ?
                      AND issuer.actor_id = ? AND issuer.issuer_version = ?
                      AND issuer.enabled = 1
                    """,
                    (
                        precondition["tenant_id"],
                        precondition["business_id"],
                        precondition["source_system"],
                        precondition["evidence_kind"],
                        precondition["captured_by"],
                        precondition["issuer_version"],
                    ),
                ).fetchone()
                if issuer is None:
                    raise ValueError(
                        "external attempt precondition issuer is no longer trusted"
                    )

            work = connection.execute(
                """
                SELECT *
                FROM work_items
                WHERE work_item_id = ?
                  AND tenant_id = ?
                  AND business_id = ?
                  AND status = 'claimed'
                  AND claimed_by = ?
                  AND lease_expires_at > ?
                """,
                (
                    attempt["work_item_id"],
                    attempt["tenant_id"],
                    attempt["business_id"],
                    worker_id,
                    attempted_at,
                ),
            ).fetchone()
            if work is None:
                raise ValueError(
                    "worker does not hold a live lease for the attempted work"
                )
            if self._is_emergency_stop_active_in_connection(
                connection,
                tenant_id=attempt["tenant_id"],
                business_id=attempt["business_id"],
            ):
                raise ValueError(
                    "cannot begin an execution attempt while emergency stop "
                    "is active"
                )
            if (
                work["action_type"] != attempt["action_type"]
                or work["assigned_actor_id"] != attempt["producer_id"]
            ):
                raise ValueError(
                    "attempt actor or action does not match the claimed work"
                )
            current_authority = self._decide_authority_in_connection(
                connection,
                ActionRequest(
                    action_type=work["action_type"],
                    tenant_id=work["tenant_id"],
                    business_id=work["business_id"],
                    actor_id=work["assigned_actor_id"],
                    platform=work["platform"],
                    account_id=work["account_id"],
                    amount=(
                        Decimal(work["amount"])
                        if work["amount"] is not None
                        else None
                    ),
                    currency=work["currency"],
                    attributes=json.loads(work["attributes_json"]),
                ),
                now=attempt["attempted_at"],
            )
            if current_authority.value != work["authority_mode"]:
                raise ValueError(
                    "work authority is stale at the execution boundary"
                )
            if (
                current_authority is AuthorityMode.APPROVE
                and not self._has_valid_work_approval_in_connection(
                    connection,
                    work=work,
                    now=attempt["attempted_at"],
                )
            ):
                raise ValueError(
                    "approval-held work requires a current durable approval"
                )
            if (
                attempt["execution_mode"] == "external"
                and is_prohibited_financial_action(attempt["action_type"])
            ):
                raise ValueError(
                    "external money movement and financial commitments "
                    "are forbidden"
                )
            if (
                attempt["execution_mode"] == "external"
                and requires_spend_envelope(attempt["action_type"])
            ):
                self._reserve_spend_commitment_in_connection(
                    connection,
                    work=work,
                    attempt_id=attempt["attempt_id"],
                    now=attempt["attempted_at"],
                )
            connection.execute(
                """
                INSERT INTO execution_attempts(
                    attempt_id, work_item_id, tenant_id, business_id,
                    producer_id, execution_mode, action_type, target_ref,
                    idempotency_key, precondition_receipt_id, status, summary,
                    attempted_at, observed_at, updated_at,
                    reconciliation_available_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'attempted', ?, ?,
                        NULL, ?, ?)
                """,
                (
                    attempt["attempt_id"],
                    attempt["work_item_id"],
                    attempt["tenant_id"],
                    attempt["business_id"],
                    attempt["producer_id"],
                    attempt["execution_mode"],
                    attempt["action_type"],
                    attempt["target_ref"],
                    attempt["idempotency_key"],
                    attempt["precondition_receipt_id"],
                    attempt["summary"],
                    attempted_at,
                    attempted_at,
                    attempted_at,
                ),
            )
            connection.execute(
                """
                UPDATE work_items
                SET status = 'attempted', claimed_by = NULL,
                    lease_expires_at = NULL, last_error = NULL, updated_at = ?
                WHERE work_item_id = ?
                """,
                (attempted_at, attempt["work_item_id"]),
            )
            connection.execute(
                """
                INSERT INTO audit_records(
                    audit_id, event_id, run_id, tenant_id, business_id,
                    record_type, details_json, created_at
                )
                VALUES (?, NULL, ?, ?, ?, 'execution.attempted', ?, ?)
                """,
                (
                    f"audit-attempt-{attempt['attempt_id']}",
                    attempt["work_item_id"],
                    attempt["tenant_id"],
                    attempt["business_id"],
                    json.dumps(
                        {
                            "attempt_id": attempt["attempt_id"],
                            "execution_mode": attempt["execution_mode"],
                            "producer_id": attempt["producer_id"],
                            "status": "attempted",
                            "target_ref": attempt["target_ref"],
                        },
                        sort_keys=True,
                    ),
                    attempted_at,
                ),
            )
        return True

    def get_execution_attempt(
        self,
        attempt_id: str,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM execution_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for field in ("attempted_at", "observed_at", "updated_at"):
            if result[field] is not None:
                result[field] = datetime.fromisoformat(result[field])
        return result

    def claim_uncertain_attempt(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int = 300,
        tenant_id: str | None = None,
        business_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Claim an attempted-but-unobserved write for read-only reconciliation."""
        if lease_seconds < 1:
            raise ValueError("reconciliation lease must be positive")
        current = _utc_iso(now)
        lease_expires = _utc_iso(
            datetime.fromtimestamp(
                now.timestamp() + lease_seconds,
                tz=timezone.utc,
            )
        )
        filters = [
            "reconciliation_attempt_count < reconciliation_max_attempts",
            "COALESCE(reconciliation_available_at, attempted_at) <= ?",
            "(status = 'attempted' OR "
            "(status = 'reconciling' "
            "AND reconciliation_lease_expires_at <= ?))",
        ]
        parameters: list[Any] = [current, current]
        if tenant_id is not None:
            filters.append("tenant_id = ?")
            parameters.append(tenant_id)
        if business_id is not None:
            filters.append("business_id = ?")
            parameters.append(business_id)
        query = (
            "SELECT * FROM execution_attempts WHERE "
            + " AND ".join(filters)
            + " ORDER BY attempted_at, attempt_id LIMIT 1"
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exhausted_filters = [
                "status = 'reconciling'",
                "reconciliation_lease_expires_at <= ?",
                "reconciliation_attempt_count >= reconciliation_max_attempts",
            ]
            exhausted_parameters: list[Any] = [current]
            if tenant_id is not None:
                exhausted_filters.append("tenant_id = ?")
                exhausted_parameters.append(tenant_id)
            if business_id is not None:
                exhausted_filters.append("business_id = ?")
                exhausted_parameters.append(business_id)
            exhausted = connection.execute(
                "SELECT * FROM execution_attempts WHERE "
                + " AND ".join(exhausted_filters),
                exhausted_parameters,
            ).fetchall()
            for row in exhausted:
                failure_message = (
                    "reconciliation lease expired after final attempt"
                )
                connection.execute(
                    """
                    UPDATE execution_attempts
                    SET status = 'reconciliation_failed',
                        reconciliation_claimed_by = NULL,
                        reconciliation_lease_expires_at = NULL,
                        reconciliation_last_error = ?,
                        updated_at = ?
                    WHERE attempt_id = ?
                    """,
                    (failure_message, current, row["attempt_id"]),
                )
                connection.execute(
                    """
                    UPDATE work_items
                    SET status = 'reconciliation_failed',
                        last_error = ?,
                        updated_at = ?
                    WHERE work_item_id = ?
                    """,
                    (failure_message, current, row["work_item_id"]),
                )
                connection.execute(
                    """
                    INSERT INTO audit_records(
                        audit_id, event_id, run_id, tenant_id, business_id,
                        record_type, details_json, created_at
                    )
                    VALUES (?, NULL, ?, ?, ?,
                            'execution.reconciliation_failed', ?, ?)
                    """,
                    (
                        f"audit-reconcile-expired-{row['attempt_id']}",
                        row["work_item_id"],
                        row["tenant_id"],
                        row["business_id"],
                        json.dumps(
                            {
                                "attempt_id": row["attempt_id"],
                                "error": failure_message,
                            },
                            sort_keys=True,
                        ),
                        current,
                    ),
                )
            row = connection.execute(query, parameters).fetchone()
            if row is None:
                return None
            cursor = connection.execute(
                """
                UPDATE execution_attempts
                SET status = 'reconciling',
                    reconciliation_claimed_by = ?,
                    reconciliation_lease_expires_at = ?,
                    reconciliation_attempt_count =
                        reconciliation_attempt_count + 1,
                    updated_at = ?
                WHERE attempt_id = ?
                  AND (
                      status = 'attempted'
                      OR (
                          status = 'reconciling'
                          AND reconciliation_lease_expires_at <= ?
                      )
                  )
                """,
                (
                    worker_id,
                    lease_expires,
                    current,
                    row["attempt_id"],
                    current,
                ),
            )
            if cursor.rowcount != 1:
                return None
            connection.execute(
                """
                UPDATE work_items
                SET status = 'reconciling', updated_at = ?
                WHERE work_item_id = ?
                """,
                (current, row["work_item_id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM execution_attempts WHERE attempt_id = ?",
                (row["attempt_id"],),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO audit_records(
                    audit_id, event_id, run_id, tenant_id, business_id,
                    record_type, details_json, created_at
                )
                VALUES (?, NULL, ?, ?, ?, 'execution.reconciliation_claimed',
                        ?, ?)
                """,
                (
                    (
                        f"audit-reconcile-{row['attempt_id']}-"
                        f"{claimed['reconciliation_attempt_count']}"
                    ),
                    row["work_item_id"],
                    row["tenant_id"],
                    row["business_id"],
                    json.dumps(
                        {
                            "attempt_id": row["attempt_id"],
                            "worker_id": worker_id,
                        },
                        sort_keys=True,
                    ),
                    current,
                ),
            )
        return self.get_execution_attempt(row["attempt_id"])

    def fail_attempt_reconciliation(
        self,
        *,
        attempt_id: str,
        worker_id: str,
        error: str,
        retry_at: datetime,
        now: datetime,
    ) -> str:
        """Release a reconciliation lease without retrying the external write."""
        current = _utc_iso(now)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM execution_attempts
                WHERE attempt_id = ? AND status = 'reconciling'
                  AND reconciliation_claimed_by = ?
                  AND reconciliation_lease_expires_at > ?
                """,
                (attempt_id, worker_id, current),
            ).fetchone()
            if row is None:
                raise ValueError(
                    "worker does not hold the attempt reconciliation lease"
                )
            status = (
                "reconciliation_failed"
                if row["reconciliation_attempt_count"]
                >= row["reconciliation_max_attempts"]
                else "attempted"
            )
            connection.execute(
                """
                UPDATE execution_attempts
                SET status = ?, reconciliation_available_at = ?,
                    reconciliation_claimed_by = NULL,
                    reconciliation_lease_expires_at = NULL,
                    reconciliation_last_error = ?, updated_at = ?
                WHERE attempt_id = ?
                """,
                (
                    status,
                    _utc_iso(retry_at),
                    error,
                    current,
                    attempt_id,
                ),
            )
            connection.execute(
                """
                UPDATE work_items
                SET status = ?, last_error = ?, updated_at = ?
                WHERE work_item_id = ?
                """,
                (status, error, current, row["work_item_id"]),
            )
            connection.execute(
                """
                INSERT INTO audit_records(
                    audit_id, event_id, run_id, tenant_id, business_id,
                    record_type, details_json, created_at
                )
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        f"audit-reconcile-failure-{attempt_id}-"
                        f"{row['reconciliation_attempt_count']}"
                    ),
                    row["work_item_id"],
                    row["tenant_id"],
                    row["business_id"],
                    (
                        "execution.reconciliation_failed"
                        if status == "reconciliation_failed"
                        else "execution.reconciliation_retry_scheduled"
                    ),
                    json.dumps(
                        {
                            "attempt_id": attempt_id,
                            "error": error,
                            "status": status,
                        },
                        sort_keys=True,
                    ),
                    current,
                ),
            )
        return status

    def attach_outcome_receipt(
        self,
        receipt: dict[str, Any],
    ) -> bool:
        """Atomically attach read-back evidence and mark an attempt observed."""
        with self._connection() as connection:
            attempt = connection.execute(
                """
                SELECT work_item_id, tenant_id, business_id, status,
                       target_ref, attempted_at
                FROM execution_attempts
                WHERE attempt_id = ?
                """,
                (receipt["attempt_id"],),
            ).fetchone()
            if attempt is None:
                raise ValueError("receipt references an unknown attempt")
            if (
                attempt["work_item_id"] != receipt["work_item_id"]
                or attempt["tenant_id"] != receipt["tenant_id"]
                or attempt["business_id"] != receipt["business_id"]
            ):
                raise ValueError("receipt crosses the attempt identity boundary")
            if receipt["source_ref"] != attempt["target_ref"]:
                raise ValueError(
                    "receipt source does not match the attempted target"
                )
            if (
                receipt["observed_at"]
                < datetime.fromisoformat(attempt["attempted_at"])
                or receipt["valid_until"] <= receipt["observed_at"]
            ):
                raise ValueError(
                    "post-attempt evidence has an invalid observation time"
                )
            inserted = self._insert_evidence_receipt(connection, receipt)
            if not inserted:
                return False
            if receipt["evidence_kind"] in {
                "external_readback",
                "machine_check",
            }:
                if attempt["status"] in {"verified", "disproved"}:
                    raise ValueError(
                        "terminal attempt evidence cannot be appended"
                    )
                observed_at = _utc_iso(receipt["observed_at"])
                connection.execute(
                    """
                    UPDATE execution_attempts
                    SET status = 'observed', observed_at = ?, updated_at = ?,
                        reconciliation_claimed_by = NULL,
                        reconciliation_lease_expires_at = NULL,
                        reconciliation_last_error = NULL
                    WHERE attempt_id = ?
                    """,
                    (observed_at, observed_at, receipt["attempt_id"]),
                )
                connection.execute(
                    """
                    UPDATE work_items
                    SET status = 'observed', updated_at = ?
                    WHERE work_item_id = ?
                    """,
                    (observed_at, receipt["work_item_id"]),
                )
                connection.execute(
                    """
                    INSERT INTO audit_records(
                        audit_id, event_id, run_id, tenant_id, business_id,
                        record_type, details_json, created_at
                    )
                    VALUES (?, NULL, ?, ?, ?, 'execution.observed', ?, ?)
                    """,
                    (
                        f"audit-observation-{receipt['receipt_id']}",
                        receipt["work_item_id"],
                        receipt["tenant_id"],
                        receipt["business_id"],
                        json.dumps(
                            {
                                "attempt_id": receipt["attempt_id"],
                                "evidence_kind": receipt["evidence_kind"],
                                "receipt_id": receipt["receipt_id"],
                                "status": "observed",
                            },
                            sort_keys=True,
                        ),
                        observed_at,
                    ),
                )
        return True

    def list_attempt_receipts(
        self,
        attempt_id: str,
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM evidence_receipts
                WHERE attempt_id = ?
                ORDER BY observed_at, receipt_id
                """,
                (attempt_id,),
            ).fetchall()
        receipts = []
        for row in rows:
            receipts.append(self._evidence_receipt_row_to_dict(row))
        return receipts

    def record_outcome_verification(
        self,
        verification: dict[str, Any],
    ) -> bool:
        """Atomically persist a verification decision and final truth state."""
        decided_at = _utc_iso(verification["decided_at"])
        with self._immediate_connection() as connection:
            existing = connection.execute(
                """
                SELECT attempt_id, verifier_id, decision,
                       evidence_receipt_ids_json, expected_facts_json,
                       rationale, policy_version, decided_at
                FROM outcome_verifications
                WHERE verification_id = ?
                """,
                (verification["verification_id"],),
            ).fetchone()
            receipt_ids_json = json.dumps(
                verification["evidence_receipt_ids"],
                sort_keys=True,
            )
            expected_facts_json = json.dumps(
                verification["expected_facts"],
                sort_keys=True,
            )
            if existing is not None:
                exact_replay = (
                    existing["attempt_id"] == verification["attempt_id"]
                    and existing["verifier_id"] == verification["verifier_id"]
                    and existing["decision"] == verification["decision"]
                    and existing["evidence_receipt_ids_json"]
                    == receipt_ids_json
                    and existing["expected_facts_json"]
                    == expected_facts_json
                    and existing["rationale"] == verification["rationale"]
                    and existing["policy_version"]
                    == verification["policy_version"]
                    and existing["decided_at"] == decided_at
                )
                if not exact_replay:
                    raise ValueError(
                        "verification ID was reused with a different decision"
                    )
                return False
            attempt = connection.execute(
                """
                SELECT *
                FROM execution_attempts
                WHERE attempt_id = ?
                """,
                (verification["attempt_id"],),
            ).fetchone()
            if attempt is None:
                raise ValueError("verification references an unknown attempt")
            if (
                attempt["work_item_id"] != verification["work_item_id"]
                or attempt["tenant_id"] != verification["tenant_id"]
                or attempt["business_id"] != verification["business_id"]
            ):
                raise ValueError(
                    "verification crosses the attempt identity boundary"
                )
            work, objective = self._completion_context(
                connection,
                verification["work_item_id"],
            )
            if (
                work["action_type"] != attempt["action_type"]
                or work["assigned_actor_id"] != attempt["producer_id"]
                or work["tenant_id"] != attempt["tenant_id"]
                or work["business_id"] != attempt["business_id"]
            ):
                raise ValueError(
                    "verification work semantics no longer match the attempt"
                )
            if attempt["status"] in {"verified", "disproved"}:
                raise ValueError(
                    "terminal verification decisions are immutable"
                )
            decision = verification["decision"]
            if decision not in {"verified", "disproved", "inconclusive"}:
                raise ValueError("unknown verification decision")
            if attempt["status"] != "observed":
                raise ValueError(
                    "attempt must be externally observed before verification"
                )
            if decision == "verified" and attempt["execution_mode"] != "external":
                raise ValueError(
                    "simulated execution cannot become verified completion"
                )
            verifier = connection.execute(
                """
                SELECT tenant_id, roles_json, business_ids_json, enabled
                FROM actors
                WHERE actor_id = ?
                """,
                (verification["verifier_id"],),
            ).fetchone()
            if (
                verifier is None
                or not verifier["enabled"]
                or verifier["tenant_id"] != attempt["tenant_id"]
                or attempt["business_id"]
                not in json.loads(verifier["business_ids_json"])
                or verification["verifier_id"] == attempt["producer_id"]
                or not (
                    {"qa", "qa-verifier", "verifier"}
                    & set(json.loads(verifier["roles_json"]))
                )
            ):
                raise ValueError(
                    "verifier is not independent and authorized in this scope"
                )
            receipt_ids = verification["evidence_receipt_ids"]
            if not receipt_ids or len(receipt_ids) != len(set(receipt_ids)):
                raise ValueError(
                    "verification requires unique evidence receipt IDs"
                )
            placeholders = ",".join("?" for _ in receipt_ids)
            receipt_rows = connection.execute(
                f"""
                SELECT *
                FROM evidence_receipts
                WHERE receipt_id IN ({placeholders})
                """,
                receipt_ids,
            ).fetchall()
            if len(receipt_rows) != len(receipt_ids):
                raise ValueError("verification evidence is missing")
            receipts = [
                self._evidence_receipt_row_to_dict(row)
                for row in receipt_rows
            ]
            decision_time = verification["decided_at"]
            attempted_at = datetime.fromisoformat(attempt["attempted_at"])
            observed_result: dict[str, Any] = {}
            for receipt in receipts:
                self._assert_evidence_receipt_integrity(receipt)
                if (
                    receipt["attempt_id"] != attempt["attempt_id"]
                    or receipt["work_item_id"] != attempt["work_item_id"]
                    or receipt["tenant_id"] != attempt["tenant_id"]
                    or receipt["business_id"] != attempt["business_id"]
                    or receipt["source_ref"] != attempt["target_ref"]
                    or receipt["evidence_kind"]
                    not in {"external_readback", "machine_check"}
                    or receipt["observed_at"] < attempted_at
                    or receipt["observed_at"] > decision_time
                    or receipt["valid_until"] <= decision_time
                ):
                    raise ValueError(
                        "verification evidence is stale, untrusted, or "
                        "outside the attempted target"
                    )
                issuer = connection.execute(
                    """
                    SELECT 1
                    FROM evidence_issuers AS issuer
                    JOIN actors AS actor
                      ON actor.actor_id = issuer.actor_id
                     AND actor.tenant_id = issuer.tenant_id
                     AND actor.enabled = 1
                    JOIN json_each(actor.business_ids_json) AS membership
                      ON membership.value = issuer.business_id
                    WHERE issuer.tenant_id = ? AND issuer.business_id = ?
                      AND issuer.source_system = ?
                      AND issuer.evidence_kind = ?
                      AND issuer.actor_id = ? AND issuer.issuer_version = ?
                      AND issuer.enabled = 1
                    """,
                    (
                        receipt["tenant_id"],
                        receipt["business_id"],
                        receipt["source_system"],
                        receipt["evidence_kind"],
                        receipt["captured_by"],
                        receipt["issuer_version"],
                    ),
                ).fetchone()
                if issuer is None:
                    raise ValueError(
                        "verification evidence issuer is no longer trusted"
                    )
                for key, value in receipt["payload"].items():
                    if key in observed_result and observed_result[key] != value:
                        raise ValueError(
                            "authoritative receipts contradict each other"
                        )
                    observed_result[key] = value

            latest_rows = connection.execute(
                """
                SELECT receipt.receipt_id, receipt.source_system,
                       receipt.evidence_kind, receipt.captured_by,
                       receipt.issuer_version, receipt.observed_at
                FROM evidence_receipts AS receipt
                JOIN evidence_issuers AS issuer
                  ON issuer.tenant_id = receipt.tenant_id
                 AND issuer.business_id = receipt.business_id
                 AND issuer.source_system = receipt.source_system
                 AND issuer.evidence_kind = receipt.evidence_kind
                 AND issuer.actor_id = receipt.captured_by
                 AND issuer.issuer_version = receipt.issuer_version
                 AND issuer.enabled = 1
                JOIN actors AS actor
                  ON actor.actor_id = issuer.actor_id
                 AND actor.tenant_id = issuer.tenant_id
                 AND actor.enabled = 1
                JOIN json_each(actor.business_ids_json) AS membership
                  ON membership.value = issuer.business_id
                WHERE receipt.attempt_id = ? AND receipt.source_ref = ?
                  AND receipt.evidence_kind IN (
                      'external_readback', 'machine_check'
                  )
                  AND receipt.observed_at <= ? AND receipt.valid_until > ?
                ORDER BY receipt.observed_at DESC, receipt.receipt_id
                """,
                (
                    attempt["attempt_id"],
                    attempt["target_ref"],
                    decided_at,
                    decided_at,
                ),
            ).fetchall()
            latest_by_issuer: dict[
                tuple[str, str, str, str],
                tuple[str, set[str]],
            ] = {}
            for row in latest_rows:
                key = (
                    row["source_system"],
                    row["evidence_kind"],
                    row["captured_by"],
                    row["issuer_version"],
                )
                current = latest_by_issuer.get(key)
                if current is None or row["observed_at"] > current[0]:
                    latest_by_issuer[key] = (
                        row["observed_at"],
                        {row["receipt_id"]},
                    )
                elif row["observed_at"] == current[0]:
                    current[1].add(row["receipt_id"])
            latest_ids = {
                receipt_id
                for _, receipt_ids_for_issuer in latest_by_issuer.values()
                for receipt_id in receipt_ids_for_issuer
            }
            if set(receipt_ids) != latest_ids:
                raise ValueError(
                    "verification must use the latest authoritative receipt "
                    "from every applicable issuer"
                )
            expected_facts = verification["expected_facts"]
            if decision == "verified":
                if not expected_facts:
                    raise ValueError(
                        "verified decision requires expected facts"
                    )
                mismatches = {
                    key: value
                    for key, value in expected_facts.items()
                    if observed_result.get(key) != value
                }
                if mismatches:
                    raise ValueError(
                        "observed result does not satisfy expected facts"
                    )
            key_id, signature = self._completion_signature(
                verification,
                attempt,
                receipts,
                work,
                objective,
            )
            work_status = {
                "verified": "verified",
                "disproved": "disproved",
                "inconclusive": "verification_inconclusive",
            }[decision]
            connection.execute(
                """
                INSERT INTO outcome_verifications(
                    verification_id, attempt_id, work_item_id, tenant_id,
                    business_id, verifier_id, decision,
                    evidence_receipt_ids_json, expected_facts_json, rationale,
                    policy_version, decided_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    verification["verification_id"],
                    verification["attempt_id"],
                    verification["work_item_id"],
                    verification["tenant_id"],
                    verification["business_id"],
                    verification["verifier_id"],
                    decision,
                    receipt_ids_json,
                    expected_facts_json,
                    verification["rationale"],
                    verification["policy_version"],
                    decided_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO completion_attestations(
                    verification_id, attempt_id, work_item_id, tenant_id,
                    business_id, key_id, signature, created_at,
                    payload_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2)
                """,
                (
                    verification["verification_id"],
                    verification["attempt_id"],
                    verification["work_item_id"],
                    verification["tenant_id"],
                    verification["business_id"],
                    key_id,
                    signature,
                    decided_at,
                ),
            )
            connection.execute(
                """
                UPDATE execution_attempts
                SET status = ?, updated_at = ?
                WHERE attempt_id = ?
                """,
                (decision, decided_at, verification["attempt_id"]),
            )
            connection.execute(
                """
                UPDATE work_items
                SET status = ?, updated_at = ?
                WHERE work_item_id = ?
                """,
                (work_status, decided_at, verification["work_item_id"]),
            )
            connection.execute(
                """
                INSERT INTO audit_records(
                    audit_id, event_id, run_id, tenant_id, business_id,
                    record_type, details_json, created_at
                )
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"audit-verification-{verification['verification_id']}",
                    verification["work_item_id"],
                    verification["tenant_id"],
                    verification["business_id"],
                    f"outcome.{decision}",
                    json.dumps(
                        {
                            "attempt_id": verification["attempt_id"],
                            "decision": decision,
                            "evidence_receipt_ids": verification[
                                "evidence_receipt_ids"
                            ],
                            "policy_version": verification["policy_version"],
                            "verifier_id": verification["verifier_id"],
                        },
                        sort_keys=True,
                    ),
                    decided_at,
                ),
            )
        return True

    def get_latest_verification_for_work(
        self,
        work_item_id: str,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT verification.*, attestation.key_id,
                       attestation.signature, attestation.payload_version
                FROM outcome_verifications AS verification
                JOIN completion_attestations AS attestation
                  ON attestation.verification_id =
                     verification.verification_id
                WHERE verification.work_item_id = ?
                ORDER BY verification.decided_at DESC,
                         verification.rowid DESC
                LIMIT 1
                """,
                (work_item_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["evidence_receipt_ids"] = json.loads(
            result.pop("evidence_receipt_ids_json")
        )
        result["expected_facts"] = json.loads(
            result.pop("expected_facts_json")
        )
        result["decided_at"] = datetime.fromisoformat(result["decided_at"])
        return result

    def verify_completion_attestation(
        self,
        verification: dict[str, Any],
        attempt: dict[str, Any],
        receipts: list[dict[str, Any]],
    ) -> bool:
        try:
            if verification.get("payload_version") != 2:
                return False
            with self._connection() as connection:
                work, objective = self._completion_context(
                    connection,
                    verification["work_item_id"],
                )
            key_id, signature = self._completion_signature(
                verification,
                attempt,
                receipts,
                work,
                objective,
            )
            return hmac.compare_digest(
                verification["key_id"],
                key_id,
            ) and hmac.compare_digest(
                verification["signature"],
                signature,
            )
        except (KeyError, TypeError, ValueError, SchemaDriftError):
            return False

    def upsert_capability(
        self,
        *,
        capability_id: str,
        display_name: str,
        description: str,
        required_role: str,
        action_types: tuple[str, ...],
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO capability_definitions(
                    capability_id, display_name, description, required_role,
                    action_types_json
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(capability_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    description=excluded.description,
                    required_role=excluded.required_role,
                    action_types_json=excluded.action_types_json
                """,
                (
                    capability_id,
                    display_name,
                    description,
                    required_role,
                    json.dumps(sorted(action_types)),
                ),
            )

    def get_capability(self, capability_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM capability_definitions
                WHERE capability_id = ?
                """,
                (capability_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["action_types"] = tuple(
            json.loads(result.pop("action_types_json"))
        )
        return result

    def assign_capability(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor_id: str,
        capability_id: str,
        enabled: bool = True,
    ) -> None:
        actor = self.get_actor(actor_id)
        capability = self.get_capability(capability_id)
        if actor is None or not actor.can_access(
            tenant_id=tenant_id,
            business_id=business_id,
        ):
            raise ValueError("capability actor is outside the identity boundary")
        if capability is None:
            raise ValueError("capability is not registered")
        if capability["required_role"] not in actor.roles:
            raise ValueError("actor does not hold the capability's required role")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_capabilities(
                    tenant_id, business_id, actor_id, capability_id, enabled
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(
                    tenant_id, business_id, actor_id, capability_id
                ) DO UPDATE SET enabled=excluded.enabled
                """,
                (
                    tenant_id,
                    business_id,
                    actor_id,
                    capability_id,
                    int(enabled),
                ),
            )

    def actor_has_capability(
        self,
        *,
        tenant_id: str,
        business_id: str,
        actor_id: str,
        capability_id: str,
    ) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM agent_capabilities
                WHERE tenant_id = ?
                  AND business_id = ?
                  AND actor_id = ?
                  AND capability_id = ?
                  AND enabled = 1
                """,
                (tenant_id, business_id, actor_id, capability_id),
            ).fetchone()
        return row is not None

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
            raise ValueError("evidence confidence must be between 0 and 1")
        business = self.get_business(business_id)
        if business is None or business.tenant_id != tenant_id:
            raise ValueError("evidence business is outside the tenant")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO evidence_records(
                    evidence_id, tenant_id, business_id, source_type, source_ref,
                    statement, facts_json, confidence, observed_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    tenant_id,
                    business_id,
                    source_type,
                    source_ref,
                    statement,
                    json.dumps(facts, sort_keys=True),
                    str(confidence),
                    _utc_iso(observed_at),
                    _utc_now(),
                ),
            )

    def get_evidence(
        self,
        evidence_ids: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        if not evidence_ids:
            return []
        placeholders = ",".join("?" for _ in evidence_ids)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM evidence_records
                WHERE evidence_id IN ({placeholders})
                ORDER BY evidence_id
                """,
                evidence_ids,
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["facts"] = json.loads(result.pop("facts_json"))
            result["confidence"] = Decimal(result["confidence"])
            results.append(result)
        return results

    def record_plan_and_evaluation(
        self,
        *,
        plan_id: str,
        tenant_id: str,
        business_id: str,
        objective_id: str,
        capability_id: str,
        planner_id: str,
        plan: dict[str, Any],
        plan_hash: str,
        status: str,
        evaluation_id: str,
        evaluator_version: str,
        decision: str,
        score: int,
        reasons: tuple[str, ...],
        authority_modes: tuple[str, ...],
        evaluation_hash: str,
        created_at: datetime,
    ) -> None:
        timestamp = _utc_iso(created_at)
        computed_plan_hash = hashlib.sha256(
            json.dumps(
                plan,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        computed_evaluation_hash = hashlib.sha256(
            json.dumps(
                {
                    "authority_modes": authority_modes,
                    "decision": decision,
                    "reasons": reasons,
                    "score": score,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if computed_plan_hash != plan_hash:
            raise ValueError("stored plan hash does not match its payload")
        if computed_evaluation_hash != evaluation_hash:
            raise ValueError(
                "stored evaluation hash does not match its decision"
            )
        if status != decision or decision not in {"accepted", "rejected"}:
            raise ValueError("plan status and evaluation decision must match")
        if (
            plan.get("tenant_id") != tenant_id
            or plan.get("business_id") != business_id
            or plan.get("objective_id") != objective_id
            or plan.get("capability_id") != capability_id
            or plan.get("planner_id") != planner_id
        ):
            raise ValueError("plan payload crosses its declared identity")
        with self._connection() as connection:
            objective = connection.execute(
                """
                SELECT 1
                FROM objectives
                WHERE objective_id = ? AND tenant_id = ? AND business_id = ?
                """,
                (objective_id, tenant_id, business_id),
            ).fetchone()
            capability = connection.execute(
                """
                SELECT action_types_json
                FROM capability_definitions
                WHERE capability_id = ?
                """,
                (capability_id,),
            ).fetchone()
            if objective is None or capability is None:
                raise ValueError(
                    "plan objective or capability is outside storage"
                )
            if decision == "accepted":
                steps = plan.get("steps", ())
                if (
                    reasons
                    or not steps
                    or len(steps) != len(authority_modes)
                    or score != 100
                ):
                    raise ValueError(
                        "accepted evaluation is structurally inconsistent"
                    )
                evidence_refs = tuple(plan.get("evidence_refs", ()))
                if (
                    not evidence_refs
                    or len(evidence_refs) != len(set(evidence_refs))
                ):
                    raise ValueError(
                        "accepted plan requires unique supporting evidence"
                    )
                placeholders = ",".join("?" for _ in evidence_refs)
                evidence_rows = connection.execute(
                    f"""
                    SELECT confidence
                    FROM evidence_records
                    WHERE evidence_id IN ({placeholders})
                      AND tenant_id = ? AND business_id = ?
                    """,
                    (*evidence_refs, tenant_id, business_id),
                ).fetchall()
                if (
                    len(evidence_rows) != len(evidence_refs)
                    or any(
                        Decimal(row["confidence"]) < Decimal("0.50")
                        for row in evidence_rows
                    )
                ):
                    raise ValueError(
                        "accepted plan evidence is missing, weak, or out of scope"
                    )
                allowed_actions = set(
                    json.loads(capability["action_types_json"])
                )
                computed_modes = []
                for step in steps:
                    actor_capability = connection.execute(
                        """
                        SELECT 1
                        FROM agent_capabilities
                        WHERE tenant_id = ? AND business_id = ?
                          AND actor_id = ? AND capability_id = ?
                          AND enabled = 1
                        """,
                        (
                            tenant_id,
                            business_id,
                            step["assigned_actor_id"],
                            capability_id,
                        ),
                    ).fetchone()
                    if (
                        step["action_type"] not in allowed_actions
                        or actor_capability is None
                    ):
                        raise ValueError(
                            "accepted plan step lacks capability authority"
                        )
                    mode = self._decide_authority_in_connection(
                        connection,
                        ActionRequest(
                            action_type=step["action_type"],
                            tenant_id=tenant_id,
                            business_id=business_id,
                            actor_id=step["assigned_actor_id"],
                            attributes={
                                "expected_output": step[
                                    "expected_output"
                                ],
                                "plan_id": plan_id,
                            },
                        ),
                        now=created_at,
                    )
                    computed_modes.append(mode.value)
                if tuple(computed_modes) != tuple(authority_modes):
                    raise ValueError(
                        "accepted evaluation authority modes are forged or stale"
                    )
            connection.execute(
                """
                INSERT INTO structured_plans(
                    plan_id, tenant_id, business_id, objective_id, capability_id,
                    planner_id, plan_json, plan_hash, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    tenant_id,
                    business_id,
                    objective_id,
                    capability_id,
                    planner_id,
                    json.dumps(plan, sort_keys=True),
                    plan_hash,
                    status,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO plan_evaluations(
                    evaluation_id, plan_id, tenant_id, business_id,
                    evaluator_version, decision, score, reasons_json,
                    evaluation_hash, created_at, authority_modes_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation_id,
                    plan_id,
                    tenant_id,
                    business_id,
                    evaluator_version,
                    decision,
                    score,
                    json.dumps(reasons),
                    evaluation_hash,
                    timestamp,
                    json.dumps(authority_modes),
                ),
            )

    def insert_memory(self, memory: MemoryRecord) -> None:
        if memory.verification_status not in (
            VerificationStatus.UNVERIFIED,
            VerificationStatus.CANDIDATE,
        ):
            raise ValueError(
                "runtime memory insertion cannot self-promote verification"
            )
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO memory_records(
                    memory_id, tenant_id, business_id, memory_type, statement,
                    source_type, source_ref, confidence, verification_status,
                    evidence_refs_json, created_at, observed_at, expires_at,
                    supersedes_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.memory_id,
                    memory.tenant_id,
                    memory.business_id,
                    memory.memory_type.value,
                    memory.statement,
                    memory.source_type,
                    memory.source_ref,
                    str(memory.confidence),
                    memory.verification_status.value,
                    json.dumps(memory.evidence_refs),
                    _utc_iso(memory.created_at),
                    _utc_iso(memory.observed_at),
                    (
                        _utc_iso(memory.expires_at)
                        if memory.expires_at
                        else None
                    ),
                    memory.supersedes_id,
                ),
            )

    def materialize_plan(
        self,
        *,
        plan_id: str,
        plan_hash: str,
        objective_id: str,
        tenant_id: str,
        business_id: str,
        work_items: tuple[dict[str, Any], ...],
        next_review_at: datetime,
        memory: MemoryRecord,
        now: datetime,
    ) -> int:
        """Atomically create all plan work, candidate memory, and audit state."""
        if memory.verification_status not in (
            VerificationStatus.UNVERIFIED,
            VerificationStatus.CANDIDATE,
        ):
            raise ValueError(
                "runtime memory insertion cannot self-promote verification"
            )
        timestamp = _utc_iso(now)
        created = 0
        with self._immediate_connection() as connection:
            plan_row = connection.execute(
                """
                SELECT *
                FROM structured_plans
                WHERE plan_id = ? AND tenant_id = ? AND business_id = ?
                """,
                (plan_id, tenant_id, business_id),
            ).fetchone()
            if plan_row is None:
                raise ValueError("plan is outside the identity boundary")
            plan_payload = json.loads(plan_row["plan_json"])
            computed_plan_hash = hashlib.sha256(
                json.dumps(
                    plan_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if (
                plan_row["plan_hash"] != plan_hash
                or computed_plan_hash != plan_row["plan_hash"]
                or plan_row["objective_id"] != objective_id
                or plan_payload.get("tenant_id") != tenant_id
                or plan_payload.get("business_id") != business_id
                or plan_payload.get("objective_id") != objective_id
                or plan_payload.get("capability_id")
                != plan_row["capability_id"]
                or plan_payload.get("planner_id") != plan_row["planner_id"]
            ):
                raise ValueError(
                    "durable plan content, hash, or identity does not match"
                )
            if plan_row["status"] == "materialized":
                return 0
            if plan_row["status"] != "accepted":
                raise ValueError("only an accepted plan can be materialized")
            evaluation = connection.execute(
                """
                SELECT decision, score, reasons_json, authority_modes_json,
                       evaluation_hash
                FROM plan_evaluations
                WHERE plan_id = ? AND tenant_id = ? AND business_id = ?
                  AND decision = 'accepted'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (plan_id, tenant_id, business_id),
            ).fetchone()
            if (
                evaluation is None
                or evaluation["authority_modes_json"] is None
            ):
                raise ValueError(
                    "plan has no accepted evaluation with authority decisions"
                )
            authority_modes = tuple(
                json.loads(evaluation["authority_modes_json"])
            )
            reasons = tuple(json.loads(evaluation["reasons_json"]))
            computed_evaluation_hash = hashlib.sha256(
                json.dumps(
                    {
                        "authority_modes": authority_modes,
                        "decision": evaluation["decision"],
                        "reasons": reasons,
                        "score": evaluation["score"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if (
                evaluation["decision"] != "accepted"
                or evaluation["score"] != 100
                or reasons
                or computed_evaluation_hash != evaluation["evaluation_hash"]
            ):
                raise ValueError(
                    "durable plan evaluation is forged or inconsistent"
                )
            objective = connection.execute(
                """
                SELECT 1
                FROM objectives
                WHERE objective_id = ? AND tenant_id = ? AND business_id = ?
                """,
                (objective_id, tenant_id, business_id),
            ).fetchone()
            if objective is None:
                raise ValueError("plan objective is outside the identity boundary")
            steps = plan_payload.get("steps", [])
            if (
                len(steps) != len(work_items)
                or len(authority_modes) != len(work_items)
            ):
                raise ValueError(
                    "materialized work must exactly match evaluated plan steps"
                )
            evidence_refs = tuple(plan_payload.get("evidence_refs", ()))
            if (
                not evidence_refs
                or len(evidence_refs) != len(set(evidence_refs))
            ):
                raise ValueError(
                    "materialization requires unique durable evidence"
                )
            placeholders = ",".join("?" for _ in evidence_refs)
            evidence_rows = connection.execute(
                f"""
                SELECT confidence
                FROM evidence_records
                WHERE evidence_id IN ({placeholders})
                  AND tenant_id = ? AND business_id = ?
                """,
                (*evidence_refs, tenant_id, business_id),
            ).fetchall()
            if (
                len(evidence_rows) != len(evidence_refs)
                or any(
                    Decimal(row["confidence"]) < Decimal("0.50")
                    for row in evidence_rows
                )
            ):
                raise ValueError(
                    "materialization evidence is missing, weak, or out of scope"
                )
            capability_definition = connection.execute(
                """
                SELECT action_types_json
                FROM capability_definitions
                WHERE capability_id = ?
                """,
                (plan_row["capability_id"],),
            ).fetchone()
            if capability_definition is None:
                raise ValueError(
                    "materialization capability is no longer registered"
                )
            allowed_actions = set(
                json.loads(capability_definition["action_types_json"])
            )
            current_modes = []
            for step in steps:
                if step["action_type"] not in allowed_actions:
                    raise ValueError(
                        "materialization action is outside its capability"
                    )
                mode = self._decide_authority_in_connection(
                    connection,
                    ActionRequest(
                        action_type=step["action_type"],
                        tenant_id=tenant_id,
                        business_id=business_id,
                        actor_id=step["assigned_actor_id"],
                        attributes={
                            "expected_output": step["expected_output"],
                            "plan_id": plan_id,
                        },
                    ),
                    now=now,
                )
                current_modes.append(mode.value)
            if (
                tuple(current_modes) != authority_modes
                or "forbidden" in current_modes
            ):
                raise ValueError(
                    "evaluated authority is stale at materialization time"
                )
            if (
                memory.tenant_id != tenant_id
                or memory.business_id != business_id
                or memory.source_type != "structured_plan"
                or memory.source_ref != plan_id
                or memory.statement != plan_payload.get("hypothesis")
                or tuple(memory.evidence_refs)
                != tuple(plan_payload.get("evidence_refs", ()))
            ):
                raise ValueError(
                    "candidate memory does not match the accepted plan"
                )
            for index, item in enumerate(work_items):
                step = steps[index]
                expected_mode = current_modes[index]
                expected_status = (
                    "awaiting_approval"
                    if expected_mode == "approve"
                    else "ready"
                )
                actor = connection.execute(
                    """
                    SELECT tenant_id, business_ids_json, enabled
                    FROM actors
                    WHERE actor_id = ?
                    """,
                    (item["assigned_actor_id"],),
                ).fetchone()
                capability = connection.execute(
                    """
                    SELECT 1
                    FROM agent_capabilities
                    WHERE tenant_id = ? AND business_id = ?
                      AND actor_id = ? AND capability_id = ? AND enabled = 1
                    """,
                    (
                        tenant_id,
                        business_id,
                        item["assigned_actor_id"],
                        plan_row["capability_id"],
                    ),
                ).fetchone()
                if (
                    item["title"] != step["title"]
                    or item["rationale"] != step["rationale"]
                    or item["action_type"] != step["action_type"]
                    or item["assigned_actor_id"]
                    != step["assigned_actor_id"]
                    or item["attributes"].get("expected_output")
                    != step["expected_output"]
                    or item["attributes"].get("plan_id") != plan_id
                    or item["authority_mode"] != expected_mode
                    or item["status"] != expected_status
                    or expected_mode not in {"auto", "notify", "approve"}
                    or actor is None
                    or not actor["enabled"]
                    or actor["tenant_id"] != tenant_id
                    or business_id
                    not in json.loads(actor["business_ids_json"])
                    or capability is None
                ):
                    raise ValueError(
                        "materialized work differs from the accepted plan"
                    )
                work_item_id = item["work_item_id"]
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO work_items(
                        work_item_id, work_key, objective_id, tenant_id,
                        business_id, title, rationale, action_type,
                        assigned_actor_id, platform, account_id, amount, currency,
                        attributes_json, authority_mode, status, priority_score,
                        attempt_count, max_attempts, available_at, claimed_by,
                        lease_expires_at, last_error, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL,
                            ?, ?, ?, ?, 0, ?, ?, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        work_item_id,
                        f"plan:{plan_hash}:{index}",
                        objective_id,
                        tenant_id,
                        business_id,
                        item["title"],
                        item["rationale"],
                        item["action_type"],
                        item["assigned_actor_id"],
                        json.dumps(item["attributes"], sort_keys=True),
                        item["authority_mode"],
                        item["status"],
                        item["priority_score"],
                        item["max_attempts"],
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError(
                        "materialized work conflicts with existing durable work"
                    )
                created += 1
                connection.execute(
                    """
                    INSERT INTO audit_records(
                        audit_id, event_id, run_id, tenant_id, business_id,
                        record_type, details_json, created_at
                    )
                    VALUES (?, NULL, ?, ?, ?, 'work.discovered', ?, ?)
                    """,
                    (
                        item["audit_id"],
                        work_item_id,
                        tenant_id,
                        business_id,
                        json.dumps(
                            {
                                "action_type": item["action_type"],
                                "assigned_actor_id": item[
                                    "assigned_actor_id"
                                ],
                                "authority_mode": item["authority_mode"],
                                "objective_id": objective_id,
                                "plan_id": plan_id,
                                "status": item["status"],
                                "title": item["title"],
                            },
                            sort_keys=True,
                        ),
                        timestamp,
                    ),
                )
                if expected_status == "awaiting_approval":
                    work = connection.execute(
                        "SELECT * FROM work_items WHERE work_item_id = ?",
                        (work_item_id,),
                    ).fetchone()
                    if work is None:
                        raise ValueError(
                            "approval-held plan work was not persisted"
                        )
                    self._insert_approval_request(
                        connection,
                        work=work,
                        requested_at=now,
                        expires_at=now + timedelta(hours=24),
                    )
            connection.execute(
                """
                UPDATE objectives
                SET next_review_at = ?, updated_at = ?
                WHERE objective_id = ? AND tenant_id = ? AND business_id = ?
                """,
                (
                    _utc_iso(next_review_at),
                    timestamp,
                    objective_id,
                    tenant_id,
                    business_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_records(
                    memory_id, tenant_id, business_id, memory_type, statement,
                    source_type, source_ref, confidence, verification_status,
                    evidence_refs_json, created_at, observed_at, expires_at,
                    supersedes_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.memory_id,
                    memory.tenant_id,
                    memory.business_id,
                    memory.memory_type.value,
                    memory.statement,
                    memory.source_type,
                    memory.source_ref,
                    str(memory.confidence),
                    memory.verification_status.value,
                    json.dumps(memory.evidence_refs),
                    _utc_iso(memory.created_at),
                    _utc_iso(memory.observed_at),
                    (
                        _utc_iso(memory.expires_at)
                        if memory.expires_at
                        else None
                    ),
                    memory.supersedes_id,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE structured_plans
                SET status = 'materialized'
                WHERE plan_id = ? AND status = 'accepted'
                """,
                (plan_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "accepted plan changed before materialization committed"
                )
        return created

    def dashboard_snapshot(self) -> dict[str, Any]:
        with self._connection() as connection:
            counts = {
                "tenants": connection.execute(
                    "SELECT COUNT(*) FROM tenants"
                ).fetchone()[0],
                "businesses": connection.execute(
                    "SELECT COUNT(*) FROM businesses"
                ).fetchone()[0],
                "events": connection.execute(
                    "SELECT COUNT(*) FROM events"
                ).fetchone()[0],
                "runs": connection.execute(
                    "SELECT COUNT(*) FROM workflow_runs"
                ).fetchone()[0],
                "audit_records": connection.execute(
                    "SELECT COUNT(*) FROM audit_records"
                ).fetchone()[0],
                "objectives": connection.execute(
                    "SELECT COUNT(*) FROM objectives"
                ).fetchone()[0],
                "work_items": connection.execute(
                    "SELECT COUNT(*) FROM work_items"
                ).fetchone()[0],
                "plans": connection.execute(
                    "SELECT COUNT(*) FROM structured_plans"
                ).fetchone()[0],
                "evidence": connection.execute(
                    "SELECT COUNT(*) FROM evidence_records"
                ).fetchone()[0],
                "memories": connection.execute(
                    "SELECT COUNT(*) FROM memory_records"
                ).fetchone()[0],
                "execution_attempts": connection.execute(
                    "SELECT COUNT(*) FROM execution_attempts"
                ).fetchone()[0],
                "evidence_receipts": connection.execute(
                    "SELECT COUNT(*) FROM evidence_receipts"
                ).fetchone()[0],
                "outcome_verifications": connection.execute(
                    "SELECT COUNT(*) FROM outcome_verifications"
                ).fetchone()[0],
                "approval_requests": connection.execute(
                    "SELECT COUNT(*) FROM approval_requests"
                ).fetchone()[0],
                "emergency_stop_events": connection.execute(
                    "SELECT COUNT(*) FROM emergency_stop_events"
                ).fetchone()[0],
                "spend_envelopes": connection.execute(
                    "SELECT COUNT(*) FROM spend_envelopes"
                ).fetchone()[0],
                "spend_commitments": connection.execute(
                    "SELECT COUNT(*) FROM spend_commitments"
                ).fetchone()[0],
                "routing_decisions": connection.execute(
                    "SELECT COUNT(*) FROM routing_decisions"
                ).fetchone()[0],
                "model_usage_records": connection.execute(
                    "SELECT COUNT(*) FROM model_usage_records"
                ).fetchone()[0],
                "shadow_model_attempts": connection.execute(
                    "SELECT COUNT(*) FROM shadow_model_attempts"
                ).fetchone()[0],
                "shadow_model_outcomes": connection.execute(
                    "SELECT COUNT(*) FROM shadow_model_outcomes"
                ).fetchone()[0],
                "model_evaluation_replays": connection.execute(
                    "SELECT COUNT(*) FROM model_evaluation_replays"
                ).fetchone()[0],
                "affiliate_shadow_runs": connection.execute(
                    "SELECT COUNT(*) FROM affiliate_shadow_runs"
                ).fetchone()[0],
                "affiliate_observations": connection.execute(
                    "SELECT COUNT(*) FROM affiliate_observations"
                ).fetchone()[0],
                "affiliate_learnings": connection.execute(
                    "SELECT COUNT(*) FROM affiliate_learnings"
                ).fetchone()[0],
                "capability_pack_acceptances": connection.execute(
                    "SELECT COUNT(*) FROM capability_pack_acceptances WHERE passed=1"
                ).fetchone()[0],
                "aggregate_performance_snapshots": connection.execute(
                    "SELECT COUNT(*) FROM aggregate_performance_snapshots"
                ).fetchone()[0],
                "production_qualifications": connection.execute(
                    "SELECT COUNT(*) FROM production_qualifications WHERE decision='passed'"
                ).fetchone()[0],
                "legacy_cutover_plans": connection.execute(
                    "SELECT COUNT(*) FROM legacy_cutover_plans"
                ).fetchone()[0],
                "open_model_circuits": connection.execute(
                    """
                    SELECT COUNT(*) FROM model_circuit_states
                    WHERE circuit_state != 'closed'
                    """
                ).fetchone()[0],
            }
            routing_decisions = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT decision_id, request_id, business_id,
                           catalog_version, status, provider_id, model_id,
                           estimated_cost_micros, previous_decision_id,
                           is_circuit_probe, created_at
                    FROM routing_decisions
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]
            model_usage = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT usage_id, decision_id, business_id, provider_id,
                           model_id, input_tokens, output_tokens, cost_micros,
                           outcome, latency_ms, created_at
                    FROM model_usage_records
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]
            model_circuits = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT business_id, provider_id, model_id, circuit_state,
                           consecutive_failures, open_until, probe_in_flight,
                           updated_at
                    FROM model_circuit_states
                    ORDER BY updated_at DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]
            model_cost_totals = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT business_id, provider_id,
                           SUM(input_tokens) AS input_tokens,
                           SUM(output_tokens) AS output_tokens,
                           SUM(cost_micros) AS cost_micros
                    FROM model_usage_records
                    GROUP BY business_id, provider_id
                    ORDER BY cost_micros DESC
                    """
                ).fetchall()
            ]
            shadow_model_attempts = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT attempt.attempt_id, attempt.decision_id,
                           attempt.business_id, attempt.provider_id,
                           attempt.model_id, attempt.attempt_kind,
                           attempt.prompt_template_id, attempt.prompt_version,
                           attempt.input_token_estimate,
                           attempt.max_output_tokens,
                           COALESCE(outcome.status, 'uncertain') AS status,
                           outcome.provider_outcome, outcome.error_code,
                           attempt.created_at
                    FROM shadow_model_attempts AS attempt
                    LEFT JOIN shadow_model_outcomes AS outcome
                      ON outcome.attempt_id = attempt.attempt_id
                    ORDER BY attempt.created_at DESC, attempt.rowid DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]
            model_evaluation_replays = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT replay_id, suite_id, suite_version,
                           evaluator_version, case_count, passed_count,
                           passed, created_at
                    FROM model_evaluation_replays
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]
            affiliate_shadow_runs = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT run.run_id, run.objective_id, run.business_id,
                           run.producer_id, recommendation.status AS recommendation_status,
                           offer.offer_key, experiment.status AS experiment_status,
                           experiment.mode, measurement.click_count,
                           measurement.conversion_count,
                           measurement.conversion_rate_bps,
                           verification.decision AS verification_decision,
                           learning.decision AS learning_decision, run.created_at
                    FROM affiliate_shadow_runs run
                    LEFT JOIN affiliate_recommendations recommendation ON recommendation.run_id=run.run_id
                    LEFT JOIN affiliate_offer_snapshots offer ON offer.snapshot_id=recommendation.selected_snapshot_id
                    LEFT JOIN affiliate_experiments experiment ON experiment.run_id=run.run_id
                    LEFT JOIN affiliate_measurements measurement ON measurement.experiment_id=experiment.experiment_id
                    LEFT JOIN affiliate_verifications verification ON verification.measurement_id=measurement.measurement_id
                    LEFT JOIN affiliate_learnings learning ON learning.verification_id=verification.verification_id
                    ORDER BY run.created_at DESC LIMIT 100
                    """
                ).fetchall()
            ]
            capability_pack_acceptances = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT pack_id, pack_version, evaluator_version,
                           case_count, passed_count, passed, accepted_at
                    FROM capability_pack_acceptances
                    ORDER BY pack_id, accepted_at DESC LIMIT 100
                    """
                ).fetchall()
            ]
            aggregate_performance = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT snapshot.snapshot_id, snapshot.business_id,
                           snapshot.channel, snapshot.offer_key,
                           snapshot.window_start, snapshot.window_end,
                           snapshot.impressions, snapshot.engagements,
                           snapshot.content_clicks, snapshot.outbound_clicks,
                           snapshot.conversions, snapshot.commission_minor,
                           snapshot.evidence_class,
                           verification.decision AS verification_decision,
                           snapshot.imported_at
                    FROM aggregate_performance_snapshots snapshot
                    LEFT JOIN aggregate_performance_verifications verification
                      ON verification.snapshot_id=snapshot.snapshot_id
                    ORDER BY snapshot.imported_at DESC LIMIT 100
                    """
                ).fetchall()
            ]
            production_qualifications = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT qualification_id, business_id, kind, release_version,
                           artifact_hash, producer_id, verifier_id, decision,
                           external_side_effects_enabled, qualified_at
                    FROM production_qualifications
                    ORDER BY qualified_at DESC, rowid DESC LIMIT 100
                    """
                ).fetchall()
            ]
            legacy_cutovers = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT plan.plan_id, plan.business_id, plan.source_system,
                           plan.capability_id, plan.mode,
                           plan.legacy_disable_allowed,
                           plan.external_side_effects_enabled,
                           event.stage AS latest_stage, event.created_at
                    FROM legacy_cutover_plans plan
                    JOIN legacy_cutover_events event ON event.rowid=(
                      SELECT latest.rowid FROM legacy_cutover_events latest
                      WHERE latest.plan_id=plan.plan_id
                      ORDER BY latest.rowid DESC LIMIT 1
                    )
                    ORDER BY event.created_at DESC LIMIT 100
                    """
                ).fetchall()
            ]
            spend_envelopes = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT envelope.envelope_id, envelope.business_id,
                           envelope.action_type, envelope.platform,
                           envelope.account_id, envelope.currency,
                           envelope.limit_minor,
                           COALESCE(SUM(commitment.amount_minor), 0)
                               AS committed_minor,
                           envelope.limit_minor - COALESCE(
                               SUM(commitment.amount_minor), 0
                           ) AS remaining_minor,
                           envelope.period_start, envelope.period_end,
                           envelope.created_by
                    FROM spend_envelopes AS envelope
                    LEFT JOIN spend_commitments AS commitment
                      ON commitment.envelope_id = envelope.envelope_id
                    GROUP BY envelope.envelope_id
                    ORDER BY envelope.period_start DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]
            spend_commitments = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT commitment_id, envelope_id, attempt_id,
                           work_item_id, business_id, amount_minor, currency,
                           created_at
                    FROM spend_commitments
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]
            approvals = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT request.approval_id, request.work_item_id,
                           request.business_id, request.requester_id,
                           request.action_type, request.requested_at,
                           request.expires_at, work.status AS work_status,
                           (
                               SELECT event.decision
                               FROM approval_events AS event
                               WHERE event.approval_id = request.approval_id
                               ORDER BY event.created_at DESC, event.rowid DESC
                               LIMIT 1
                           ) AS latest_decision
                    FROM approval_requests AS request
                    JOIN work_items AS work
                      ON work.work_item_id = request.work_item_id
                    ORDER BY request.requested_at DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]
            emergency_stops = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT event_id, business_id, actor_id, action, reason,
                           created_at
                    FROM emergency_stop_events
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]
            execution_attempts = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT attempt_id, work_item_id, business_id, producer_id,
                           execution_mode, action_type, target_ref, status,
                           precondition_receipt_id, attempted_at, observed_at,
                           reconciliation_attempt_count,
                           reconciliation_max_attempts,
                           reconciliation_available_at,
                           reconciliation_lease_expires_at,
                           reconciliation_last_error
                    FROM execution_attempts
                    ORDER BY attempted_at DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]
            evidence_receipts = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT receipt_id, work_item_id, attempt_id, business_id,
                           evidence_kind, source_system, source_ref, captured_by,
                           issuer_version, observed_at, valid_until, content_hash
                    FROM evidence_receipts
                    ORDER BY observed_at DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]
            outcome_verifications = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT verification_id, attempt_id, work_item_id,
                           business_id, verifier_id, decision,
                           evidence_receipt_ids_json, expected_facts_json,
                           policy_version, decided_at
                    FROM outcome_verifications
                    ORDER BY decided_at DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]
            plans = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT p.plan_id, p.business_id, p.objective_id,
                           p.capability_id, p.planner_id, p.status, p.plan_hash,
                           e.decision, e.score, e.reasons_json, p.created_at
                    FROM structured_plans AS p
                    JOIN plan_evaluations AS e ON e.plan_id = p.plan_id
                    ORDER BY p.created_at DESC
                    LIMIT 50
                    """
                ).fetchall()
            ]
            evidence = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT evidence_id, business_id, source_type, source_ref,
                           statement, confidence, observed_at
                    FROM evidence_records
                    ORDER BY observed_at DESC
                    LIMIT 50
                    """
                ).fetchall()
            ]
            memories = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT memory_id, business_id, memory_type, statement,
                           source_type, source_ref, confidence,
                           verification_status, created_at
                    FROM memory_records
                    ORDER BY created_at DESC
                    LIMIT 50
                    """
                ).fetchall()
            ]
            objective_statuses = {
                row["status"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM objectives
                    GROUP BY status
                    """
                ).fetchall()
            }
            work_statuses = {
                row["status"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM work_items
                    GROUP BY status
                    """
                ).fetchall()
            }
            objectives = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT objective_id, tenant_id, business_id, statement,
                           metric, target_value, current_value, status, priority,
                           next_review_at, updated_at
                    FROM objectives
                    ORDER BY priority ASC, updated_at DESC
                    LIMIT 50
                    """
                ).fetchall()
            ]
            work_items = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT work_item_id, objective_id, business_id, title,
                           action_type, assigned_actor_id, authority_mode, status,
                           priority_score, attempt_count, max_attempts,
                           available_at, lease_expires_at, last_error, updated_at
                    FROM work_items
                    ORDER BY updated_at DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]
            runs = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT run_id, event_id, tenant_id, business_id, action_type,
                           authority_mode, status, summary, created_at
                    FROM workflow_runs
                    ORDER BY created_at DESC
                    LIMIT 50
                    """
                ).fetchall()
            ]
            audits = [
                {
                    **dict(row),
                    "details": json.loads(row["details_json"]),
                }
                for row in connection.execute(
                    """
                    SELECT audit_id, event_id, run_id, tenant_id, business_id,
                           record_type, details_json, created_at
                    FROM audit_records
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 100
                    """
                ).fetchall()
            ]
        return {
            "generated_at": _utc_now(),
            "counts": counts,
            "objective_statuses": objective_statuses,
            "work_statuses": work_statuses,
            "objectives": objectives,
            "work_items": work_items,
            "plans": plans,
            "evidence": evidence,
            "memories": memories,
            "execution_attempts": execution_attempts,
            "evidence_receipts": evidence_receipts,
            "outcome_verifications": outcome_verifications,
            "approvals": approvals,
            "emergency_stops": emergency_stops,
            "spend_envelopes": spend_envelopes,
            "spend_commitments": spend_commitments,
            "routing_decisions": routing_decisions,
            "model_usage": model_usage,
            "model_circuits": model_circuits,
            "model_cost_totals": model_cost_totals,
            "shadow_model_attempts": shadow_model_attempts,
            "model_evaluation_replays": model_evaluation_replays,
            "affiliate_shadow_runs": affiliate_shadow_runs,
            "capability_pack_acceptances": capability_pack_acceptances,
            "aggregate_performance": aggregate_performance,
            "production_qualifications": production_qualifications,
            "legacy_cutovers": legacy_cutovers,
            "runs": runs,
            "audit_records": audits,
        }
