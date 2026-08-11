"""Owner chat requests become approval-held work items.

Goal 18 slice 1. When the owner asks for real work over the verified channel,
the model may flag a work request alongside its reply. The action type is
constrained to a fixed allowlist at the structured-output schema level, so
channel content can never mint an arbitrary action. Filing runs through the
same storage path the autonomous loop uses: authority is decided by the Goal 9
envelope, the item lands ``awaiting_approval`` with a 24-hour approval
request, and nothing executes until a separate explicit owner decision. The
source event ID is the dedupe key, so a replayed message cannot file twice.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from .contracts import ActionRequest, Objective, ObjectiveStatus
from .storage import SQLiteStore
from .telegram_inbound import OwnerChannelBinding

OWNER_REQUEST_ACTIONS = (
    "affiliate.offer.research",
    "affiliate.content.draft",
)
OWNER_REQUEST_PRIORITY = 20


class OwnerRequestError(ValueError):
    """Raised when a chat work request cannot be filed safely."""


def owner_objective_id(binding: OwnerChannelBinding) -> str:
    return f"owner-requests-{binding.business_id}"


def seed_owner_request_objective(
    store: SQLiteStore,
    *,
    binding: OwnerChannelBinding,
    now: datetime | None = None,
) -> None:
    """Create the standing objective that owner-filed work belongs to."""
    now = now or datetime.now(timezone.utc)
    store.upsert_objective(
        Objective(
            objective_id=owner_objective_id(binding),
            tenant_id=binding.tenant_id,
            business_id=binding.business_id,
            statement="Complete work the owner files from the live channel.",
            metric="owner_requests_completed",
            target=Decimal("1000000"),
            status=ObjectiveStatus.ACTIVE,
            priority=OWNER_REQUEST_PRIORITY,
            review_interval_seconds=86_400,
        ),
        next_review_at=now,
    )


def file_owner_request(
    store: SQLiteStore,
    *,
    binding: OwnerChannelBinding,
    action_type: str,
    title: str,
    rationale: str,
    source_event_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """File one approval-held work item from an owner chat request."""
    if action_type not in OWNER_REQUEST_ACTIONS:
        raise OwnerRequestError(f"action type {action_type!r} is not allowed")
    title = title.strip()
    rationale = rationale.strip()
    if not title or not rationale:
        raise OwnerRequestError("work requests need a title and rationale")
    now = now or datetime.now(timezone.utc)
    authority_mode = store.decide_authority(
        ActionRequest(
            action_type=action_type,
            tenant_id=binding.tenant_id,
            business_id=binding.business_id,
            actor_id="atlas",
            attributes={"source_event_id": source_event_id},
        ),
        now=now,
    )
    work_item_id = f"work-{uuid4().hex}"
    status = {
        "auto": "ready",
        "notify": "ready",
        "approve": "awaiting_approval",
        "forbidden": "rejected",
    }[authority_mode.value]
    inserted = store.enqueue_work_item(
        work_item_id=work_item_id,
        work_key=f"owner-request:{source_event_id}",
        objective_id=owner_objective_id(binding),
        tenant_id=binding.tenant_id,
        business_id=binding.business_id,
        title=title[:200],
        rationale=rationale[:500],
        action_type=action_type,
        assigned_actor_id="atlas",
        platform=None,
        account_id=None,
        amount=None,
        currency=None,
        attributes={"source_event_id": source_event_id, "source": "owner-chat"},
        authority_mode=authority_mode.value,
        status=status,
        priority_score=OWNER_REQUEST_PRIORITY,
        max_attempts=3,
        available_at=now,
        next_review_at=now,
        audit_id=f"audit-{uuid4().hex}",
    )
    if not inserted:
        return {"filed": False, "duplicate": True, "work_item_id": None}
    return {
        "filed": True,
        "duplicate": False,
        "work_item_id": work_item_id,
        "status": status,
    }
