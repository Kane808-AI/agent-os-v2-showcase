"""Durable, local-only autonomous work loop for Agent OS v2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol, Sequence
from uuid import uuid4

from .contracts import (
    ActionRequest,
    ActorIdentity,
    AuthorityMode,
    Objective,
)
from .storage import ObjectiveRecord, SQLiteStore


class WorkStatus(StrEnum):
    READY = "ready"
    CLAIMED = "claimed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVAL_EXPIRED = "approval_expired"
    SIMULATED = "simulated"
    REJECTED = "rejected"
    FAILED = "failed"


class LeaseLostError(RuntimeError):
    """A worker attempted to resolve work after losing its lease."""


@dataclass(frozen=True, slots=True)
class WorkProposal:
    title: str
    rationale: str
    action_type: str
    assigned_actor_id: str
    priority_score: int
    platform: str | None = None
    account_id: str | None = None
    attributes: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    success: bool
    summary: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CycleReport:
    objectives_reviewed: int
    work_discovered: int
    work_deferred: int
    work_simulated: int
    approval_holds: int
    rejected: int
    retries_scheduled: int
    failed: int


class WorkDiscoveryPlanner(Protocol):
    def discover(
        self,
        objective: Objective,
        candidates: Sequence[ActorIdentity],
    ) -> WorkProposal | None:
        """Choose one bounded next action for a due objective."""


class WorkExecutor(Protocol):
    def execute(self, work_item: dict[str, object]) -> ExecutionResult:
        """Execute one claimed work item."""


class DeterministicWorkDiscovery:
    """Stable discovery policy used before introducing a model planner."""

    METRIC_ACTIONS = {
        "affiliate_sales": (
            "growth",
            "affiliate.funnel.review",
            "Review the affiliate funnel for the highest-leverage experiment",
        ),
        "qualified_leads": (
            "marketing",
            "marketing.pipeline.review",
            "Review the lead pipeline and propose the next acquisition experiment",
        ),
        "revenue": (
            "sales",
            "revenue.pipeline.review",
            "Review revenue pipeline constraints and propose the next intervention",
        ),
    }

    def discover(
        self,
        objective: Objective,
        candidates: Sequence[ActorIdentity],
    ) -> WorkProposal | None:
        required_role, action_type, title = self.METRIC_ACTIONS.get(
            objective.metric,
            (
                "operator",
                "objective.progress.review",
                "Review objective progress and propose the next bounded action",
            ),
        )
        eligible = sorted(
            (
                actor
                for actor in candidates
                if required_role in actor.roles
            ),
            key=lambda actor: actor.actor_id,
        )
        if not eligible:
            return None
        assignee = eligible[0]
        return WorkProposal(
            title=title,
            rationale=(
                f"The objective metric {objective.metric} is "
                f"{objective.current_value} against a target of {objective.target}."
            ),
            action_type=action_type,
            assigned_actor_id=assignee.actor_id,
            priority_score=max(0, 10_000 - objective.priority),
            attributes={
                "metric": objective.metric,
                "objective_statement": objective.statement,
            },
        )


class SimulatedWorkExecutor:
    """Executor that records intent without exposing any external capability."""

    def execute(self, work_item: dict[str, object]) -> ExecutionResult:
        return ExecutionResult(
            success=True,
            summary=(
                f"Simulated {work_item['action_type']} "
                f"as {work_item['assigned_actor_id']}."
            ),
        )


class AutonomousLoop:
    def __init__(
        self,
        store: SQLiteStore,
        *,
        discovery: WorkDiscoveryPlanner | None = None,
        executor: WorkExecutor | None = None,
        orchestrator_actor_id: str | None = None,
        worker_id: str | None = None,
        lease_seconds: int = 300,
        retry_base_seconds: int = 60,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if retry_base_seconds < 1:
            raise ValueError("retry_base_seconds must be positive")
        self.store = store
        self.discovery = discovery or DeterministicWorkDiscovery()
        self.executor = executor or SimulatedWorkExecutor()
        self.orchestrator_actor_id = orchestrator_actor_id
        self.worker_id = worker_id or f"worker-{uuid4().hex}"
        self.lease_seconds = lease_seconds
        self.retry_base_seconds = retry_base_seconds

    def _next_review(
        self,
        record: ObjectiveRecord,
        now: datetime,
    ) -> datetime:
        return now + timedelta(
            seconds=record.objective.review_interval_seconds
        )

    def discover_due_work(
        self,
        *,
        now: datetime | None = None,
        tenant_id: str | None = None,
        business_id: str | None = None,
    ) -> tuple[int, int, int, int, int]:
        current_time = now or datetime.now(timezone.utc)
        records = self.store.list_due_objectives(
            now=current_time,
            tenant_id=tenant_id,
            business_id=business_id,
        )
        discovered = deferred = approval_holds = rejected = 0
        for record in records:
            objective = record.objective
            next_review_at = self._next_review(record, current_time)
            if self.store.is_emergency_stop_active(
                tenant_id=objective.tenant_id,
                business_id=objective.business_id,
            ):
                self._defer(
                    record,
                    next_review_at,
                    current_time,
                    "emergency_stop.deferred",
                    "business emergency stop is active",
                )
                deferred += 1
                continue
            agents = self.store.list_agents_for_business(
                tenant_id=objective.tenant_id,
                business_id=objective.business_id,
            )
            if self.orchestrator_actor_id is not None:
                orchestrator = self.store.get_actor(
                    self.orchestrator_actor_id
                )
            else:
                orchestrators = sorted(
                    (
                        actor
                        for actor in agents
                        if "orchestrator" in actor.roles
                    ),
                    key=lambda actor: actor.actor_id,
                )
                orchestrator = orchestrators[0] if orchestrators else None
            if orchestrator is None or not orchestrator.can_access(
                tenant_id=objective.tenant_id,
                business_id=objective.business_id,
            ):
                self._defer(
                    record,
                    next_review_at,
                    current_time,
                    "work.discovery_deferred",
                    "orchestrator is missing or unauthorized",
                )
                deferred += 1
                continue

            candidates = [
                actor
                for actor in agents
                if actor.actor_id != orchestrator.actor_id
            ]
            proposal = self.discovery.discover(objective, candidates)
            if proposal is None:
                self._defer(
                    record,
                    next_review_at,
                    current_time,
                    "work.discovery_deferred",
                    "no eligible department agent is registered",
                )
                deferred += 1
                continue

            assignee = self.store.get_actor(proposal.assigned_actor_id)
            if assignee is None or not assignee.can_access(
                tenant_id=objective.tenant_id,
                business_id=objective.business_id,
            ):
                self._defer(
                    record,
                    next_review_at,
                    current_time,
                    "work.discovery_deferred",
                    "proposed assignee is missing or unauthorized",
                )
                deferred += 1
                continue

            action = ActionRequest(
                action_type=proposal.action_type,
                tenant_id=objective.tenant_id,
                business_id=objective.business_id,
                actor_id=proposal.assigned_actor_id,
                platform=proposal.platform,
                account_id=proposal.account_id,
                attributes=proposal.attributes or {},
            )
            authority_mode = self.store.decide_authority(
                action,
                now=current_time,
            )
            status = {
                AuthorityMode.AUTO: WorkStatus.READY,
                AuthorityMode.NOTIFY: WorkStatus.READY,
                AuthorityMode.APPROVE: WorkStatus.AWAITING_APPROVAL,
                AuthorityMode.FORBIDDEN: WorkStatus.REJECTED,
            }[authority_mode]
            work_key = (
                f"{objective.objective_id}:"
                f"{record.next_review_at.astimezone(timezone.utc).isoformat()}"
            )
            inserted = self.store.enqueue_work_item(
                work_item_id=f"work-{uuid4().hex}",
                work_key=work_key,
                objective_id=objective.objective_id,
                tenant_id=objective.tenant_id,
                business_id=objective.business_id,
                title=proposal.title,
                rationale=proposal.rationale,
                action_type=proposal.action_type,
                assigned_actor_id=proposal.assigned_actor_id,
                platform=proposal.platform,
                account_id=proposal.account_id,
                amount=None,
                currency=None,
                attributes=proposal.attributes or {},
                authority_mode=authority_mode.value,
                status=status.value,
                priority_score=proposal.priority_score,
                max_attempts=3,
                available_at=current_time,
                next_review_at=next_review_at,
                audit_id=f"audit-{uuid4().hex}",
            )
            if inserted:
                discovered += 1
                approval_holds += int(status is WorkStatus.AWAITING_APPROVAL)
                rejected += int(status is WorkStatus.REJECTED)
        return len(records), discovered, deferred, approval_holds, rejected

    def _defer(
        self,
        record: ObjectiveRecord,
        next_review_at: datetime,
        now: datetime,
        record_type: str,
        reason: str,
    ) -> None:
        objective = record.objective
        self.store.defer_objective(
            objective_id=objective.objective_id,
            tenant_id=objective.tenant_id,
            business_id=objective.business_id,
            next_review_at=next_review_at,
            record_type=record_type,
            details={
                "objective_id": objective.objective_id,
                "reason": reason,
            },
            audit_id=f"audit-{uuid4().hex}",
            now=now,
        )

    def execute_one(
        self,
        *,
        now: datetime | None = None,
        tenant_id: str | None = None,
        business_id: str | None = None,
    ) -> WorkStatus | None:
        current_time = now or datetime.now(timezone.utc)
        work = self.store.claim_next_work(
            worker_id=self.worker_id,
            now=current_time,
            lease_seconds=self.lease_seconds,
            tenant_id=tenant_id,
            business_id=business_id,
        )
        if work is None:
            return None

        assignee = self.store.get_actor(str(work["assigned_actor_id"]))
        action = ActionRequest(
            action_type=str(work["action_type"]),
            tenant_id=str(work["tenant_id"]),
            business_id=str(work["business_id"]),
            actor_id=str(work["assigned_actor_id"]),
            platform=(
                str(work["platform"]) if work["platform"] is not None else None
            ),
            account_id=(
                str(work["account_id"])
                if work["account_id"] is not None
                else None
            ),
            amount=work["amount"],
            currency=(
                str(work["currency"]) if work["currency"] is not None else None
            ),
            attributes=work["attributes"],
        )
        authority_mode = AuthorityMode.FORBIDDEN
        if assignee is not None and assignee.can_access(
            tenant_id=action.tenant_id,
            business_id=action.business_id,
        ):
            authority_mode = self.store.decide_authority(
                action,
                now=current_time,
            )

        valid_approval = (
            authority_mode is AuthorityMode.APPROVE
            and self.store.has_valid_work_approval(
                work_item_id=str(work["work_item_id"]),
                now=current_time,
            )
        )
        if authority_mode is AuthorityMode.FORBIDDEN or (
            authority_mode is AuthorityMode.APPROVE
            and not valid_approval
        ):
            status = (
                WorkStatus.AWAITING_APPROVAL
                if authority_mode is AuthorityMode.APPROVE
                else WorkStatus.REJECTED
            )
            resolved = self.store.resolve_claimed_work(
                work_item_id=str(work["work_item_id"]),
                worker_id=self.worker_id,
                status=status.value,
                authority_mode=authority_mode.value,
                record_type=(
                    "approval.required"
                    if status is WorkStatus.AWAITING_APPROVAL
                    else "policy.rejected"
                ),
                details={
                    "action_type": action.action_type,
                    "authority_mode": authority_mode.value,
                    "status": status.value,
                },
                audit_id=f"audit-{uuid4().hex}",
                now=current_time,
            )
            if not resolved:
                raise LeaseLostError(
                    f"worker lost lease for {work['work_item_id']}"
                )
            return status

        try:
            execution = self.executor.execute(work)
        except Exception as error:
            execution = ExecutionResult(
                success=False,
                summary="Executor raised an exception.",
                error=f"{type(error).__name__}: {error}",
            )
        if execution.success:
            resolved = self.store.resolve_claimed_work(
                work_item_id=str(work["work_item_id"]),
                worker_id=self.worker_id,
                status=WorkStatus.SIMULATED.value,
                authority_mode=authority_mode.value,
                record_type="work.simulated",
                details={
                    "action_type": action.action_type,
                    "authority_mode": authority_mode.value,
                    "status": WorkStatus.SIMULATED.value,
                    "summary": execution.summary,
                },
                audit_id=f"audit-{uuid4().hex}",
                now=current_time,
            )
            if not resolved:
                raise LeaseLostError(
                    f"worker lost lease for {work['work_item_id']}"
                )
            return WorkStatus.SIMULATED

        attempt_count = int(work["attempt_count"])
        retry_at = current_time + timedelta(
            seconds=self.retry_base_seconds * (2 ** max(0, attempt_count - 1))
        )
        next_status = self.store.fail_claimed_work(
            work_item_id=str(work["work_item_id"]),
            worker_id=self.worker_id,
            error=execution.error or execution.summary,
            retry_at=retry_at,
            audit_id=f"audit-{uuid4().hex}",
            now=current_time,
        )
        return WorkStatus(next_status)

    def run_cycle(
        self,
        *,
        now: datetime | None = None,
        tenant_id: str | None = None,
        business_id: str | None = None,
        max_work: int = 10,
    ) -> CycleReport:
        if max_work < 0:
            raise ValueError("max_work cannot be negative")
        current_time = now or datetime.now(timezone.utc)
        reviewed, discovered, deferred, holds, rejected = (
            self.discover_due_work(
                now=current_time,
                tenant_id=tenant_id,
                business_id=business_id,
            )
        )
        simulated = retries = failed = 0
        for _ in range(max_work):
            status = self.execute_one(
                now=current_time,
                tenant_id=tenant_id,
                business_id=business_id,
            )
            if status is None:
                break
            simulated += int(status is WorkStatus.SIMULATED)
            holds += int(status is WorkStatus.AWAITING_APPROVAL)
            rejected += int(status is WorkStatus.REJECTED)
            retries += int(status is WorkStatus.READY)
            failed += int(status is WorkStatus.FAILED)
        return CycleReport(
            objectives_reviewed=reviewed,
            work_discovered=discovered,
            work_deferred=deferred,
            work_simulated=simulated,
            approval_holds=holds,
            rejected=rejected,
            retries_scheduled=retries,
            failed=failed,
        )
