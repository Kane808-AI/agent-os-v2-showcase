"""Reusable Goal 13 capability packs and aggregate performance evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

class PortfolioError(ValueError):
    """Raised when a capability pack or aggregate report is unsafe."""


class PackDecision(StrEnum):
    ACCEPTED = "accepted"
    HELD = "held"


class AggregateVerificationDecision(StrEnum):
    VERIFIED = "verified"
    INCONCLUSIVE = "inconclusive"
    REJECTED = "rejected"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise PortfolioError("portfolio timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _text(label: str, value: str) -> None:
    if not value or value != value.strip():
        raise PortfolioError(f"{label} must be a non-empty trimmed value")


@dataclass(frozen=True, slots=True)
class CapabilityAcceptance:
    pack_id: str
    pack_version: str
    pack_hash: str
    evaluator_version: str
    case_count: int
    passed_count: int
    passed: bool


@dataclass(frozen=True, slots=True)
class AggregateSnapshot:
    channel: str
    offer_key: str
    source_system: str
    source_ref: str
    window_start: datetime
    window_end: datetime
    impressions: int
    engagements: int
    content_clicks: int
    outbound_clicks: int
    conversions: int
    gross_revenue_minor: int
    commission_minor: int
    minimum_outbound_clicks: int
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for label in ("channel", "offer_key", "source_system", "source_ref"):
            _text(label, getattr(self, label))
        if not self.source_system.endswith("-readonly"):
            raise PortfolioError("aggregate source must be explicitly read-only")
        start, end = _aware(self.window_start), _aware(self.window_end)
        if start >= end:
            raise PortfolioError("aggregate reporting window is invalid")
        counts = (
            self.impressions,
            self.engagements,
            self.content_clicks,
            self.outbound_clicks,
            self.conversions,
        )
        if any(value < 0 for value in counts):
            raise PortfolioError("aggregate counts cannot be negative")
        if not (
            self.conversions
            <= self.outbound_clicks
            <= self.content_clicks
            <= self.engagements
            <= self.impressions
        ):
            raise PortfolioError("aggregate funnel counts are inconsistent")
        if (
            self.gross_revenue_minor < 0
            or not 0 <= self.commission_minor <= self.gross_revenue_minor
        ):
            raise PortfolioError("aggregate economics are inconsistent")
        if self.minimum_outbound_clicks <= 0:
            raise PortfolioError("aggregate sample floor must be positive")
        if not self.evidence_refs or len(set(self.evidence_refs)) != len(
            self.evidence_refs
        ):
            raise PortfolioError("aggregate snapshot requires unique evidence")
        for reference in self.evidence_refs:
            _text("evidence_ref", reference)

    def metrics(self) -> dict[str, Any]:
        return {
            "commission_minor": self.commission_minor,
            "content_clicks": self.content_clicks,
            "conversion_rate_bps": (
                self.conversions * 10_000 // self.outbound_clicks
                if self.outbound_clicks
                else 0
            ),
            "conversions": self.conversions,
            "engagement_rate_bps": (
                self.engagements * 10_000 // self.impressions
                if self.impressions
                else 0
            ),
            "engagements": self.engagements,
            "gross_revenue_minor": self.gross_revenue_minor,
            "impressions": self.impressions,
            "outbound_click_rate_bps": (
                self.outbound_clicks * 10_000 // self.impressions
                if self.impressions
                else 0
            ),
            "outbound_clicks": self.outbound_clicks,
            "sufficient_sample": (
                self.outbound_clicks >= self.minimum_outbound_clicks
            ),
        }


@dataclass(frozen=True, slots=True)
class AggregateResult:
    snapshot_id: str
    metrics: dict[str, Any]
    snapshot_hash: str
    evidence_class: str = "directional_aggregate"


class CapabilityPackCatalog:
    """Load and deterministically accept business-neutral capability packs."""

    EVALUATOR_VERSION = "capability-pack-v1"
    REQUIRED_PACK_IDS = frozenset({
        "accounting",
        "applications",
        "commerce",
        "customer-success",
        "digital-marketing-consulting",
        "engineering",
        "finance",
        "operations",
        "physical-products",
        "qa",
        "research",
        "sales",
        "youtube",
    })
    _IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]*$")
    _VERSION = re.compile(r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")

    def __init__(self, departments_root: str | Path, agents_root: str | Path):
        self.departments_root = Path(departments_root).resolve()
        self.agents_root = Path(agents_root).resolve()
        self.policy = self._load_json(
            self.departments_root / "capability-pack-policy.json"
        )
        self._validate_policy()
        registry = self._load_json(self.agents_root / "registry.json")
        self.roles = {
            item["constitution_id"] for item in registry.get("roles", ())
        }
        self.packs = self._load_packs()

    def _load_json(self, path: Path) -> dict[str, Any]:
        resolved = path.resolve()
        if not resolved.is_relative_to(
            self.departments_root
        ) and not resolved.is_relative_to(self.agents_root):
            raise PortfolioError("capability pack path escapes its root")
        try:
            value = json.loads(resolved.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise PortfolioError(f"cannot load capability file {path.name}") from error
        if not isinstance(value, dict):
            raise PortfolioError(f"capability file {path.name} must be an object")
        return value

    def _validate_policy(self) -> None:
        required = {
            "schema_version",
            "policy_version",
            "evaluator_version",
            "allowed_modes",
            "global_forbidden_actions",
            "required_pack_ids",
        }
        if set(self.policy) != required or self.policy["schema_version"] != 1:
            raise PortfolioError("capability pack policy shape is invalid")
        if self.policy["evaluator_version"] != self.EVALUATOR_VERSION:
            raise PortfolioError("capability evaluator version is incompatible")
        allowed = self.policy["allowed_modes"]
        if set(allowed) != {"read_only", "proposal", "simulated"}:
            raise PortfolioError("capability modes must remain non-executing")
        forbidden = set(self.policy["global_forbidden_actions"])
        required_forbidden = {
            "ads.spend.execute",
            "affiliate.link.modify",
            "banking.money-movement",
            "external.contact",
            "external.publish",
            "payout.modify",
            "production.write",
        }
        if not required_forbidden <= forbidden:
            raise PortfolioError("global capability boundary is incomplete")
        required_packs = self.policy["required_pack_ids"]
        if not required_packs or len(required_packs) != len(set(required_packs)):
            raise PortfolioError("required capability pack IDs are invalid")
        if set(required_packs) != self.REQUIRED_PACK_IDS:
            raise PortfolioError("required portfolio coverage is incomplete")

    def _load_packs(self) -> dict[str, dict[str, Any]]:
        paths = sorted(self.departments_root.glob("*/capability-pack.json"))
        packs: dict[str, dict[str, Any]] = {}
        capability_ids: set[str] = set()
        for path in paths:
            pack = self._load_json(path)
            self._validate_pack(pack, path)
            pack_id = pack["pack_id"]
            if pack_id in packs:
                raise PortfolioError(f"duplicate capability pack {pack_id}")
            for capability in pack["capabilities"]:
                capability_id = capability["capability_id"]
                if capability_id in capability_ids:
                    raise PortfolioError(
                        f"duplicate capability ID {capability_id}"
                    )
                capability_ids.add(capability_id)
            packs[pack_id] = pack
        missing = set(self.policy["required_pack_ids"]) - set(packs)
        if missing:
            raise PortfolioError(
                f"required capability packs are missing: {sorted(missing)}"
            )
        return packs

    def _validate_pack(self, pack: dict[str, Any], path: Path) -> None:
        required = {
            "schema_version",
            "pack_id",
            "version",
            "status",
            "policy_version",
            "department",
            "owner_role",
            "verifier_role",
            "execution_boundary",
            "objective_metrics",
            "input_sources",
            "capabilities",
            "additional_forbidden_actions",
            "evaluation_cases",
        }
        if set(pack) != required or pack["schema_version"] != 1:
            raise PortfolioError(f"{path.name} has an invalid pack shape")
        for label in ("pack_id", "department", "owner_role", "verifier_role"):
            if not self._IDENTIFIER.fullmatch(pack[label]):
                raise PortfolioError(f"{path.name} has invalid {label}")
        if not self._VERSION.fullmatch(pack["version"]):
            raise PortfolioError(f"{path.name} must use a semantic version")
        if (
            pack["status"] != "accepted"
            or pack["policy_version"] != self.policy["policy_version"]
            or pack["execution_boundary"] != "shadow-only"
        ):
            raise PortfolioError(f"{path.name} crosses the Goal 13 boundary")
        if pack["owner_role"] == pack["verifier_role"]:
            raise PortfolioError(f"{path.name} lacks independent verification")
        if pack["owner_role"] not in self.roles or pack["verifier_role"] not in self.roles:
            raise PortfolioError(f"{path.name} references an unknown role")
        if not pack["objective_metrics"] or len(pack["objective_metrics"]) != len(
            set(pack["objective_metrics"])
        ):
            raise PortfolioError(f"{path.name} requires unique objective metrics")
        for source in pack["input_sources"]:
            if set(source) != {"source_type", "mode"} or source["mode"] != "read_only":
                raise PortfolioError(f"{path.name} has a non-read-only input")
        if not pack["capabilities"]:
            raise PortfolioError(f"{path.name} has no capabilities")
        for capability in pack["capabilities"]:
            self._validate_capability(pack, capability, path)
        cases = pack["evaluation_cases"]
        capability_ids = {
            capability["capability_id"] for capability in pack["capabilities"]
        }
        case_ids: set[str] = set()
        for case in cases:
            if set(case) != {
                "case_id", "capability_id", "requested_mode", "action_type",
                "expected",
            }:
                raise PortfolioError(f"{path.name} has an invalid evaluation case")
            if (
                not self._IDENTIFIER.fullmatch(case["case_id"])
                or case["capability_id"] not in capability_ids
                or case["case_id"] in case_ids
                or case["expected"] not in {"accepted", "held"}
            ):
                raise PortfolioError(f"{path.name} has an invalid evaluation case")
            case_ids.add(case["case_id"])
        expected = {case.get("expected") for case in cases}
        if len(cases) < 2 or expected != {"accepted", "held"}:
            raise PortfolioError(f"{path.name} lacks happy and boundary cases")
        serialized = _canonical(pack).lower()
        if "northwind" in serialized or "/users/" in serialized:
            raise PortfolioError(f"{path.name} contains client or machine state")

    def _validate_capability(
        self,
        pack: dict[str, Any],
        capability: dict[str, Any],
        path: Path,
    ) -> None:
        required = {
            "capability_id",
            "description",
            "allowed_modes",
            "allowed_actions",
            "required_evidence",
            "outputs",
        }
        if set(capability) != required or not self._IDENTIFIER.fullmatch(
            capability["capability_id"]
        ):
            raise PortfolioError(f"{path.name} has an invalid capability")
        _text("description", capability["description"])
        modes = set(capability["allowed_modes"])
        if not modes or not modes <= set(self.policy["allowed_modes"]):
            raise PortfolioError(f"{path.name} has an executing mode")
        forbidden = set(self.policy["global_forbidden_actions"]) | set(
            pack["additional_forbidden_actions"]
        )
        if not capability["allowed_actions"] or forbidden & set(
            capability["allowed_actions"]
        ):
            raise PortfolioError(f"{path.name} grants a forbidden action")
        if not capability["required_evidence"] or not capability["outputs"]:
            raise PortfolioError(f"{path.name} capability lacks evidence or output")

    def decide(
        self,
        *,
        pack_id: str,
        capability_id: str,
        requested_mode: str,
        action_type: str,
    ) -> PackDecision:
        pack = self.packs.get(pack_id)
        if pack is None:
            return PackDecision.HELD
        capability = next(
            (
                item
                for item in pack["capabilities"]
                if item["capability_id"] == capability_id
            ),
            None,
        )
        if capability is None:
            return PackDecision.HELD
        forbidden = set(self.policy["global_forbidden_actions"]) | set(
            pack["additional_forbidden_actions"]
        )
        if (
            requested_mode not in capability["allowed_modes"]
            or action_type not in capability["allowed_actions"]
            or action_type in forbidden
        ):
            return PackDecision.HELD
        return PackDecision.ACCEPTED

    def evaluate(
        self,
        pack_id: str,
        *,
        store: Any | None = None,
        now: datetime | None = None,
    ) -> CapabilityAcceptance:
        pack = self.packs[pack_id]
        passed_count = 0
        for case in pack["evaluation_cases"]:
            decision = self.decide(
                pack_id=pack_id,
                capability_id=case["capability_id"],
                requested_mode=case["requested_mode"],
                action_type=case["action_type"],
            )
            passed_count += decision.value == case["expected"]
        pack_hash = _digest(pack)
        result = CapabilityAcceptance(
            pack_id=pack_id,
            pack_version=pack["version"],
            pack_hash=pack_hash,
            evaluator_version=self.EVALUATOR_VERSION,
            case_count=len(pack["evaluation_cases"]),
            passed_count=passed_count,
            passed=passed_count == len(pack["evaluation_cases"]),
        )
        if store is not None:
            with store._immediate_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO capability_pack_acceptances(
                        acceptance_id, pack_id, pack_version, pack_hash,
                        evaluator_version, case_count, passed_count, passed,
                        accepted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(pack_id, pack_version, pack_hash, evaluator_version)
                    DO NOTHING
                    """,
                    (
                        f"pack-acceptance-{uuid4()}", result.pack_id,
                        result.pack_version, result.pack_hash,
                        result.evaluator_version, result.case_count,
                        result.passed_count, int(result.passed),
                        _aware(now or datetime.now(timezone.utc)).isoformat(),
                    ),
                )
        return result

    def evaluate_all(
        self,
        *,
        store: Any | None = None,
        now: datetime | None = None,
    ) -> tuple[CapabilityAcceptance, ...]:
        return tuple(
            self.evaluate(pack_id, store=store, now=now)
            for pack_id in sorted(self.packs)
        )


class AggregatePerformanceService:
    """Persist privacy-safe aggregate evidence without inventing user events."""

    EVIDENCE_CLASS = "directional_aggregate"

    def __init__(self, store: Any):
        self.store = store

    def import_snapshot(
        self,
        *,
        tenant_id: str,
        business_id: str,
        producer_id: str,
        snapshot: AggregateSnapshot,
        now: datetime | None = None,
    ) -> AggregateResult:
        actor = self.store.get_actor(producer_id)
        if (
            actor is None
            or not actor.can_access(tenant_id=tenant_id, business_id=business_id)
            or not actor.roles
            & {"commerce", "marketing", "research", "operations"}
        ):
            raise PortfolioError("aggregate producer is outside business scope")
        evidence = self.store.get_evidence(snapshot.evidence_refs)
        if len(evidence) != len(snapshot.evidence_refs) or any(
            item["tenant_id"] != tenant_id
            or item["business_id"] != business_id
            for item in evidence
        ):
            raise PortfolioError("aggregate evidence is missing or crosses scope")
        imported_at = _aware(now or datetime.now(timezone.utc))
        if _aware(snapshot.window_end) > imported_at:
            raise PortfolioError("aggregate window must be complete before import")
        if any(
            _aware(datetime.fromisoformat(item["observed_at"]))
            < _aware(snapshot.window_end)
            or _aware(datetime.fromisoformat(item["observed_at"])) > imported_at
            or item["confidence"] < 0.70
            for item in evidence
        ):
            raise PortfolioError("aggregate evidence is stale, future, or too weak")
        expected_facts = {
            "commission_minor": snapshot.commission_minor,
            "content_clicks": snapshot.content_clicks,
            "conversions": snapshot.conversions,
            "engagements": snapshot.engagements,
            "gross_revenue_minor": snapshot.gross_revenue_minor,
            "impressions": snapshot.impressions,
            "outbound_clicks": snapshot.outbound_clicks,
        }
        observed_facts: dict[str, int] = {}
        for item in evidence:
            for key in expected_facts:
                if key not in item["facts"]:
                    continue
                value = item["facts"][key]
                if key in observed_facts and observed_facts[key] != value:
                    raise PortfolioError("aggregate evidence facts conflict")
                observed_facts[key] = value
        if observed_facts != expected_facts:
            raise PortfolioError("aggregate counts do not match normalized evidence")
        metrics = snapshot.metrics()
        payload = {
            "channel": snapshot.channel,
            "evidence_class": self.EVIDENCE_CLASS,
            "evidence_refs": list(snapshot.evidence_refs),
            "metrics": metrics,
            "minimum_outbound_clicks": snapshot.minimum_outbound_clicks,
            "offer_key": snapshot.offer_key,
            "source_ref": snapshot.source_ref,
            "source_system": snapshot.source_system,
            "window_end": _aware(snapshot.window_end).isoformat(),
            "window_start": _aware(snapshot.window_start).isoformat(),
        }
        snapshot_hash = _digest(payload)
        snapshot_id = f"aggregate-{uuid4()}"
        with self.store._immediate_connection() as connection:
            connection.execute(
                """
                INSERT INTO aggregate_performance_snapshots(
                    snapshot_id, tenant_id, business_id, producer_id, channel,
                    offer_key, source_system, source_ref, window_start,
                    window_end, impressions, engagements, content_clicks,
                    outbound_clicks, conversions, gross_revenue_minor,
                    commission_minor, minimum_outbound_clicks,
                    evidence_refs_json, evidence_class, snapshot_hash,
                    imported_at, limitation
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    snapshot_id, tenant_id, business_id, producer_id,
                    snapshot.channel, snapshot.offer_key, snapshot.source_system,
                    snapshot.source_ref, _aware(snapshot.window_start).isoformat(),
                    _aware(snapshot.window_end).isoformat(), snapshot.impressions,
                    snapshot.engagements, snapshot.content_clicks,
                    snapshot.outbound_clicks, snapshot.conversions,
                    snapshot.gross_revenue_minor, snapshot.commission_minor,
                    snapshot.minimum_outbound_clicks,
                    _canonical(snapshot.evidence_refs), self.EVIDENCE_CLASS,
                    snapshot_hash, imported_at.isoformat(),
                    "Aggregate evidence does not identify people or prove incrementality.",
                ),
            )
        return AggregateResult(snapshot_id, metrics, snapshot_hash)

    def verify(
        self,
        *,
        snapshot_id: str,
        verifier_id: str,
        now: datetime | None = None,
    ) -> AggregateVerificationDecision:
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT * FROM aggregate_performance_snapshots WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            raise PortfolioError("aggregate snapshot is missing")
        payload = self._payload(row)
        recomputed_hash = _digest(payload)
        if recomputed_hash != row["snapshot_hash"]:
            decision = AggregateVerificationDecision.REJECTED
            rationale = "Aggregate fields differ from their immutable hash."
        elif row["outbound_clicks"] < row["minimum_outbound_clicks"]:
            decision = AggregateVerificationDecision.INCONCLUSIVE
            rationale = "Aggregate report is consistent but below its sample floor."
        else:
            decision = AggregateVerificationDecision.VERIFIED
            rationale = (
                "Aggregate arithmetic and evidence bindings are consistent; "
                "the result remains directional and non-causal."
            )
        try:
            with self.store._immediate_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO aggregate_performance_verifications(
                        verification_id, snapshot_id, tenant_id, business_id,
                        verifier_id, decision, recomputed_hash, rationale,
                        verified_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        f"aggregate-verification-{uuid4()}", snapshot_id,
                        row["tenant_id"], row["business_id"], verifier_id,
                        decision.value, recomputed_hash, rationale,
                        _aware(now or datetime.now(timezone.utc)).isoformat(),
                    ),
                )
        except Exception as error:
            raise PortfolioError(
                "aggregate verification requires independent scoped QA"
            ) from error
        return decision

    @staticmethod
    def _payload(row: Any) -> dict[str, Any]:
        impressions = int(row["impressions"])
        engagements = int(row["engagements"])
        outbound = int(row["outbound_clicks"])
        conversions = int(row["conversions"])
        metrics = {
            "commission_minor": int(row["commission_minor"]),
            "content_clicks": int(row["content_clicks"]),
            "conversion_rate_bps": conversions * 10_000 // outbound if outbound else 0,
            "conversions": conversions,
            "engagement_rate_bps": engagements * 10_000 // impressions if impressions else 0,
            "engagements": engagements,
            "gross_revenue_minor": int(row["gross_revenue_minor"]),
            "impressions": impressions,
            "outbound_click_rate_bps": outbound * 10_000 // impressions if impressions else 0,
            "outbound_clicks": outbound,
            "sufficient_sample": outbound >= row["minimum_outbound_clicks"],
        }
        return {
            "channel": row["channel"],
            "evidence_class": row["evidence_class"],
            "evidence_refs": list(json.loads(row["evidence_refs_json"])),
            "metrics": metrics,
            "minimum_outbound_clicks": row["minimum_outbound_clicks"],
            "offer_key": row["offer_key"],
            "source_ref": row["source_ref"],
            "source_system": row["source_system"],
            "window_end": row["window_end"],
            "window_start": row["window_start"],
        }
