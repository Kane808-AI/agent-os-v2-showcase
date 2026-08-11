"""Goal 14 production qualification, packaging, and cutover controls.

This module deliberately separates *qualification* from *activation*.  It can
build a tenant package, record independently verified operational evidence,
and rehearse a legacy cutover, but it has no connector, publisher, credential
resolver, deployment API, or legacy-disable operation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Callable
from uuid import uuid4

from .contracts import ActorType
from .storage import LATEST_SCHEMA_VERSION


class ProductionError(ValueError):
    """Raised when production evidence or a package fails closed."""


class QualificationDecision(StrEnum):
    PASSED = "passed"
    HELD = "held"


class CutoverStage(StrEnum):
    INVENTORIED = "inventoried"
    SHADOW_COMPARED = "shadow_compared"
    RECOVERY_VERIFIED = "recovery_verified"
    APPROVED = "approved"
    CANARY_OBSERVED = "canary_observed"
    ROLLED_BACK = "rolled_back"


REQUIRED_QUALIFICATIONS = frozenset({
    "packaging",
    "onboarding",
    "persistence",
    "security",
    "observability",
    "recovery",
    "cost",
    "upgrade",
})

REQUIRED_METRICS = frozenset({
    "availability",
    "backup_age_seconds",
    "emergency_stop_state",
    "error_rate",
    "model_cost_micros",
    "oldest_work_age_seconds",
    "queue_depth",
    "schema_version",
})

_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SECRET_REF = re.compile(r"^secretref://[a-z0-9][a-z0-9/_-]+$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_SECRET_MARKERS = (
    "api_key=",
    "password=",
    "postgres://",
    "postgresql://",
    "private key",
    "sk-",
    "token=",
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda item: sorted(item) if isinstance(item, (set, frozenset)) else str(item),
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProductionError("production timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _text(label: str, value: str) -> None:
    if not value or value != value.strip():
        raise ProductionError(f"{label} must be a non-empty trimmed value")


def _content_is_secret_free(value: Any) -> bool:
    lowered = _canonical(value).lower()
    return not any(marker in lowered for marker in _FORBIDDEN_SECRET_MARKERS)


@dataclass(frozen=True, slots=True)
class TenantDeploymentManifest:
    tenant_id: str
    business_id: str
    release_version: str
    image_digest: str
    database_adapter: str
    runtime_database_role_ref: str
    migration_database_role_ref: str
    backup_database_role_ref: str
    secret_provider: str
    attestation_provider: str
    attestation_key_ref: str
    dashboard_origin: str
    dashboard_auth: str
    tls_required: bool
    telemetry_mode: str
    external_side_effects_enabled: bool = False

    def __post_init__(self) -> None:
        for label in ("tenant_id", "business_id"):
            _text(label, getattr(self, label))
        if not _VERSION.fullmatch(self.release_version) or "latest" in self.release_version:
            raise ProductionError("deployment release must be an immutable version")
        if not _DIGEST.fullmatch(self.image_digest):
            raise ProductionError("deployment image must use an immutable sha256 digest")
        if self.database_adapter != "postgresql":
            raise ProductionError("production deployment requires PostgreSQL")
        role_refs = (
            self.runtime_database_role_ref,
            self.migration_database_role_ref,
            self.backup_database_role_ref,
        )
        if len(set(role_refs)) != 3 or any(
            not _SECRET_REF.fullmatch(reference) for reference in role_refs
        ):
            raise ProductionError("database duties require three distinct secret references")
        if self.secret_provider not in {
            "aws-secrets-manager", "azure-key-vault", "gcp-secret-manager", "vault"
        }:
            raise ProductionError("production secrets require a hosted secret provider")
        if self.attestation_provider != "external-kms":
            raise ProductionError("production attestations require an external KMS")
        if not _SECRET_REF.fullmatch(self.attestation_key_ref):
            raise ProductionError("attestation key must be an opaque secret reference")
        if self.attestation_key_ref in role_refs:
            raise ProductionError("database and attestation authority must be separated")
        if (
            not self.dashboard_origin.startswith("https://")
            or self.dashboard_auth not in {"oidc", "saml"}
            or not self.tls_required
        ):
            raise ProductionError("production dashboard requires TLS and federated auth")
        if self.telemetry_mode != "metadata-only":
            raise ProductionError("production telemetry must exclude prompt and secret content")
        if self.external_side_effects_enabled:
            raise ProductionError("qualification packages cannot activate side effects")
        if not _content_is_secret_free(asdict(self)):
            raise ProductionError("deployment manifest appears to contain a secret value")

    @property
    def manifest_hash(self) -> str:
        return _hash(asdict(self))


@dataclass(frozen=True, slots=True)
class OperationalProfile:
    metrics: frozenset[str]
    log_mode: str
    trace_mode: str
    retention_days: int
    health_timeout_seconds: int
    alert_route_ref: str

    def __post_init__(self) -> None:
        if self.metrics != REQUIRED_METRICS:
            raise ProductionError("operational profile has incomplete or excess metrics")
        if self.log_mode != "metadata-only" or self.trace_mode != "metadata-only":
            raise ProductionError("operational telemetry cannot retain content")
        if not 7 <= self.retention_days <= 90:
            raise ProductionError("telemetry retention must be between 7 and 90 days")
        if not 1 <= self.health_timeout_seconds <= 60:
            raise ProductionError("health timeout is outside the supported range")
        if not _SECRET_REF.fullmatch(self.alert_route_ref):
            raise ProductionError("alert route must remain an opaque reference")


@dataclass(frozen=True, slots=True)
class CostModel:
    currency: str
    monthly_fixed_minor: int
    storage_gib_month_minor: int
    operation_micros: int
    model_cost_markup_bps: int
    monthly_limit_minor: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ProductionError("cost currency must be ISO-style uppercase")
        values = (
            self.monthly_fixed_minor,
            self.storage_gib_month_minor,
            self.operation_micros,
            self.model_cost_markup_bps,
            self.monthly_limit_minor,
        )
        if any(value < 0 for value in values):
            raise ProductionError("cost inputs cannot be negative")
        if self.model_cost_markup_bps > 10_000:
            raise ProductionError("model markup cannot exceed 100 percent")
        if self.monthly_limit_minor <= 0:
            raise ProductionError("cost model requires a positive monthly limit")

    def estimate_monthly_minor(
        self, *, storage_gib: int, operations: int, model_cost_micros: int
    ) -> int:
        if min(storage_gib, operations, model_cost_micros) < 0:
            raise ProductionError("cost usage cannot be negative")
        operation_minor = (operations * self.operation_micros + 9_999) // 10_000
        model_minor = (
            model_cost_micros * (10_000 + self.model_cost_markup_bps)
            + 99_999_999
        ) // 100_000_000
        return (
            self.monthly_fixed_minor
            + storage_gib * self.storage_gib_month_minor
            + operation_minor
            + model_minor
        )


@dataclass(frozen=True, slots=True)
class ResilienceReport:
    persistence_adapter: str
    isolation_control: str
    attestation_control: str
    crash_cases: int
    state_machine_cases: int
    fuzz_seed: int
    failures: int
    backup_hash: str
    restore_integrity: str
    point_in_time_recovery: bool
    rpo_seconds: int
    rto_seconds: int
    max_rpo_seconds: int
    max_rto_seconds: int
    evidence_hash: str

    def __post_init__(self) -> None:
        if self.persistence_adapter != "postgresql":
            raise ProductionError("resilience qualification requires PostgreSQL")
        if self.isolation_control != "row-level-security":
            raise ProductionError("production tenant isolation requires row-level security")
        if self.attestation_control != "external-kms":
            raise ProductionError("hostile-database-admin containment requires external KMS")
        if self.crash_cases < 8 or self.state_machine_cases < 128:
            raise ProductionError("resilience qualification coverage is insufficient")
        if self.failures != 0:
            raise ProductionError("resilience qualification contains failures")
        if not _HASH.fullmatch(self.backup_hash) or not _HASH.fullmatch(self.evidence_hash):
            raise ProductionError("resilience evidence hashes are invalid")
        if self.restore_integrity != "ok" or not self.point_in_time_recovery:
            raise ProductionError("restore and point-in-time recovery must pass")
        if min(self.rpo_seconds, self.rto_seconds) < 0:
            raise ProductionError("recovery observations cannot be negative")
        if self.rpo_seconds > self.max_rpo_seconds or self.rto_seconds > self.max_rto_seconds:
            raise ProductionError("recovery objectives were missed")


@dataclass(frozen=True, slots=True)
class UpgradePlan:
    from_version: str
    to_version: str
    target_schema_version: int
    release_artifact_hash: str
    pre_upgrade_backup_hash: str
    migration_rehearsal_passed: bool
    rollback_rehearsal_passed: bool
    canary_passed: bool

    def __post_init__(self) -> None:
        if (
            not _VERSION.fullmatch(self.from_version)
            or not _VERSION.fullmatch(self.to_version)
            or self.from_version == self.to_version
        ):
            raise ProductionError("upgrade requires distinct immutable releases")
        if self.target_schema_version != LATEST_SCHEMA_VERSION:
            raise ProductionError("upgrade target schema is not current")
        if not _HASH.fullmatch(self.release_artifact_hash) or not _HASH.fullmatch(
            self.pre_upgrade_backup_hash
        ):
            raise ProductionError("upgrade artifacts require sha256 hashes")
        if not all((
            self.migration_rehearsal_passed,
            self.rollback_rehearsal_passed,
            self.canary_passed,
        )):
            raise ProductionError("upgrade migration, canary, and rollback must all pass")


class TenantPackageBuilder:
    """Build a private, atomic package containing references but no secrets."""

    def build(
        self,
        manifest: TenantDeploymentManifest,
        destination: str | Path,
        *,
        before_publish: Callable[[Path], None] | None = None,
    ) -> Path:
        target = Path(destination)
        if target.exists():
            raise ProductionError(f"package destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = Path(tempfile.mkdtemp(prefix=".agent-os-package-", dir=target.parent))
        try:
            public_manifest = asdict(manifest)
            files = {
                "manifest.json": json.dumps(public_manifest, indent=2, sort_keys=True) + "\n",
                "README.txt": (
                    "Agent OS isolated tenant package. Resolve secretref:// values "
                    "at runtime. Qualification does not activate external side effects.\n"
                ),
            }
            if not _content_is_secret_free(files):
                raise ProductionError("package contains secret-like material")
            for name, content in files.items():
                path = staged / name
                with path.open("w", encoding="utf-8") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(path, 0o600)
            os.chmod(staged, 0o700)
            directory_fd = os.open(staged, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            if before_publish is not None:
                before_publish(staged)
            if target.exists():
                raise ProductionError(
                    f"package destination appeared during publication: {target}"
                )
            os.replace(staged, target)
            parent_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            return target
        except Exception:
            shutil.rmtree(staged, ignore_errors=True)
            raise


class ProductionReadinessService:
    """Record independently verified readiness without activating a runtime."""

    _STAGES = (
        CutoverStage.INVENTORIED,
        CutoverStage.SHADOW_COMPARED,
        CutoverStage.RECOVERY_VERIFIED,
        CutoverStage.APPROVED,
        CutoverStage.CANARY_OBSERVED,
    )

    def __init__(self, store: Any):
        self.store = store

    def _actors(
        self, *, tenant_id: str, business_id: str, producer_id: str, verifier_id: str
    ) -> tuple[Any, Any]:
        producer = self.store.get_actor(producer_id)
        verifier = self.store.get_actor(verifier_id)
        if (
            producer is None
            or verifier is None
            or producer_id == verifier_id
            or not producer.can_access(tenant_id=tenant_id, business_id=business_id)
            or not verifier.can_access(tenant_id=tenant_id, business_id=business_id)
            or not producer.roles & {"platform-reliability", "operations"}
            or not verifier.roles & {"qa", "verifier"}
        ):
            raise ProductionError("qualification requires independent scoped operations and QA")
        return producer, verifier

    def record_qualification(
        self,
        *,
        tenant_id: str,
        business_id: str,
        kind: str,
        release_version: str,
        artifact_hash: str,
        checks: dict[str, bool],
        producer_id: str,
        verifier_id: str,
        now: datetime | None = None,
    ) -> QualificationDecision:
        self._actors(
            tenant_id=tenant_id, business_id=business_id,
            producer_id=producer_id, verifier_id=verifier_id,
        )
        if kind not in REQUIRED_QUALIFICATIONS:
            raise ProductionError("unknown production qualification kind")
        if not _VERSION.fullmatch(release_version) or not _HASH.fullmatch(artifact_hash):
            raise ProductionError("qualification release or artifact hash is invalid")
        if not checks or any(type(value) is not bool for value in checks.values()):
            raise ProductionError("qualification checks must be named booleans")
        decision = (
            QualificationDecision.PASSED
            if all(checks.values())
            else QualificationDecision.HELD
        )
        checks_json = _canonical(checks)
        with self.store._immediate_connection() as connection:
            self.store._require_business_scope(
                connection, tenant_id=tenant_id, business_id=business_id
            )
            connection.execute(
                """
                INSERT INTO production_qualifications(
                    qualification_id, tenant_id, business_id, kind,
                    release_version, artifact_hash, checks_json, checks_hash,
                    producer_id, verifier_id, decision,
                    external_side_effects_enabled, qualified_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?
                )
                """,
                (
                    f"production-qualification-{uuid4()}", tenant_id, business_id,
                    kind, release_version, artifact_hash, checks_json,
                    _hash(checks), producer_id, verifier_id, decision.value,
                    _aware(now or datetime.now(timezone.utc)).isoformat(),
                ),
            )
        return decision

    def qualify_manifest(
        self,
        manifest: TenantDeploymentManifest,
        *,
        producer_id: str,
        verifier_id: str,
        now: datetime | None = None,
    ) -> tuple[QualificationDecision, QualificationDecision]:
        packaging = self.record_qualification(
            tenant_id=manifest.tenant_id, business_id=manifest.business_id,
            kind="packaging", release_version=manifest.release_version,
            artifact_hash=manifest.manifest_hash,
            checks={
                "immutable_image": True,
                "isolated_database_roles": True,
                "no_secret_values": True,
                "side_effects_disabled": not manifest.external_side_effects_enabled,
            }, producer_id=producer_id, verifier_id=verifier_id, now=now,
        )
        onboarding = self.record_qualification(
            tenant_id=manifest.tenant_id, business_id=manifest.business_id,
            kind="onboarding", release_version=manifest.release_version,
            artifact_hash=manifest.manifest_hash,
            checks={
                "dashboard_auth": manifest.dashboard_auth in {"oidc", "saml"},
                "single_business_scope": bool(manifest.business_id),
                "tenant_scope": bool(manifest.tenant_id),
                "tls": manifest.tls_required,
            }, producer_id=producer_id, verifier_id=verifier_id, now=now,
        )
        return packaging, onboarding

    def qualify_operations(
        self,
        *, tenant_id: str, business_id: str, release_version: str,
        profile: OperationalProfile, cost_model: CostModel,
        storage_gib: int, operations: int, model_cost_micros: int,
        producer_id: str, verifier_id: str, now: datetime | None = None,
    ) -> int:
        estimated = cost_model.estimate_monthly_minor(
            storage_gib=storage_gib, operations=operations,
            model_cost_micros=model_cost_micros,
        )
        profile_hash = _hash(asdict(profile))
        self.record_qualification(
            tenant_id=tenant_id, business_id=business_id, kind="observability",
            release_version=release_version, artifact_hash=profile_hash,
            checks={"complete_metrics": profile.metrics == REQUIRED_METRICS,
                    "content_free": profile.log_mode == profile.trace_mode == "metadata-only",
                    "alert_route_bound": bool(profile.alert_route_ref)},
            producer_id=producer_id, verifier_id=verifier_id, now=now,
        )
        self.record_qualification(
            tenant_id=tenant_id, business_id=business_id, kind="cost",
            release_version=release_version, artifact_hash=_hash(asdict(cost_model)),
            checks={"within_limit": estimated <= cost_model.monthly_limit_minor,
                    "integer_accounting": isinstance(estimated, int)},
            producer_id=producer_id, verifier_id=verifier_id, now=now,
        )
        return estimated

    def qualify_resilience(
        self, *, tenant_id: str, business_id: str, release_version: str,
        report: ResilienceReport, producer_id: str, verifier_id: str,
        now: datetime | None = None,
    ) -> tuple[QualificationDecision, QualificationDecision]:
        persistence = self.record_qualification(
            tenant_id=tenant_id, business_id=business_id, kind="persistence",
            release_version=release_version, artifact_hash=report.evidence_hash,
            checks={"postgresql": True, "row_level_security": True,
                    "external_attestation": True}, producer_id=producer_id,
            verifier_id=verifier_id, now=now,
        )
        recovery = self.record_qualification(
            tenant_id=tenant_id, business_id=business_id, kind="recovery",
            release_version=release_version, artifact_hash=report.backup_hash,
            checks={"crash_consistent": report.crash_cases >= 8,
                    "fuzz_clean": report.failures == 0,
                    "pitr": report.point_in_time_recovery,
                    "rpo_met": report.rpo_seconds <= report.max_rpo_seconds,
                    "rto_met": report.rto_seconds <= report.max_rto_seconds},
            producer_id=producer_id, verifier_id=verifier_id, now=now,
        )
        return persistence, recovery

    def qualify_security(
        self, *, manifest: TenantDeploymentManifest, threat_model_hash: str,
        critical_findings: int, high_findings: int, producer_id: str,
        verifier_id: str, now: datetime | None = None,
    ) -> QualificationDecision:
        if not _HASH.fullmatch(threat_model_hash) or min(critical_findings, high_findings) < 0:
            raise ProductionError("security evidence is invalid")
        return self.record_qualification(
            tenant_id=manifest.tenant_id, business_id=manifest.business_id,
            kind="security", release_version=manifest.release_version,
            artifact_hash=threat_model_hash,
            checks={"critical_findings_closed": critical_findings == 0,
                    "high_findings_closed": high_findings == 0,
                    "external_kms": manifest.attestation_provider == "external-kms",
                    "least_privilege_roles": len({manifest.runtime_database_role_ref,
                                                   manifest.migration_database_role_ref,
                                                   manifest.backup_database_role_ref}) == 3},
            producer_id=producer_id, verifier_id=verifier_id, now=now,
        )

    def qualify_upgrade(
        self, *, tenant_id: str, business_id: str, plan: UpgradePlan,
        producer_id: str, verifier_id: str, now: datetime | None = None,
    ) -> QualificationDecision:
        return self.record_qualification(
            tenant_id=tenant_id, business_id=business_id, kind="upgrade",
            release_version=plan.to_version, artifact_hash=plan.release_artifact_hash,
            checks={"backup": bool(plan.pre_upgrade_backup_hash),
                    "canary": plan.canary_passed,
                    "migration_rehearsed": plan.migration_rehearsal_passed,
                    "rollback_rehearsed": plan.rollback_rehearsal_passed,
                    "schema_current": plan.target_schema_version == LATEST_SCHEMA_VERSION},
            producer_id=producer_id, verifier_id=verifier_id, now=now,
        )

    def create_cutover_plan(
        self, *, tenant_id: str, business_id: str, source_system: str,
        capability_id: str, mode: str, owner_id: str, rollback_hash: str,
        now: datetime | None = None,
    ) -> str:
        owner = self.store.get_actor(owner_id)
        if (
            source_system not in {"agent-os-v1", "openclaw-legacy"}
            or mode not in {"read_only", "proposal", "shadow"}
            or not _HASH.fullmatch(rollback_hash)
            or owner is None
            or not owner.can_access(tenant_id=tenant_id, business_id=business_id)
            or not owner.roles & {"operations", "platform-reliability"}
        ):
            raise ProductionError("legacy cutover plan is unsafe or outside scope")
        plan_id = f"legacy-cutover-{uuid4()}"
        created_at = _aware(now or datetime.now(timezone.utc)).isoformat()
        with self.store._immediate_connection() as connection:
            connection.execute(
                """INSERT INTO legacy_cutover_plans(
                    plan_id, tenant_id, business_id, source_system,
                    capability_id, mode, owner_id, rollback_hash,
                    legacy_disable_allowed, external_side_effects_enabled,
                    created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?
                )""",
                (plan_id, tenant_id, business_id, source_system, capability_id,
                 mode, owner_id, rollback_hash, created_at),
            )
            connection.execute(
                """INSERT INTO legacy_cutover_events(
                    event_id, plan_id, tenant_id, business_id, stage, actor_id,
                    evidence_hash, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?
                )""",
                (f"cutover-event-{uuid4()}", plan_id, tenant_id, business_id,
                 CutoverStage.INVENTORIED.value, owner_id, rollback_hash, created_at),
            )
        return plan_id

    def advance_cutover(
        self, *, plan_id: str, stage: CutoverStage, actor_id: str,
        evidence_hash: str, now: datetime | None = None,
    ) -> None:
        if not _HASH.fullmatch(evidence_hash):
            raise ProductionError("cutover event requires a sha256 evidence hash")
        with self.store._immediate_connection() as connection:
            plan = connection.execute(
                "SELECT * FROM legacy_cutover_plans WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if plan is None:
                raise ProductionError("legacy cutover plan is missing")
            actor = self.store.get_actor(actor_id)
            if actor is None or not actor.can_access(
                tenant_id=plan["tenant_id"], business_id=plan["business_id"]
            ):
                raise ProductionError("cutover actor is outside business scope")
            latest = connection.execute(
                "SELECT stage FROM legacy_cutover_events WHERE plan_id=? ORDER BY rowid DESC LIMIT 1",
                (plan_id,),
            ).fetchone()["stage"]
            if stage is CutoverStage.ROLLED_BACK:
                if latest not in {CutoverStage.APPROVED.value, CutoverStage.CANARY_OBSERVED.value}:
                    raise ProductionError("rollback is not valid from the current stage")
            else:
                try:
                    expected = self._STAGES[self._STAGES.index(CutoverStage(latest)) + 1]
                except (ValueError, IndexError):
                    raise ProductionError("cutover has no valid next stage") from None
                if stage is not expected:
                    raise ProductionError(f"cutover must advance to {expected.value}")
            if stage is CutoverStage.APPROVED:
                if actor.actor_type is not ActorType.HUMAN or not actor.roles & {"business-owner", "operations"}:
                    raise ProductionError("cutover approval requires a scoped human owner")
            elif stage in {CutoverStage.SHADOW_COMPARED, CutoverStage.RECOVERY_VERIFIED}:
                if not actor.roles & {"qa", "verifier"}:
                    raise ProductionError("cutover comparison and recovery require QA")
            connection.execute(
                """INSERT INTO legacy_cutover_events(
                    event_id, plan_id, tenant_id, business_id, stage, actor_id,
                    evidence_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"cutover-event-{uuid4()}", plan_id, plan["tenant_id"],
                 plan["business_id"], stage.value, actor_id, evidence_hash,
                 _aware(now or datetime.now(timezone.utc)).isoformat()),
            )

    def readiness(
        self, *, tenant_id: str, business_id: str, release_version: str
    ) -> dict[str, Any]:
        with self.store._connection() as connection:
            rows = connection.execute(
                """SELECT kind, decision FROM production_qualifications
                   WHERE tenant_id=? AND business_id=? AND release_version=?
                   ORDER BY rowid""",
                (tenant_id, business_id, release_version),
            ).fetchall()
        latest = {row["kind"]: row["decision"] for row in rows}
        passed = {kind for kind, decision in latest.items() if decision == "passed"}
        missing = sorted(REQUIRED_QUALIFICATIONS - passed)
        return {
            "decision": QualificationDecision.PASSED.value if not missing else QualificationDecision.HELD.value,
            "missing": missing,
            "qualified": sorted(passed),
            "external_side_effects_enabled": False,
            "eligible_mode": "read_only_canary" if not missing else "none",
        }
