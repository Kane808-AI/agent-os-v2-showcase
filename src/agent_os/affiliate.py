"""Brand-neutral affiliate-marketing shadow experiment loop."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from .contracts import MemoryRecord, MemoryType, ObjectiveStatus, VerificationStatus
from .storage import SQLiteStore


class AffiliateShadowError(ValueError):
    """Raised when affiliate shadow evidence or sequencing is invalid."""


class VerificationDecision(StrEnum):
    VERIFIED = "verified"
    INCONCLUSIVE = "inconclusive"
    REJECTED = "rejected"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _utc(value: datetime | None) -> datetime:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise AffiliateShadowError("affiliate timestamps must be timezone-aware")
    return observed.astimezone(timezone.utc)


def _text(label: str, value: str) -> None:
    if not value or value != value.strip():
        raise AffiliateShadowError(f"{label} must be a non-empty trimmed value")


def _https(label: str, value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise AffiliateShadowError(f"{label} must be a credential-free HTTPS URL")


def _read_only_source(label: str, value: str) -> None:
    _text(label, value)
    if not value.endswith("-readonly"):
        raise AffiliateShadowError(f"{label} must identify a read-only source")


@dataclass(frozen=True, slots=True)
class OfferSnapshot:
    offer_key: str
    source_system: str
    source_ref: str
    merchant_name: str
    channel: str
    destination_url: str
    currency: str
    commission_rate_bps: int
    expected_order_value_minor: int
    audience_fit_score: int
    evidence_confidence_bps: int
    destination_healthy: bool
    terms_verified: bool
    disclosure_required: str
    approved_claims: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    observed_at: datetime

    def __post_init__(self) -> None:
        for label in (
            "offer_key", "source_ref", "merchant_name",
            "channel", "disclosure_required",
        ):
            _text(label, getattr(self, label))
        _read_only_source("source_system", self.source_system)
        _https("destination_url", self.destination_url)
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise AffiliateShadowError("currency must be an uppercase ISO code")
        if self.commission_rate_bps < 0 or self.expected_order_value_minor < 0:
            raise AffiliateShadowError("offer economics cannot be negative")
        if not 0 <= self.audience_fit_score <= 10_000:
            raise AffiliateShadowError("audience fit must be between 0 and 10000")
        if not 0 <= self.evidence_confidence_bps <= 10_000:
            raise AffiliateShadowError("evidence confidence must be between 0 and 10000")
        if len(set(self.approved_claims)) != len(self.approved_claims):
            raise AffiliateShadowError("approved claims must be unique")
        for claim in self.approved_claims:
            _text("approved_claim", claim)
        if not self.evidence_refs or len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise AffiliateShadowError("offer requires unique evidence references")
        for evidence_ref in self.evidence_refs:
            _text("evidence_ref", evidence_ref)
        _utc(self.observed_at)

    def payload(self) -> dict[str, Any]:
        return {
            "approved_claims": list(self.approved_claims),
            "audience_fit_score": self.audience_fit_score,
            "channel": self.channel,
            "commission_rate_bps": self.commission_rate_bps,
            "currency": self.currency,
            "destination_healthy": self.destination_healthy,
            "destination_url": self.destination_url,
            "disclosure_required": self.disclosure_required,
            "evidence_confidence_bps": self.evidence_confidence_bps,
            "evidence_refs": list(self.evidence_refs),
            "expected_order_value_minor": self.expected_order_value_minor,
            "merchant_name": self.merchant_name,
            "observed_at": _utc(self.observed_at).isoformat(),
            "offer_key": self.offer_key,
            "source_ref": self.source_ref,
            "source_system": self.source_system,
            "terms_verified": self.terms_verified,
        }


@dataclass(frozen=True, slots=True)
class ContentDraft:
    channel: str
    headline: str
    body: str
    disclosure: str
    call_to_action: str
    destination_url: str
    claims: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for label in ("channel", "headline", "body", "disclosure", "call_to_action"):
            _text(label, getattr(self, label))
        _https("destination_url", self.destination_url)
        if len(set(self.claims)) != len(self.claims):
            raise AffiliateShadowError("content claims must be unique")
        for claim in self.claims:
            _text("claim", claim)

    def payload(self) -> dict[str, Any]:
        return {
            "body": self.body,
            "call_to_action": self.call_to_action,
            "channel": self.channel,
            "claims": list(self.claims),
            "destination_url": self.destination_url,
            "disclosure": self.disclosure,
            "headline": self.headline,
        }


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    kind: str
    subject_key: str
    source_system: str
    source_ref: str
    occurred_at: datetime
    payload_hash: str
    click_observation_id: str | None = None
    gross_revenue_minor: int = 0
    commission_minor: int = 0

    def __post_init__(self) -> None:
        for label in ("observation_id", "kind", "subject_key", "source_ref"):
            _text(label, getattr(self, label))
        _read_only_source("source_system", self.source_system)
        if self.kind not in {"impression", "click", "conversion"}:
            raise AffiliateShadowError("observation kind is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", self.payload_hash):
            raise AffiliateShadowError("observation requires a SHA-256 payload hash")
        if self.gross_revenue_minor < 0 or not 0 <= self.commission_minor <= self.gross_revenue_minor:
            raise AffiliateShadowError("observation economics are invalid")
        if self.kind == "conversion" and self.click_observation_id is None:
            raise AffiliateShadowError("conversion requires its attributed click")
        if self.kind != "conversion" and (
            self.click_observation_id is not None
            or self.gross_revenue_minor
            or self.commission_minor
        ):
            raise AffiliateShadowError("non-conversion observations cannot carry revenue")
        _utc(self.occurred_at)


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    recommendation_id: str
    status: str
    selected_snapshot_id: str | None
    candidate_order: tuple[str, ...]
    rejection_reasons: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class MeasurementResult:
    measurement_id: str
    impression_count: int
    click_count: int
    conversion_count: int
    conversion_rate_bps: int
    gross_revenue_minor: int
    commission_minor: int
    sufficient_sample: bool
    measurement_hash: str


class AffiliateShadowLoop:
    """Durable historical-replay loop with no external write capability."""

    EVALUATOR_VERSION = "affiliate-offer-v1"
    MAXIMUM_OFFER_AGE_DAYS = 30
    MINIMUM_EVIDENCE_CONFIDENCE_BPS = 7000
    MINIMUM_AUDIENCE_FIT_SCORE = 6000

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def start_run(
        self,
        *,
        objective_id: str,
        producer_id: str,
        now: datetime | None = None,
    ) -> str:
        objective = self.store.get_objective(objective_id)
        if (
            objective is None
            or objective.objective.metric != "affiliate_sales"
            or objective.objective.status is not ObjectiveStatus.ACTIVE
        ):
            raise AffiliateShadowError("run requires an active affiliate_sales objective")
        actor = self.store.get_actor(producer_id)
        if actor is None or not actor.can_access(
            tenant_id=objective.objective.tenant_id,
            business_id=objective.objective.business_id,
        ) or not actor.roles & {"commerce", "marketing", "growth"}:
            raise AffiliateShadowError("producer is outside the affiliate business scope")
        run_id = f"affiliate-run-{uuid4()}"
        with self.store._immediate_connection() as connection:
            connection.execute(
                "INSERT INTO affiliate_shadow_runs VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id, objective_id, objective.objective.tenant_id,
                    objective.objective.business_id, producer_id, _utc(now).isoformat(),
                ),
            )
        return run_id

    def record_offer(
        self,
        *,
        run_id: str,
        snapshot: OfferSnapshot,
        now: datetime | None = None,
    ) -> str:
        run = self._run(run_id)
        current = _utc(now)
        with self.store._connection() as connection:
            if connection.execute(
                "SELECT 1 FROM affiliate_recommendations WHERE run_id=?", (run_id,)
            ).fetchone() is not None:
                raise AffiliateShadowError("offer research is frozen after recommendation")
        if _utc(snapshot.observed_at) > current:
            raise AffiliateShadowError("offer evidence cannot be observed in the future")
        if snapshot.currency != self.store.get_business(run["business_id"]).base_currency:
            raise AffiliateShadowError("offer currency must match the business base currency")
        evidence = self.store.get_evidence(snapshot.evidence_refs)
        if len(evidence) != len(snapshot.evidence_refs) or any(
            item["tenant_id"] != run["tenant_id"] or item["business_id"] != run["business_id"]
            for item in evidence
        ):
            raise AffiliateShadowError("offer evidence is missing or crosses business scope")
        snapshot_id = f"offer-{uuid4()}"
        payload = snapshot.payload()
        with self.store._immediate_connection() as connection:
            connection.execute(
                """
                INSERT INTO affiliate_offer_snapshots VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    snapshot_id, run_id, run["tenant_id"], run["business_id"],
                    snapshot.offer_key, snapshot.source_system, snapshot.source_ref,
                    snapshot.merchant_name, snapshot.channel, snapshot.destination_url,
                    snapshot.currency, snapshot.commission_rate_bps,
                    snapshot.expected_order_value_minor, snapshot.audience_fit_score,
                    snapshot.evidence_confidence_bps, int(snapshot.destination_healthy),
                    int(snapshot.terms_verified), snapshot.disclosure_required,
                    _canonical(snapshot.approved_claims), _canonical(snapshot.evidence_refs),
                    _utc(snapshot.observed_at).isoformat(), _digest(payload), current.isoformat(),
                ),
            )
        return snapshot_id

    def recommend(self, *, run_id: str, now: datetime | None = None) -> RecommendationResult:
        run = self._run(run_id)
        current = _utc(now)
        with self.store._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM affiliate_offer_snapshots WHERE run_id=? ORDER BY offer_key, snapshot_id",
                (run_id,),
            ).fetchall()
        if not rows:
            raise AffiliateShadowError("recommendation requires researched offers")
        eligible: list[tuple[tuple[int, str], Any]] = []
        reasons: dict[str, tuple[str, ...]] = {}
        for row in rows:
            rejected = []
            if current - datetime.fromisoformat(row["observed_at"]) > timedelta(
                days=self.MAXIMUM_OFFER_AGE_DAYS
            ):
                rejected.append("stale_offer_evidence")
            if not row["destination_healthy"]:
                rejected.append("destination_unhealthy")
            if not row["terms_verified"]:
                rejected.append("terms_unverified")
            if (
                row["evidence_confidence_bps"]
                < self.MINIMUM_EVIDENCE_CONFIDENCE_BPS
            ):
                rejected.append("low_evidence_confidence")
            if row["audience_fit_score"] < self.MINIMUM_AUDIENCE_FIT_SCORE:
                rejected.append("low_audience_fit")
            if row["commission_rate_bps"] <= 0:
                rejected.append("no_commission_economics")
            if rejected:
                reasons[row["snapshot_id"]] = tuple(sorted(rejected))
            else:
                score = (
                    row["audience_fit_score"] + row["evidence_confidence_bps"]
                    + min(row["commission_rate_bps"], 5000)
                    + min(row["expected_order_value_minor"] // 100, 2000)
                )
                eligible.append(((-score, row["offer_key"]), row))
        eligible.sort(key=lambda item: item[0])
        order = tuple(item[1]["snapshot_id"] for item in eligible)
        selected = eligible[0][1] if eligible else None
        recommendation_id = f"affiliate-rec-{uuid4()}"
        with self.store._immediate_connection() as connection:
            connection.execute(
                """
                INSERT INTO affiliate_recommendations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recommendation_id, run_id, run["tenant_id"], run["business_id"],
                    selected["snapshot_id"] if selected else None,
                    "selected" if selected else "held", _canonical(order),
                    _canonical(reasons), self.EVALUATOR_VERSION, current.isoformat(),
                ),
            )
        return RecommendationResult(
            recommendation_id, "selected" if selected else "held",
            selected["snapshot_id"] if selected else None, order, reasons,
        )

    def propose_content(
        self,
        *,
        recommendation_id: str,
        shadow_attempt_id: str,
        draft: ContentDraft,
        now: datetime | None = None,
    ) -> str:
        with self.store._connection() as connection:
            row = connection.execute(
                """
                SELECT r.*, o.destination_url, o.channel, o.disclosure_required,
                       o.approved_claims_json
                FROM affiliate_recommendations r
                JOIN affiliate_offer_snapshots o ON o.snapshot_id=r.selected_snapshot_id
                WHERE r.recommendation_id=? AND r.status='selected'
                """,
                (recommendation_id,),
            ).fetchone()
        if row is None:
            raise AffiliateShadowError("content requires a selected offer recommendation")
        if draft.destination_url != row["destination_url"] or draft.channel != row["channel"]:
            raise AffiliateShadowError("content destination or channel drifts from selected offer")
        if draft.disclosure != row["disclosure_required"]:
            raise AffiliateShadowError("content must use the required affiliate disclosure")
        if not set(draft.claims) <= set(json.loads(row["approved_claims_json"])):
            raise AffiliateShadowError("content includes an unapproved claim")
        proposal_id = f"affiliate-content-{uuid4()}"
        content_hash = _digest(draft.payload())
        try:
            with self.store._immediate_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO affiliate_content_proposals VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?
                    )
                    """,
                    (
                        proposal_id, row["run_id"], recommendation_id, shadow_attempt_id,
                        row["tenant_id"], row["business_id"], draft.channel,
                        draft.headline, draft.body, draft.disclosure, draft.call_to_action,
                        draft.destination_url, _canonical(draft.claims), content_hash,
                        _utc(now).isoformat(),
                    ),
                )
        except Exception as error:
            raise AffiliateShadowError("content is not backed by validated Goal 11 output") from error
        return proposal_id

    def define_experiment(
        self,
        *,
        proposal_id: str,
        hypothesis: str,
        window_start: datetime,
        window_end: datetime,
        minimum_clicks: int,
        now: datetime | None = None,
    ) -> str:
        _text("hypothesis", hypothesis)
        start, end = _utc(window_start), _utc(window_end)
        if start >= end or minimum_clicks <= 0:
            raise AffiliateShadowError("experiment window and sample floor are invalid")
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT * FROM affiliate_content_proposals WHERE proposal_id=? AND status='proposed'",
                (proposal_id,),
            ).fetchone()
        if row is None:
            raise AffiliateShadowError("experiment requires a content proposal")
        experiment_id = f"affiliate-exp-{uuid4()}"
        tracking_key = f"shadow:{row['run_id']}:{uuid4().hex}"
        with self.store._immediate_connection() as connection:
            connection.execute(
                "INSERT INTO affiliate_experiments VALUES (?, ?, ?, ?, ?, ?, 'historical_replay', ?, ?, ?, ?, 'shadow', ?)",
                (
                    experiment_id, row["run_id"], proposal_id, row["tenant_id"],
                    row["business_id"], tracking_key, hypothesis, start.isoformat(),
                    end.isoformat(), minimum_clicks, _utc(now).isoformat(),
                ),
            )
        return experiment_id

    def import_observation(
        self,
        *,
        experiment_id: str,
        observation: Observation,
        now: datetime | None = None,
    ) -> None:
        with self.store._connection() as connection:
            experiment = connection.execute(
                "SELECT * FROM affiliate_experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
        if experiment is None:
            raise AffiliateShadowError("observation experiment is missing")
        occurred = _utc(observation.occurred_at)
        current = _utc(now)
        if occurred > current:
            raise AffiliateShadowError("observation cannot be imported before it occurred")
        if not datetime.fromisoformat(experiment["window_start"]) <= occurred < datetime.fromisoformat(experiment["window_end"]):
            raise AffiliateShadowError("observation is outside the replay window")
        with self.store._connection() as connection:
            if connection.execute(
                "SELECT 1 FROM affiliate_measurements WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone() is not None:
                raise AffiliateShadowError("replay observations are frozen after measurement")
        try:
            with self.store._immediate_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO affiliate_observations VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        observation.observation_id, experiment_id,
                        experiment["tenant_id"], experiment["business_id"],
                        observation.kind, observation.subject_key,
                        observation.click_observation_id,
                        observation.gross_revenue_minor, observation.commission_minor,
                        observation.source_system, observation.source_ref,
                        observation.payload_hash, occurred.isoformat(), current.isoformat(),
                    ),
                )
        except Exception as error:
            raise AffiliateShadowError("observation is duplicate or attribution is invalid") from error

    def measure(
        self,
        *,
        experiment_id: str,
        now: datetime | None = None,
    ) -> MeasurementResult:
        current = _utc(now)
        with self.store._connection() as connection:
            experiment = connection.execute(
                "SELECT * FROM affiliate_experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
            if experiment is None or current < datetime.fromisoformat(experiment["window_end"]):
                raise AffiliateShadowError("measurement requires a completed replay window")
            metrics = self._metrics(connection, experiment_id, experiment["minimum_clicks"])
        measurement_id = f"affiliate-measure-{uuid4()}"
        measurement_hash = _digest(metrics)
        with self.store._immediate_connection() as connection:
            connection.execute(
                "INSERT INTO affiliate_measurements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    measurement_id, experiment_id, experiment["tenant_id"],
                    experiment["business_id"], metrics["impression_count"],
                    metrics["click_count"], metrics["conversion_count"],
                    metrics["conversion_rate_bps"], metrics["gross_revenue_minor"],
                    metrics["commission_minor"], int(metrics["sufficient_sample"]),
                    measurement_hash, current.isoformat(),
                ),
            )
        return MeasurementResult(measurement_id, **metrics, measurement_hash=measurement_hash)

    def verify(
        self,
        *,
        measurement_id: str,
        verifier_id: str,
        now: datetime | None = None,
    ) -> VerificationDecision:
        with self.store._connection() as connection:
            row = connection.execute(
                """
                SELECT m.*, e.minimum_clicks FROM affiliate_measurements m
                JOIN affiliate_experiments e ON e.experiment_id=m.experiment_id
                WHERE m.measurement_id=?
                """,
                (measurement_id,),
            ).fetchone()
            if row is None:
                raise AffiliateShadowError("measurement is missing")
            metrics = self._metrics(connection, row["experiment_id"], row["minimum_clicks"])
        recomputed = _digest(metrics)
        if recomputed != row["measurement_hash"]:
            decision = VerificationDecision.REJECTED
            rationale = "Persisted measurement differs from replay evidence."
        elif not metrics["sufficient_sample"]:
            decision = VerificationDecision.INCONCLUSIVE
            rationale = "Replay is internally consistent but below the sample floor."
        else:
            decision = VerificationDecision.VERIFIED
            rationale = "Independent replay matches attributed click and conversion evidence."
        try:
            with self.store._immediate_connection() as connection:
                connection.execute(
                    "INSERT INTO affiliate_verifications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"affiliate-verify-{uuid4()}", measurement_id, row["tenant_id"],
                        row["business_id"], verifier_id, decision.value, recomputed,
                        rationale, _utc(now).isoformat(),
                    ),
                )
        except Exception as error:
            raise AffiliateShadowError(
                "verification requires independent scoped QA"
            ) from error
        return decision

    def learn(self, *, measurement_id: str, now: datetime | None = None) -> str:
        current = _utc(now)
        with self.store._connection() as connection:
            row = connection.execute(
                """
                SELECT v.verification_id, v.decision AS verification_decision,
                       m.*, e.run_id, r.producer_id, o.offer_key, o.evidence_refs_json
                FROM affiliate_verifications v
                JOIN affiliate_measurements m ON m.measurement_id=v.measurement_id
                JOIN affiliate_experiments e ON e.experiment_id=m.experiment_id
                JOIN affiliate_shadow_runs r ON r.run_id=e.run_id
                JOIN affiliate_recommendations rec ON rec.run_id=r.run_id
                JOIN affiliate_offer_snapshots o ON o.snapshot_id=rec.selected_snapshot_id
                WHERE m.measurement_id=?
                """,
                (measurement_id,),
            ).fetchone()
        if row is None or row["verification_decision"] != "verified":
            raise AffiliateShadowError("learning requires independently verified measurement")
        decision = "recommend" if row["conversion_count"] > 0 else "revise"
        statement = (
            f"Historical affiliate shadow replay for {row['offer_key']} observed "
            f"{row['conversion_count']} attributed conversions from {row['click_count']} clicks "
            f"({row['conversion_rate_bps']} bps). This is candidate correlational learning, "
            "not proof of incrementality or authority to publish or spend."
        )
        memory_id = f"affiliate-memory-{uuid4()}"
        memory = MemoryRecord(
            memory_id=memory_id, tenant_id=row["tenant_id"], business_id=row["business_id"],
            memory_type=MemoryType.EPISODIC, statement=statement,
            source_type="affiliate_shadow_replay", source_ref=measurement_id,
            confidence=Decimal("0.75"), verification_status=VerificationStatus.CANDIDATE,
            created_at=current, observed_at=current,
            evidence_refs=tuple(json.loads(row["evidence_refs_json"])),
        )
        with self.store._immediate_connection() as connection:
            connection.execute(
                """
                INSERT INTO memory_records(
                    memory_id, tenant_id, business_id, memory_type, statement,
                    source_type, source_ref, confidence, verification_status,
                    evidence_refs_json, created_at, observed_at, expires_at,
                    supersedes_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.memory_id, memory.tenant_id, memory.business_id,
                    memory.memory_type.value, memory.statement, memory.source_type,
                    memory.source_ref, str(memory.confidence),
                    memory.verification_status.value, _canonical(memory.evidence_refs),
                    current.isoformat(), current.isoformat(), None, None,
                ),
            )
            connection.execute(
                "INSERT INTO affiliate_learnings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"affiliate-learning-{uuid4()}", row["verification_id"], memory_id,
                    row["tenant_id"], row["business_id"], decision,
                    hashlib.sha256(statement.encode()).hexdigest(), current.isoformat(),
                ),
            )
        return memory_id

    def _run(self, run_id: str):
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT * FROM affiliate_shadow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise AffiliateShadowError("affiliate shadow run is missing")
        return row

    @staticmethod
    def _metrics(connection, experiment_id: str, minimum_clicks: int) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT
              SUM(CASE WHEN kind='impression' THEN 1 ELSE 0 END) impressions,
              SUM(CASE WHEN kind='click' THEN 1 ELSE 0 END) clicks,
              SUM(CASE WHEN kind='conversion' THEN 1 ELSE 0 END) conversions,
              SUM(CASE WHEN kind='conversion' THEN gross_revenue_minor ELSE 0 END) gross,
              SUM(CASE WHEN kind='conversion' THEN commission_minor ELSE 0 END) commission
            FROM affiliate_observations WHERE experiment_id=?
            """,
            (experiment_id,),
        ).fetchone()
        impressions, clicks, conversions = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
        return {
            "impression_count": impressions,
            "click_count": clicks,
            "conversion_count": conversions,
            "conversion_rate_bps": (conversions * 10_000 // clicks) if clicks else 0,
            "gross_revenue_minor": int(row[3] or 0),
            "commission_minor": int(row[4] or 0),
            "sufficient_sample": clicks >= minimum_clicks,
        }
