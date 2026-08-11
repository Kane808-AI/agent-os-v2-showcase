"""Local Agent OS runtime slice.

This module proves control flow only. It contains no external tool executor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from .contracts import ActionRequest, AuthorityMode, Event, TenantStatus
from .storage import EventProcessingInProgress, SQLiteStore


class RunStatus(StrEnum):
    SIMULATED = "simulated"
    AWAITING_APPROVAL = "awaiting_approval"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class AtlasPlan:
    action: ActionRequest
    summary: str


@dataclass(frozen=True, slots=True)
class ProcessResult:
    run_id: str
    event_id: str
    status: RunStatus
    authority_mode: AuthorityMode
    summary: str
    duplicate: bool = False


class AtlasPlanner(Protocol):
    def plan(self, event: Event) -> AtlasPlan:
        """Translate a normalized event into one proposed action."""


class DeterministicAtlasPlanner:
    """Safe planner used until a model adapter is evaluated."""

    ACTIONS_BY_EVENT = {
        "objective.review.requested": "portfolio.review",
        "metric.threshold_breached": "experiment.plan",
        "work.discovery.requested": "work.discover",
    }

    def plan(self, event: Event) -> AtlasPlan:
        action_type = self.ACTIONS_BY_EVENT.get(event.kind, "event.triage")
        if event.kind == "command.received":
            requested_action = event.payload.get("requested_action")
            if isinstance(requested_action, str) and requested_action.strip():
                action_type = requested_action
        return AtlasPlan(
            action=ActionRequest(
                action_type=action_type,
                tenant_id=event.tenant_id,
                business_id=event.business_id,
                actor_id="atlas",
                platform=(
                    str(event.payload["platform"])
                    if "platform" in event.payload
                    else None
                ),
                account_id=(
                    str(event.payload["account_id"])
                    if "account_id" in event.payload
                    else None
                ),
                attributes={
                    "source_event_id": event.event_id,
                    "source_event_kind": event.kind,
                },
            ),
            summary=f"Atlas proposed {action_type} for {event.kind}.",
        )


class AgentRuntime:
    def __init__(
        self,
        store: SQLiteStore,
        planner: AtlasPlanner | None = None,
        *,
        worker_id: str | None = None,
        processing_lease_seconds: int = 300,
    ) -> None:
        if processing_lease_seconds < 1:
            raise ValueError("processing lease must be positive")
        self.store = store
        self.planner = planner or DeterministicAtlasPlanner()
        self.worker_id = worker_id or f"event-worker-{uuid4().hex}"
        self.processing_lease_seconds = processing_lease_seconds

    def process(self, event: Event) -> ProcessResult:
        claim = self.store.claim_event_processing(
            event,
            worker_id=self.worker_id,
            now=datetime.now(timezone.utc),
            lease_seconds=self.processing_lease_seconds,
        )
        if not claim.claimed:
            existing = self.store.get_run_for_event(claim.event_id)
            if existing is not None:
                return ProcessResult(
                    run_id=existing["run_id"],
                    event_id=claim.event_id,
                    status=RunStatus(existing["status"]),
                    authority_mode=AuthorityMode(
                        existing["authority_mode"] or AuthorityMode.FORBIDDEN
                    ),
                    summary=existing["summary"],
                    duplicate=True,
                )
            raise EventProcessingInProgress(
                f"event {claim.event_id} is already processing"
            )
        if not claim.inserted:
            recovered_event = self.store.get_event(
                claim.event_id,
                tenant_id=event.tenant_id,
                business_id=event.business_id,
            )
            if recovered_event is None:
                raise RuntimeError("event receipt exists but the event cannot be loaded")
            event = recovered_event

        run_id = f"run-{uuid4().hex}"
        tenant = self.store.get_tenant(event.tenant_id)
        business = self.store.get_business(event.business_id)
        actor = self.store.get_actor(event.actor_id)

        rejection_reason = None
        if tenant is None or tenant.status is not TenantStatus.ACTIVE:
            rejection_reason = "tenant is missing or inactive"
        elif business is None or business.tenant_id != event.tenant_id:
            rejection_reason = "business is missing or outside the tenant"
        elif actor is None or not actor.can_access(
            tenant_id=event.tenant_id,
            business_id=event.business_id,
        ):
            rejection_reason = "actor is missing, disabled, or unauthorized"

        if rejection_reason is not None:
            return self._finish(
                event=event,
                run_id=run_id,
                action_type=None,
                authority_mode=AuthorityMode.FORBIDDEN,
                status=RunStatus.REJECTED,
                summary=f"Event rejected: {rejection_reason}.",
                audit_type="identity.rejected",
            )

        if self.store.is_emergency_stop_active(
            tenant_id=event.tenant_id,
            business_id=event.business_id,
        ):
            return self._finish(
                event=event,
                run_id=run_id,
                action_type=None,
                authority_mode=AuthorityMode.FORBIDDEN,
                status=RunStatus.REJECTED,
                summary="Event rejected: business emergency stop is active.",
                audit_type="emergency_stop.blocked",
            )

        plan = self.planner.plan(event)
        action_actor = self.store.get_actor(plan.action.actor_id)
        if action_actor is None or not action_actor.can_access(
            tenant_id=plan.action.tenant_id,
            business_id=plan.action.business_id,
        ):
            return self._finish(
                event=event,
                run_id=run_id,
                action_type=plan.action.action_type,
                authority_mode=AuthorityMode.FORBIDDEN,
                status=RunStatus.REJECTED,
                summary=f"{plan.summary} Action actor is missing or unauthorized.",
                audit_type="identity.rejected",
            )

        authority_mode = self.store.decide_authority(plan.action)

        if authority_mode is AuthorityMode.FORBIDDEN:
            return self._finish(
                event=event,
                run_id=run_id,
                action_type=plan.action.action_type,
                authority_mode=authority_mode,
                status=RunStatus.REJECTED,
                summary=f"{plan.summary} Policy rejected the action.",
                audit_type="policy.rejected",
            )

        if authority_mode is AuthorityMode.APPROVE:
            return self._finish(
                event=event,
                run_id=run_id,
                action_type=plan.action.action_type,
                authority_mode=authority_mode,
                status=RunStatus.AWAITING_APPROVAL,
                summary=f"{plan.summary} Waiting for approval.",
                audit_type="approval.required",
            )

        return self._finish(
            event=event,
            run_id=run_id,
            action_type=plan.action.action_type,
            authority_mode=authority_mode,
            status=RunStatus.SIMULATED,
            summary=f"{plan.summary} Execution simulated; no external action exists.",
            audit_type="execution.simulated",
        )

    def _finish(
        self,
        *,
        event: Event,
        run_id: str,
        action_type: str | None,
        authority_mode: AuthorityMode,
        status: RunStatus,
        summary: str,
        audit_type: str,
    ) -> ProcessResult:
        audit_details = {
            "action_type": action_type,
            "authority_mode": authority_mode.value,
            "status": status.value,
            "summary": summary,
        }
        self.store.record_outcome(
            run_id=run_id,
            event_id=event.event_id,
            tenant_id=event.tenant_id,
            business_id=event.business_id,
            action_type=action_type,
            authority_mode=authority_mode.value,
            status=status.value,
            summary=summary,
            audit_id=f"audit-{uuid4().hex}",
            audit_type=audit_type,
            audit_details=audit_details,
            processing_worker_id=self.worker_id,
        )
        return ProcessResult(
            run_id=run_id,
            event_id=event.event_id,
            status=status,
            authority_mode=authority_mode,
            summary=summary,
        )
