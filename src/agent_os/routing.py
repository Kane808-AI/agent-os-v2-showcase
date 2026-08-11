"""Governed, deterministic model-routing control plane.

This module selects routes and records evidence. It deliberately does not call
model providers or resolve credential references; the Goal 11 shadow runtime
consumes its selected decisions through a separate boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import re
import sqlite3
from typing import Any, Iterable
from uuid import uuid4

from .agents import AgentConstitution
from .storage import SQLiteStore


class RoutingError(ValueError):
    """Raised when routing configuration or evidence is invalid."""


class ReasoningTier(str, Enum):
    UTILITY = "utility"
    STANDARD = "standard"
    ADVANCED = "advanced"


class DataClass(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED_FINANCIAL = "restricted-financial"


class ProviderOutcome(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH_ERROR = "auth_error"
    SERVER_ERROR = "server_error"
    INVALID_RESPONSE = "invalid_response"


class RouteStatus(str, Enum):
    SELECTED = "selected"
    HELD = "held"


_REASONING_RANK = {
    ReasoningTier.UTILITY: 0,
    ReasoningTier.STANDARD: 1,
    ReasoningTier.ADVANCED: 2,
}
_ALLOWED_MODALITIES = frozenset(
    {"text", "code", "vision", "image", "audio", "video"}
)
_VERSIONED_REF = re.compile(r"^[A-Za-z0-9._-]+(?:[/@:][A-Za-z0-9._-]+)+$")
_CONTEXT_CLASS_TOKENS = {
    "small": 32_000,
    "medium": 128_000,
    "large": 256_000,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc(value: datetime | None) -> datetime:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise RoutingError("routing timestamps must be timezone-aware")
    return observed.astimezone(timezone.utc)


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _cost(tokens: int, rate_micros_per_million: int) -> int:
    if tokens < 0:
        raise RoutingError("token counts cannot be negative")
    return (
        tokens * rate_micros_per_million + 999_999
    ) // 1_000_000


@dataclass(frozen=True)
class ModelCatalogEntry:
    model_id: str
    provider_id: str
    provider_model_ref: str
    reasoning_tier: ReasoningTier
    tool_use: bool
    structured_output: bool
    modalities: frozenset[str]
    context_window_tokens: int
    allowed_data_classes: frozenset[DataClass]
    input_micros_per_million: int
    output_micros_per_million: int
    quality_score: int
    evaluation_version: str
    enabled: bool = True

    def __post_init__(self) -> None:
        for label, value in (
            ("model_id", self.model_id),
            ("provider_id", self.provider_id),
            ("provider_model_ref", self.provider_model_ref),
            ("evaluation_version", self.evaluation_version),
        ):
            if not value or value != value.strip():
                raise RoutingError(f"{label} must be a non-empty trimmed value")
        ref = self.provider_model_ref.lower()
        if (
            not _VERSIONED_REF.fullmatch(self.provider_model_ref)
            or "latest" in ref
            or ref == "auto"
            or ref.endswith("/auto")
        ):
            raise RoutingError(
                "provider_model_ref must be an exact versioned model reference"
            )
        if not self.modalities or not self.modalities <= _ALLOWED_MODALITIES:
            raise RoutingError("catalog modalities are invalid")
        if not self.allowed_data_classes:
            raise RoutingError("catalog data classes cannot be empty")
        if self.context_window_tokens <= 0:
            raise RoutingError("context window must be positive")
        if self.input_micros_per_million < 0:
            raise RoutingError("input price cannot be negative")
        if self.output_micros_per_million < 0:
            raise RoutingError("output price cannot be negative")
        if not 0 <= self.quality_score <= 100:
            raise RoutingError("quality score must be between 0 and 100")

    def payload(self) -> dict[str, Any]:
        return {
            "allowed_data_classes": sorted(
                item.value for item in self.allowed_data_classes
            ),
            "context_window_tokens": self.context_window_tokens,
            "enabled": self.enabled,
            "evaluation_version": self.evaluation_version,
            "input_micros_per_million": self.input_micros_per_million,
            "modalities": sorted(self.modalities),
            "model_id": self.model_id,
            "output_micros_per_million": self.output_micros_per_million,
            "provider_id": self.provider_id,
            "provider_model_ref": self.provider_model_ref,
            "quality_score": self.quality_score,
            "reasoning_tier": self.reasoning_tier.value,
            "structured_output": self.structured_output,
            "tool_use": self.tool_use,
        }


@dataclass(frozen=True)
class RouteRequest:
    request_id: str
    tenant_id: str
    business_id: str
    reasoning_tier: ReasoningTier
    data_class: DataClass
    required_modalities: frozenset[str] = field(
        default_factory=lambda: frozenset({"text"})
    )
    requires_tool_use: bool = False
    requires_structured_output: bool = False
    required_context_tokens: int = 1
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    max_cost_micros: int | None = None
    preferred_provider_ids: tuple[str, ...] = ()
    excluded_model_ids: frozenset[str] = field(default_factory=frozenset)
    independent_from_provider_id: str | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("request_id", self.request_id),
            ("tenant_id", self.tenant_id),
            ("business_id", self.business_id),
        ):
            if not value or value != value.strip():
                raise RoutingError(f"{label} must be a non-empty trimmed value")
        if (
            not self.required_modalities
            or not self.required_modalities <= _ALLOWED_MODALITIES
        ):
            raise RoutingError("required modalities are invalid")
        if self.required_context_tokens <= 0:
            raise RoutingError("required context must be positive")
        if self.estimated_input_tokens < 0 or self.estimated_output_tokens < 0:
            raise RoutingError("estimated token counts cannot be negative")
        if self.max_cost_micros is not None and self.max_cost_micros < 0:
            raise RoutingError("maximum cost cannot be negative")
        if len(set(self.preferred_provider_ids)) != len(
            self.preferred_provider_ids
        ):
            raise RoutingError("preferred providers cannot contain duplicates")

    def payload(self) -> dict[str, Any]:
        return {
            "business_id": self.business_id,
            "data_class": self.data_class.value,
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "excluded_model_ids": sorted(self.excluded_model_ids),
            "independent_from_provider_id": self.independent_from_provider_id,
            "max_cost_micros": self.max_cost_micros,
            "preferred_provider_ids": list(self.preferred_provider_ids),
            "reasoning_tier": self.reasoning_tier.value,
            "request_id": self.request_id,
            "required_context_tokens": self.required_context_tokens,
            "required_modalities": sorted(self.required_modalities),
            "requires_structured_output": self.requires_structured_output,
            "requires_tool_use": self.requires_tool_use,
            "tenant_id": self.tenant_id,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RouteRequest":
        return cls(
            request_id=payload["request_id"],
            tenant_id=payload["tenant_id"],
            business_id=payload["business_id"],
            reasoning_tier=ReasoningTier(payload["reasoning_tier"]),
            data_class=DataClass(payload["data_class"]),
            required_modalities=frozenset(payload["required_modalities"]),
            requires_tool_use=payload["requires_tool_use"],
            requires_structured_output=payload[
                "requires_structured_output"
            ],
            required_context_tokens=payload["required_context_tokens"],
            estimated_input_tokens=payload["estimated_input_tokens"],
            estimated_output_tokens=payload["estimated_output_tokens"],
            max_cost_micros=payload["max_cost_micros"],
            preferred_provider_ids=tuple(payload["preferred_provider_ids"]),
            excluded_model_ids=frozenset(payload["excluded_model_ids"]),
            independent_from_provider_id=payload[
                "independent_from_provider_id"
            ],
        )


@dataclass(frozen=True)
class RouteDecision:
    decision_id: str
    request_id: str
    tenant_id: str
    business_id: str
    catalog_version: str
    status: RouteStatus
    model_id: str | None
    provider_id: str | None
    credential_id: str | None
    policy_revision_id: str | None
    estimated_cost_micros: int
    candidate_order: tuple[str, ...]
    rejection_reasons: dict[str, tuple[str, ...]]
    previous_decision_id: str | None
    is_circuit_probe: bool
    created_at: str


def route_request_from_constitution(
    constitution: AgentConstitution,
    *,
    request_id: str,
    tenant_id: str,
    business_id: str,
    data_class: DataClass,
    estimated_input_tokens: int,
    estimated_output_tokens: int,
    max_cost_micros: int | None = None,
    preferred_provider_ids: tuple[str, ...] = (),
    independent_from_provider_id: str | None = None,
) -> RouteRequest:
    """Translate role capabilities without granting provider selection."""
    requirements = constitution.model_requirements
    if data_class.value not in requirements["data_classes"]:
        raise RoutingError(
            "work data class is outside the constitution model requirements"
        )
    if (
        requirements["independent_evaluator"]
        and independent_from_provider_id is None
    ):
        raise RoutingError(
            "independent evaluator routing requires the producer provider"
        )
    return RouteRequest(
        request_id=request_id,
        tenant_id=tenant_id,
        business_id=business_id,
        reasoning_tier=ReasoningTier(requirements["reasoning_tier"]),
        data_class=data_class,
        required_modalities=frozenset(requirements["modalities"]),
        requires_tool_use=requirements["tool_use"],
        requires_structured_output=requirements["structured_output"],
        required_context_tokens=_CONTEXT_CLASS_TOKENS[
            requirements["context_class"]
        ],
        estimated_input_tokens=estimated_input_tokens,
        estimated_output_tokens=estimated_output_tokens,
        max_cost_micros=max_cost_micros,
        preferred_provider_ids=preferred_provider_ids,
        independent_from_provider_id=(
            independent_from_provider_id
            if requirements["independent_evaluator"]
            else None
        ),
    )


class ModelRouter:
    """Persisted deterministic selector with explicit failover."""

    FAILURE_THRESHOLD = 3
    CIRCUIT_COOLDOWN = timedelta(minutes=5)

    def __init__(self, store: SQLiteStore):
        self.store = store

    def register_catalog(
        self,
        catalog_version: str,
        entries: Iterable[ModelCatalogEntry],
        *,
        created_at: datetime | None = None,
    ) -> str:
        if not re.fullmatch(r"\d+\.\d+\.\d+", catalog_version):
            raise RoutingError("catalog version must use semantic x.y.z format")
        materialized = sorted(entries, key=lambda entry: entry.model_id)
        if not materialized:
            raise RoutingError("catalog must contain at least one model")
        if len({entry.model_id for entry in materialized}) != len(materialized):
            raise RoutingError("catalog model IDs must be unique")
        payload = [entry.payload() for entry in materialized]
        content_hash = hashlib.sha256(
            _canonical(payload).encode("utf-8")
        ).hexdigest()
        timestamp = _utc(created_at).isoformat()
        with self.store._immediate_connection() as connection:
            connection.execute(
                """
                INSERT INTO model_catalog_versions(
                    catalog_version, content_hash, created_at
                ) VALUES (?, ?, ?)
                """,
                (catalog_version, content_hash, timestamp),
            )
            for entry in materialized:
                connection.execute(
                    """
                    INSERT INTO model_catalog_entries(
                        catalog_version, model_id, provider_id,
                        provider_model_ref, reasoning_tier, tool_use,
                        structured_output, modalities_json,
                        context_window_tokens, allowed_data_classes_json,
                        input_micros_per_million,
                        output_micros_per_million, quality_score,
                        evaluation_version, enabled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        catalog_version,
                        entry.model_id,
                        entry.provider_id,
                        entry.provider_model_ref,
                        entry.reasoning_tier.value,
                        int(entry.tool_use),
                        int(entry.structured_output),
                        _canonical(sorted(entry.modalities)),
                        entry.context_window_tokens,
                        _canonical(
                            sorted(
                                item.value
                                for item in entry.allowed_data_classes
                            )
                        ),
                        entry.input_micros_per_million,
                        entry.output_micros_per_million,
                        entry.quality_score,
                        entry.evaluation_version,
                        int(entry.enabled),
                    ),
                )
        return content_hash

    def activate_catalog(
        self,
        catalog_version: str,
        *,
        activation_id: str | None = None,
        activated_at: datetime | None = None,
    ) -> str:
        identity = activation_id or f"activation-{uuid4()}"
        with self.store._immediate_connection() as connection:
            connection.execute(
                """
                INSERT INTO model_catalog_activation_events(
                    activation_id, catalog_version, activated_at
                ) VALUES (?, ?, ?)
                """,
                (identity, catalog_version, _utc(activated_at).isoformat()),
            )
        return identity

    def bind_credential(
        self,
        *,
        credential_id: str,
        tenant_id: str,
        business_id: str,
        provider_id: str,
        credential_ref: str,
        created_at: datetime | None = None,
    ) -> None:
        if not credential_ref.startswith(("vault://", "env://")):
            raise RoutingError(
                "only vault:// or env:// credential references are accepted"
            )
        if any(marker in credential_ref.lower() for marker in ("api_key=", "bearer ")):
            raise RoutingError("credential material must not be persisted")
        with self.store._immediate_connection() as connection:
            connection.execute(
                """
                INSERT INTO provider_credentials(
                    credential_id, tenant_id, business_id, provider_id,
                    credential_ref, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    credential_id,
                    tenant_id,
                    business_id,
                    provider_id,
                    credential_ref,
                    _utc(created_at).isoformat(),
                ),
            )

    def revise_provider_policy(
        self,
        *,
        policy_revision_id: str,
        tenant_id: str,
        business_id: str,
        provider_id: str,
        credential_id: str,
        enabled: bool,
        allowed_data_classes: frozenset[DataClass],
        monthly_budget_micros: int,
        allowed_model_ids: frozenset[str] = frozenset(),
        created_at: datetime | None = None,
    ) -> int:
        if not allowed_data_classes:
            raise RoutingError("policy data classes cannot be empty")
        if monthly_budget_micros < 0:
            raise RoutingError("monthly budget cannot be negative")
        with self.store._immediate_connection() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(revision), 0) + 1 AS next_revision
                FROM provider_policy_revisions
                WHERE tenant_id = ? AND business_id = ? AND provider_id = ?
                """,
                (tenant_id, business_id, provider_id),
            ).fetchone()
            revision = int(row["next_revision"])
            connection.execute(
                """
                INSERT INTO provider_policy_revisions(
                    policy_revision_id, tenant_id, business_id, provider_id,
                    revision, credential_id, enabled,
                    allowed_data_classes_json, allowed_model_ids_json,
                    monthly_budget_micros, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy_revision_id,
                    tenant_id,
                    business_id,
                    provider_id,
                    revision,
                    credential_id,
                    int(enabled),
                    _canonical(
                        sorted(item.value for item in allowed_data_classes)
                    ),
                    _canonical(sorted(allowed_model_ids)),
                    monthly_budget_micros,
                    _utc(created_at).isoformat(),
                ),
            )
        return revision

    def route(
        self,
        request: RouteRequest,
        *,
        previous_decision_id: str | None = None,
        now: datetime | None = None,
    ) -> RouteDecision:
        routed_at = _utc(now)
        request_json = _canonical(request.payload())
        request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        with self.store._immediate_connection() as connection:
            existing = connection.execute(
                """
                SELECT * FROM routing_decisions
                WHERE tenant_id = ? AND business_id = ?
                  AND request_id = ?
                """,
                (
                    request.tenant_id,
                    request.business_id,
                    request.request_id,
                ),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise RoutingError(
                        "request ID is already bound to different semantics"
                    )
                return self._decision(existing)
            business = connection.execute(
                """
                SELECT 1 FROM businesses
                WHERE tenant_id = ? AND business_id = ?
                """,
                (request.tenant_id, request.business_id),
            ).fetchone()
            if business is None:
                raise RoutingError("routing request crosses tenant scope")
            activation = connection.execute(
                """
                SELECT catalog_version
                FROM model_catalog_activation_events
                WHERE activated_at <= ?
                ORDER BY activated_at DESC, rowid DESC
                LIMIT 1
                """,
                (routed_at.isoformat(),),
            ).fetchone()
            if activation is None:
                raise RoutingError("no model catalog is active")
            catalog_version = activation["catalog_version"]
            entries = connection.execute(
                """
                SELECT * FROM model_catalog_entries
                WHERE catalog_version = ? AND enabled = 1
                ORDER BY model_id
                """,
                (catalog_version,),
            ).fetchall()
            policies = {
                row["provider_id"]: row
                for row in connection.execute(
                    """
                    SELECT policy.*
                    FROM provider_policy_revisions AS policy
                    WHERE policy.tenant_id = ? AND policy.business_id = ?
                      AND policy.created_at <= ?
                      AND policy.revision = (
                          SELECT MAX(candidate.revision)
                          FROM provider_policy_revisions AS candidate
                          WHERE candidate.tenant_id = policy.tenant_id
                            AND candidate.business_id = policy.business_id
                            AND candidate.provider_id = policy.provider_id
                            AND candidate.created_at <= ?
                      )
                    """,
                    (
                        request.tenant_id,
                        request.business_id,
                        routed_at.isoformat(),
                        routed_at.isoformat(),
                    ),
                ).fetchall()
            }
            month_start = routed_at.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            spent = {
                row["provider_id"]: int(row["spent"])
                for row in connection.execute(
                    """
                    SELECT provider_id, SUM(cost_micros) AS spent
                    FROM (
                        SELECT provider_id, cost_micros
                        FROM model_usage_records
                        WHERE tenant_id = ? AND business_id = ?
                          AND created_at >= ? AND created_at <= ?
                        UNION ALL
                        SELECT decision.provider_id,
                               decision.estimated_cost_micros AS cost_micros
                        FROM routing_decisions AS decision
                        LEFT JOIN model_usage_records AS usage
                          ON usage.decision_id = decision.decision_id
                        WHERE decision.tenant_id = ?
                          AND decision.business_id = ?
                          AND decision.status = 'selected'
                          AND decision.created_at >= ?
                          AND decision.created_at <= ?
                          AND usage.usage_id IS NULL
                    )
                    GROUP BY provider_id
                    """,
                    (
                        request.tenant_id,
                        request.business_id,
                        month_start,
                        routed_at.isoformat(),
                        request.tenant_id,
                        request.business_id,
                        month_start,
                        routed_at.isoformat(),
                    ),
                ).fetchall()
            }
            rejections: dict[str, tuple[str, ...]] = {}
            candidates: list[tuple[tuple[Any, ...], sqlite3.Row, sqlite3.Row, int, bool]] = []
            for entry in entries:
                policy = policies.get(entry["provider_id"])
                reasons = self._compatibility_reasons(
                    entry, policy, request, spent
                )
                probe = False
                circuit = connection.execute(
                    """
                    SELECT * FROM model_circuit_states
                    WHERE tenant_id = ? AND business_id = ?
                      AND provider_id = ? AND model_id = ?
                    """,
                    (
                        request.tenant_id,
                        request.business_id,
                        entry["provider_id"],
                        entry["model_id"],
                    ),
                ).fetchone()
                if circuit is not None and circuit["circuit_state"] == "open":
                    if (
                        circuit["open_until"] is not None
                        and routed_at
                        >= datetime.fromisoformat(circuit["open_until"])
                        and not circuit["probe_in_flight"]
                    ):
                        probe = True
                    else:
                        reasons.append("circuit_open")
                if circuit is not None and circuit["probe_in_flight"]:
                    reasons.append("circuit_probe_in_flight")
                if reasons:
                    rejections[entry["model_id"]] = tuple(sorted(set(reasons)))
                    continue
                estimated = _cost(
                    request.estimated_input_tokens,
                    entry["input_micros_per_million"],
                ) + _cost(
                    request.estimated_output_tokens,
                    entry["output_micros_per_million"],
                )
                preferred = (
                    request.preferred_provider_ids.index(entry["provider_id"])
                    if entry["provider_id"] in request.preferred_provider_ids
                    else len(request.preferred_provider_ids)
                )
                key = (
                    preferred,
                    _REASONING_RANK[ReasoningTier(entry["reasoning_tier"])],
                    estimated,
                    -entry["quality_score"],
                    entry["model_id"],
                )
                candidates.append((key, entry, policy, estimated, probe))
            candidates.sort(key=lambda candidate: candidate[0])
            selected = candidates[0] if candidates else None
            if selected is not None and selected[4]:
                entry = selected[1]
                connection.execute(
                    """
                    UPDATE model_circuit_states
                    SET circuit_state = 'half_open', probe_in_flight = 1,
                        updated_at = ?
                    WHERE tenant_id = ? AND business_id = ?
                      AND provider_id = ? AND model_id = ?
                      AND circuit_state = 'open' AND probe_in_flight = 0
                    """,
                    (
                        routed_at.isoformat(),
                        request.tenant_id,
                        request.business_id,
                        entry["provider_id"],
                        entry["model_id"],
                    ),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RoutingError("circuit probe was claimed concurrently")
            decision_id = f"route-{uuid4()}"
            candidate_order = tuple(
                candidate[1]["model_id"] for candidate in candidates
            )
            status = (
                RouteStatus.SELECTED if selected is not None else RouteStatus.HELD
            )
            entry = selected[1] if selected is not None else None
            policy = selected[2] if selected is not None else None
            estimated = selected[3] if selected is not None else 0
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
                    decision_id,
                    request.request_id,
                    request.tenant_id,
                    request.business_id,
                    request_hash,
                    request_json,
                    catalog_version,
                    status.value,
                    entry["model_id"] if entry is not None else None,
                    entry["provider_id"] if entry is not None else None,
                    policy["credential_id"] if policy is not None else None,
                    (
                        policy["policy_revision_id"]
                        if policy is not None
                        else None
                    ),
                    estimated,
                    _canonical(candidate_order),
                    _canonical(rejections),
                    previous_decision_id,
                    int(bool(selected is not None and selected[4])),
                    routed_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM routing_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            return self._decision(row)

    def route_fallback(
        self,
        previous_decision_id: str,
        *,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> RouteDecision:
        with self.store._connection() as connection:
            previous = connection.execute(
                "SELECT * FROM routing_decisions WHERE decision_id = ?",
                (previous_decision_id,),
            ).fetchone()
            if previous is None or previous["status"] != "selected":
                raise RoutingError("fallback requires a selected prior decision")
            usage = connection.execute(
                """
                SELECT outcome FROM model_usage_records
                WHERE decision_id = ?
                """,
                (previous_decision_id,),
            ).fetchone()
            if usage is None or usage["outcome"] == ProviderOutcome.SUCCESS.value:
                raise RoutingError(
                    "fallback requires a recorded non-success provider outcome"
                )
            original = RouteRequest.from_payload(
                json.loads(previous["request_json"])
            )
        fallback = replace(
            original,
            request_id=(
                request_id
                or f"{original.request_id}:fallback:{previous_decision_id}"
            ),
            excluded_model_ids=(
                original.excluded_model_ids | {previous["model_id"]}
            ),
        )
        return self.route(
            fallback,
            previous_decision_id=previous_decision_id,
            now=now,
        )

    def record_usage(
        self,
        *,
        usage_id: str,
        decision_id: str,
        input_tokens: int,
        output_tokens: int,
        outcome: ProviderOutcome,
        latency_ms: int,
        observed_at: datetime | None = None,
    ) -> int:
        if latency_ms < 0:
            raise RoutingError("latency cannot be negative")
        timestamp = _utc(observed_at)
        with self.store._immediate_connection() as connection:
            decision = connection.execute(
                """
                SELECT decision.*, entry.input_micros_per_million,
                       entry.output_micros_per_million
                FROM routing_decisions AS decision
                JOIN model_catalog_entries AS entry
                  ON entry.catalog_version = decision.catalog_version
                 AND entry.model_id = decision.model_id
                 AND entry.provider_id = decision.provider_id
                WHERE decision.decision_id = ? AND decision.status = 'selected'
                """,
                (decision_id,),
            ).fetchone()
            if decision is None:
                raise RoutingError("usage requires a selected route decision")
            if timestamp < datetime.fromisoformat(decision["created_at"]):
                raise RoutingError("usage cannot predate its route decision")
            cost_micros = _cost(
                input_tokens, decision["input_micros_per_million"]
            ) + _cost(
                output_tokens, decision["output_micros_per_million"]
            )
            connection.execute(
                """
                INSERT INTO model_usage_records(
                    usage_id, decision_id, tenant_id, business_id,
                    provider_id, model_id, credential_id, input_tokens,
                    output_tokens, cost_micros, outcome, latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    usage_id,
                    decision_id,
                    decision["tenant_id"],
                    decision["business_id"],
                    decision["provider_id"],
                    decision["model_id"],
                    decision["credential_id"],
                    input_tokens,
                    output_tokens,
                    cost_micros,
                    outcome.value,
                    latency_ms,
                    timestamp.isoformat(),
                ),
            )
            circuit = connection.execute(
                """
                SELECT * FROM model_circuit_states
                WHERE tenant_id = ? AND business_id = ?
                  AND provider_id = ? AND model_id = ?
                """,
                (
                    decision["tenant_id"],
                    decision["business_id"],
                    decision["provider_id"],
                    decision["model_id"],
                ),
            ).fetchone()
            prior_failures = (
                int(circuit["consecutive_failures"])
                if circuit is not None
                else 0
            )
            if outcome is ProviderOutcome.SUCCESS:
                state = "closed"
                failures = 0
                open_until = None
            else:
                failures = prior_failures + 1
                must_open = (
                    outcome is ProviderOutcome.AUTH_ERROR
                    or (circuit is not None and circuit["circuit_state"] == "half_open")
                    or failures >= self.FAILURE_THRESHOLD
                )
                state = "open" if must_open else "closed"
                open_until = (
                    (timestamp + self.CIRCUIT_COOLDOWN).isoformat()
                    if must_open
                    else None
                )
            connection.execute(
                """
                INSERT INTO model_circuit_states(
                    tenant_id, business_id, provider_id, model_id,
                    circuit_state, consecutive_failures, open_until,
                    probe_in_flight, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(tenant_id, business_id, provider_id, model_id)
                DO UPDATE SET
                    circuit_state = excluded.circuit_state,
                    consecutive_failures = excluded.consecutive_failures,
                    open_until = excluded.open_until,
                    probe_in_flight = 0,
                    updated_at = excluded.updated_at
                """,
                (
                    decision["tenant_id"],
                    decision["business_id"],
                    decision["provider_id"],
                    decision["model_id"],
                    state,
                    failures,
                    open_until,
                    timestamp.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO model_health_events(
                    health_event_id, decision_id, tenant_id, business_id,
                    provider_id, model_id, outcome, circuit_state,
                    consecutive_failures, open_until, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"health-{uuid4()}",
                    decision_id,
                    decision["tenant_id"],
                    decision["business_id"],
                    decision["provider_id"],
                    decision["model_id"],
                    outcome.value,
                    state,
                    failures,
                    open_until,
                    timestamp.isoformat(),
                ),
            )
        return cost_micros

    def _compatibility_reasons(
        self,
        entry: sqlite3.Row,
        policy: sqlite3.Row | None,
        request: RouteRequest,
        spent: dict[str, int],
    ) -> list[str]:
        reasons: list[str] = []
        if entry["model_id"] in request.excluded_model_ids:
            reasons.append("explicitly_excluded")
        if (
            _REASONING_RANK[ReasoningTier(entry["reasoning_tier"])]
            < _REASONING_RANK[request.reasoning_tier]
        ):
            reasons.append("reasoning_tier")
        if request.requires_tool_use and not entry["tool_use"]:
            reasons.append("tool_use")
        if request.requires_structured_output and not entry["structured_output"]:
            reasons.append("structured_output")
        if not request.required_modalities <= set(
            json.loads(entry["modalities_json"])
        ):
            reasons.append("modalities")
        if request.required_context_tokens > entry["context_window_tokens"]:
            reasons.append("context_window")
        if request.data_class.value not in json.loads(
            entry["allowed_data_classes_json"]
        ):
            reasons.append("model_data_policy")
        if (
            request.independent_from_provider_id is not None
            and entry["provider_id"] == request.independent_from_provider_id
        ):
            reasons.append("independence")
        if policy is None:
            reasons.append("provider_not_configured")
            return reasons
        if not policy["enabled"]:
            reasons.append("provider_disabled")
        if request.data_class.value not in json.loads(
            policy["allowed_data_classes_json"]
        ):
            reasons.append("tenant_data_policy")
        allowed_models = set(json.loads(policy["allowed_model_ids_json"]))
        if allowed_models and entry["model_id"] not in allowed_models:
            reasons.append("tenant_model_policy")
        estimated = _cost(
            request.estimated_input_tokens,
            entry["input_micros_per_million"],
        ) + _cost(
            request.estimated_output_tokens,
            entry["output_micros_per_million"],
        )
        if (
            request.max_cost_micros is not None
            and estimated > request.max_cost_micros
        ):
            reasons.append("request_cost_ceiling")
        if (
            spent.get(entry["provider_id"], 0) + estimated
            > policy["monthly_budget_micros"]
        ):
            reasons.append("provider_monthly_budget")
        return reasons

    @staticmethod
    def _decision(row: sqlite3.Row) -> RouteDecision:
        return RouteDecision(
            decision_id=row["decision_id"],
            request_id=row["request_id"],
            tenant_id=row["tenant_id"],
            business_id=row["business_id"],
            catalog_version=row["catalog_version"],
            status=RouteStatus(row["status"]),
            model_id=row["model_id"],
            provider_id=row["provider_id"],
            credential_id=row["credential_id"],
            policy_revision_id=row["policy_revision_id"],
            estimated_cost_micros=row["estimated_cost_micros"],
            candidate_order=tuple(json.loads(row["candidate_order_json"])),
            rejection_reasons={
                key: tuple(value)
                for key, value in json.loads(
                    row["rejection_reasons_json"]
                ).items()
            },
            previous_decision_id=row["previous_decision_id"],
            is_circuit_probe=bool(row["is_circuit_probe"]),
            created_at=row["created_at"],
        )
