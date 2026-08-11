"""Bounded planning, evaluation, and candidate learning for Agent OS v2."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

from .autonomy import WorkStatus
from .contracts import (
    ActionRequest,
    MemoryRecord,
    MemoryType,
    Objective,
    VerificationStatus,
)
from .storage import SQLiteStore


class EvaluationDecision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class PlanStep:
    title: str
    rationale: str
    action_type: str
    assigned_actor_id: str
    expected_output: str


@dataclass(frozen=True, slots=True)
class StructuredPlan:
    plan_id: str
    tenant_id: str
    business_id: str
    objective_id: str
    capability_id: str
    planner_id: str
    hypothesis: str
    expected_metric: str
    evidence_refs: tuple[str, ...]
    steps: tuple[PlanStep, ...]

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("plan_id")
        return payload

    def digest(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PlanEvaluation:
    decision: EvaluationDecision
    score: int
    reasons: tuple[str, ...]
    authority_modes: tuple[str, ...]

    def digest(self) -> str:
        encoded = json.dumps(
            {
                "authority_modes": self.authority_modes,
                "decision": self.decision.value,
                "reasons": self.reasons,
                "score": self.score,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PlanningResult:
    plan_id: str
    decision: EvaluationDecision
    score: int
    reasons: tuple[str, ...]
    work_items_created: int
    candidate_memory_id: str | None


@dataclass(frozen=True, slots=True)
class Playbook:
    capability_id: str
    planner_id: str
    hypothesis_template: str
    expected_metric: str
    steps: tuple[Mapping[str, str], ...]

    @classmethod
    def from_path(cls, path: str | Path) -> "Playbook":
        raw = json.loads(Path(path).read_text())
        return cls(
            capability_id=raw["capability"]["capability_id"],
            planner_id=raw["planner_id"],
            hypothesis_template=raw["hypothesis_template"],
            expected_metric=raw["expected_metric"],
            steps=tuple(raw["steps"]),
        )


class StructuredPlanner(Protocol):
    def plan(
        self,
        *,
        objective: Objective,
        actor_id: str,
        evidence: Sequence[dict[str, Any]],
        playbook: Playbook,
    ) -> StructuredPlan:
        """Return a plan matching the structured contract."""


class PlaybookPlanner:
    """Deterministic provider used to evaluate the intelligence boundary."""

    def plan(
        self,
        *,
        objective: Objective,
        actor_id: str,
        evidence: Sequence[dict[str, Any]],
        playbook: Playbook,
    ) -> StructuredPlan:
        facts = {
            key: value
            for record in evidence
            for key, value in record["facts"].items()
        }
        try:
            hypothesis = playbook.hypothesis_template.format(
                objective=objective.statement,
                metric=objective.metric,
                current=objective.current_value,
                target=objective.target,
                **facts,
            )
        except KeyError as error:
            hypothesis = (
                f"Planning input is missing required fact {error.args[0]}."
            )
        steps = tuple(
            PlanStep(
                title=raw["title"],
                rationale=raw["rationale"],
                action_type=raw["action_type"],
                assigned_actor_id=actor_id,
                expected_output=raw["expected_output"],
            )
            for raw in playbook.steps
        )
        return StructuredPlan(
            plan_id=f"plan-{uuid4().hex}",
            tenant_id=objective.tenant_id,
            business_id=objective.business_id,
            objective_id=objective.objective_id,
            capability_id=playbook.capability_id,
            planner_id=playbook.planner_id,
            hypothesis=hypothesis,
            expected_metric=playbook.expected_metric,
            evidence_refs=tuple(
                sorted(record["evidence_id"] for record in evidence)
            ),
            steps=steps,
        )


class BoundedPlanEvaluator:
    VERSION = "bounded-plan-v1"

    def __init__(
        self,
        store: SQLiteStore,
        *,
        minimum_evidence_confidence: Decimal = Decimal("0.50"),
    ) -> None:
        self.store = store
        self.minimum_evidence_confidence = minimum_evidence_confidence

    def evaluate(
        self,
        plan: StructuredPlan,
        *,
        now: datetime,
    ) -> PlanEvaluation:
        reasons: list[str] = []
        modes: list[str] = []
        objective_record = self.store.get_objective(plan.objective_id)
        capability = self.store.get_capability(plan.capability_id)
        actor_id = (
            plan.steps[0].assigned_actor_id if plan.steps else ""
        )
        actor = self.store.get_actor(actor_id) if actor_id else None
        evidence = self.store.get_evidence(plan.evidence_refs)

        if (
            objective_record is None
            or objective_record.objective.tenant_id != plan.tenant_id
            or objective_record.objective.business_id != plan.business_id
        ):
            reasons.append("objective is missing or outside the plan boundary")
        if not plan.hypothesis.strip() or plan.hypothesis.startswith(
            "Planning input is missing"
        ):
            reasons.append("hypothesis is incomplete")
        if not plan.steps:
            reasons.append("plan contains no steps")
        if not plan.evidence_refs:
            reasons.append("plan contains no supporting evidence")
        if len(evidence) != len(plan.evidence_refs):
            reasons.append("one or more evidence references are missing")
        for record in evidence:
            if (
                record["tenant_id"] != plan.tenant_id
                or record["business_id"] != plan.business_id
            ):
                reasons.append("evidence crosses the plan identity boundary")
            if record["confidence"] < self.minimum_evidence_confidence:
                reasons.append("evidence confidence is below the required floor")

        if capability is None:
            reasons.append("capability is not registered")
            allowed_actions: set[str] = set()
        else:
            allowed_actions = set(capability["action_types"])
        if actor is None or not actor.can_access(
            tenant_id=plan.tenant_id,
            business_id=plan.business_id,
        ):
            reasons.append("assigned agent is missing or unauthorized")
        elif not self.store.actor_has_capability(
            tenant_id=plan.tenant_id,
            business_id=plan.business_id,
            actor_id=actor.actor_id,
            capability_id=plan.capability_id,
        ):
            reasons.append("assigned agent does not hold the capability")

        for step in plan.steps:
            if step.assigned_actor_id != actor_id:
                reasons.append("all steps must use the evaluated agent identity")
            if step.action_type not in allowed_actions:
                reasons.append(
                    f"action {step.action_type} is outside the capability"
                )
            request = ActionRequest(
                action_type=step.action_type,
                tenant_id=plan.tenant_id,
                business_id=plan.business_id,
                actor_id=step.assigned_actor_id,
                attributes={
                    "expected_output": step.expected_output,
                    "plan_id": plan.plan_id,
                },
            )
            mode = self.store.decide_authority(request, now=now)
            mode_value = mode.value
            modes.append(mode_value)
            if mode_value == "forbidden":
                reasons.append(
                    f"action {step.action_type} is forbidden by authority"
                )

        unique_reasons = tuple(dict.fromkeys(reasons))
        score = max(0, 100 - 20 * len(unique_reasons))
        return PlanEvaluation(
            decision=(
                EvaluationDecision.ACCEPTED
                if not unique_reasons
                else EvaluationDecision.REJECTED
            ),
            score=score,
            reasons=unique_reasons,
            authority_modes=tuple(modes),
        )


class IntelligenceRuntime:
    def __init__(
        self,
        store: SQLiteStore,
        *,
        planner: StructuredPlanner | None = None,
        evaluator: BoundedPlanEvaluator | None = None,
    ) -> None:
        self.store = store
        self.planner = planner or PlaybookPlanner()
        self.evaluator = evaluator or BoundedPlanEvaluator(store)

    def plan_objective(
        self,
        *,
        objective_id: str,
        actor_id: str,
        evidence_ids: tuple[str, ...],
        playbook: Playbook,
        now: datetime | None = None,
    ) -> PlanningResult:
        current_time = now or datetime.now(timezone.utc)
        record = self.store.get_objective(objective_id)
        if record is None:
            raise ValueError("objective is not registered")
        evidence = self.store.get_evidence(evidence_ids)
        plan = self.planner.plan(
            objective=record.objective,
            actor_id=actor_id,
            evidence=evidence,
            playbook=playbook,
        )
        evaluation = self.evaluator.evaluate(plan, now=current_time)
        self.store.record_plan_and_evaluation(
            plan_id=plan.plan_id,
            tenant_id=plan.tenant_id,
            business_id=plan.business_id,
            objective_id=plan.objective_id,
            capability_id=plan.capability_id,
            planner_id=plan.planner_id,
            plan=plan.canonical_payload(),
            plan_hash=plan.digest(),
            status=evaluation.decision.value,
            evaluation_id=f"evaluation-{uuid4().hex}",
            evaluator_version=self.evaluator.VERSION,
            decision=evaluation.decision.value,
            score=evaluation.score,
            reasons=evaluation.reasons,
            authority_modes=evaluation.authority_modes,
            evaluation_hash=evaluation.digest(),
            created_at=current_time,
        )

        work_items_created = 0
        memory_id = None
        if evaluation.decision is EvaluationDecision.ACCEPTED:
            next_review = current_time + timedelta(
                seconds=record.objective.review_interval_seconds
            )
            work_items = []
            for index, (step, mode) in enumerate(
                zip(plan.steps, evaluation.authority_modes, strict=True)
            ):
                status = (
                    WorkStatus.AWAITING_APPROVAL
                    if mode == "approve"
                    else WorkStatus.READY
                )
                work_items.append(
                    {
                        "work_item_id": f"work-{uuid4().hex}",
                        "title": step.title,
                        "rationale": step.rationale,
                        "action_type": step.action_type,
                        "assigned_actor_id": step.assigned_actor_id,
                        "attributes": {
                            "expected_output": step.expected_output,
                            "plan_id": plan.plan_id,
                        },
                        "authority_mode": mode,
                        "status": status.value,
                        "priority_score": max(
                            0, 10_000 - record.objective.priority - index
                        ),
                        "max_attempts": 3,
                        "audit_id": f"audit-{uuid4().hex}",
                    }
                )

            memory_id = f"memory-{uuid4().hex}"
            memory = MemoryRecord(
                memory_id=memory_id,
                tenant_id=plan.tenant_id,
                business_id=plan.business_id,
                memory_type=MemoryType.SEMANTIC,
                statement=plan.hypothesis,
                source_type="structured_plan",
                source_ref=plan.plan_id,
                confidence=Decimal("0.60"),
                verification_status=VerificationStatus.CANDIDATE,
                created_at=current_time,
                observed_at=current_time,
                evidence_refs=plan.evidence_refs,
            )
            work_items_created = self.store.materialize_plan(
                plan_id=plan.plan_id,
                plan_hash=plan.digest(),
                objective_id=plan.objective_id,
                tenant_id=plan.tenant_id,
                business_id=plan.business_id,
                work_items=tuple(work_items),
                next_review_at=next_review,
                memory=memory,
                now=current_time,
            )

        return PlanningResult(
            plan_id=plan.plan_id,
            decision=evaluation.decision,
            score=evaluation.score,
            reasons=evaluation.reasons,
            work_items_created=work_items_created,
            candidate_memory_id=memory_id,
        )
