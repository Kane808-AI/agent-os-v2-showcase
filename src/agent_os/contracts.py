"""Business-agnostic domain contracts for the Agent OS v2 kernel.

These types deliberately have no dependency on an orchestration runtime,
communication channel, client, or accounting vendor. Runtime and channel
adapters translate to and from these contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping


def _require_identifier(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be a non-empty identifier")


def _require_aware_datetime(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")


class AuthorityMode(StrEnum):
    AUTO = "auto"
    NOTIFY = "notify"
    APPROVE = "approve"
    FORBIDDEN = "forbidden"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class EmergencyStopAction(StrEnum):
    ACTIVATED = "activated"
    CLEARED = "cleared"


PROHIBITED_FINANCIAL_ACTIONS = frozenset(
    {
        "account.close",
        "account.open",
        "bank.transfer",
        "banking.money-movement",
        "bill.pay",
        "contract.sign",
        "finance.payment.execute",
        "ledger.adjust",
        "payment-method.modify",
        "payout.destination.modify",
        "tax.file",
        "vendor-payment.modify",
    }
)


def is_prohibited_financial_action(action_type: str) -> bool:
    """Return whether an action is outside Agent OS financial authority."""
    return action_type in PROHIBITED_FINANCIAL_ACTIONS


def requires_spend_envelope(action_type: str) -> bool:
    """Return whether external execution must consume a durable budget."""
    return action_type.endswith(".spend")


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class ActorType(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SERVICE = "service"


class ObjectiveStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ACHIEVED = "achieved"
    STOPPED = "stopped"


class MemoryType(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: str
    display_name: str
    status: TenantStatus = TenantStatus.ACTIVE

    def __post_init__(self) -> None:
        _require_identifier("tenant_id", self.tenant_id)
        _require_identifier("display_name", self.display_name)


@dataclass(frozen=True, slots=True)
class Business:
    business_id: str
    tenant_id: str
    legal_name: str
    display_name: str
    base_currency: str
    timezone_name: str

    def __post_init__(self) -> None:
        for name in (
            "business_id",
            "tenant_id",
            "legal_name",
            "display_name",
            "timezone_name",
        ):
            _require_identifier(name, getattr(self, name))
        if len(self.base_currency) != 3 or not self.base_currency.isalpha():
            raise ValueError("base_currency must be a three-letter currency code")
        object.__setattr__(self, "base_currency", self.base_currency.upper())


@dataclass(frozen=True, slots=True)
class ActorIdentity:
    actor_id: str
    tenant_id: str
    actor_type: ActorType
    roles: frozenset[str]
    business_ids: frozenset[str]
    enabled: bool = True

    def __post_init__(self) -> None:
        _require_identifier("actor_id", self.actor_id)
        _require_identifier("tenant_id", self.tenant_id)
        if not self.roles:
            raise ValueError("roles cannot be empty")

    def can_access(self, *, tenant_id: str, business_id: str) -> bool:
        return (
            self.enabled
            and tenant_id == self.tenant_id
            and business_id in self.business_ids
        )


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    tenant_id: str
    business_id: str
    source: str
    actor_id: str
    kind: str
    occurred_at: datetime
    payload: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "tenant_id",
            "business_id",
            "source",
            "actor_id",
            "kind",
        ):
            _require_identifier(name, getattr(self, name))
        _require_aware_datetime("occurred_at", self.occurred_at)


@dataclass(frozen=True, slots=True)
class Objective:
    objective_id: str
    tenant_id: str
    business_id: str
    statement: str
    metric: str
    target: Decimal
    current_value: Decimal = Decimal("0")
    status: ObjectiveStatus = ObjectiveStatus.DRAFT
    deadline: datetime | None = None
    priority: int = 100
    review_interval_seconds: int = 86_400

    def __post_init__(self) -> None:
        for name in ("objective_id", "tenant_id", "business_id", "statement", "metric"):
            _require_identifier(name, getattr(self, name))
        if self.deadline is not None:
            _require_aware_datetime("deadline", self.deadline)
        if self.priority < 0:
            raise ValueError("priority cannot be negative")
        if self.review_interval_seconds < 1:
            raise ValueError("review_interval_seconds must be positive")


@dataclass(frozen=True, slots=True)
class ActionRequest:
    action_type: str
    tenant_id: str
    business_id: str
    actor_id: str
    platform: str | None = None
    account_id: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    actor_roles: frozenset[str] = frozenset()
    capability_ids: frozenset[str] = frozenset()
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("action_type", "tenant_id", "business_id", "actor_id"):
            _require_identifier(name, getattr(self, name))
        if self.amount is not None and self.amount < 0:
            raise ValueError("amount cannot be negative")
        if self.amount is not None and not self.currency:
            raise ValueError("currency is required when amount is present")


@dataclass(frozen=True, slots=True)
class AuthorityRule:
    action_type: str
    mode: AuthorityMode
    platforms: frozenset[str] = frozenset()
    accounts: frozenset[str] = frozenset()
    actor_ids: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()
    capability_ids: frozenset[str] = frozenset()
    max_amount: Decimal | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        _require_identifier("action_type", self.action_type)
        if self.max_amount is not None and self.max_amount < 0:
            raise ValueError("max_amount cannot be negative")
        if self.max_amount is not None and not self.currency:
            raise ValueError("currency is required when max_amount is present")

    def matches(self, request: ActionRequest) -> bool:
        if self.action_type != request.action_type:
            return False
        if not (self.actor_ids or self.roles or self.capability_ids):
            return False
        if self.actor_ids and request.actor_id not in self.actor_ids:
            return False
        if self.roles and not (self.roles & request.actor_roles):
            return False
        if (
            self.capability_ids
            and not (self.capability_ids & request.capability_ids)
        ):
            return False
        if self.platforms and request.platform not in self.platforms:
            return False
        if self.accounts and request.account_id not in self.accounts:
            return False
        if self.max_amount is not None:
            if request.amount is None or request.currency != self.currency:
                return False
            if request.amount > self.max_amount:
                return False
        return True


@dataclass(frozen=True, slots=True)
class AuthorityEnvelope:
    envelope_id: str
    tenant_id: str
    business_id: str
    rules: tuple[AuthorityRule, ...]
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("envelope_id", "tenant_id", "business_id"):
            _require_identifier(name, getattr(self, name))
        if self.expires_at is not None:
            _require_aware_datetime("expires_at", self.expires_at)

    def decide(
        self,
        request: ActionRequest,
        *,
        now: datetime | None = None,
    ) -> AuthorityMode:
        if is_prohibited_financial_action(request.action_type):
            return AuthorityMode.FORBIDDEN
        if (
            request.tenant_id != self.tenant_id
            or request.business_id != self.business_id
        ):
            return AuthorityMode.FORBIDDEN

        decision_time = now or datetime.now(timezone.utc)
        _require_aware_datetime("now", decision_time)
        if self.expires_at is not None and decision_time >= self.expires_at:
            return AuthorityMode.FORBIDDEN

        matches = [rule.mode for rule in self.rules if rule.matches(request)]
        if not matches:
            return AuthorityMode.FORBIDDEN
        precedence = {
            AuthorityMode.AUTO: 0,
            AuthorityMode.NOTIFY: 1,
            AuthorityMode.APPROVE: 2,
            AuthorityMode.FORBIDDEN: 3,
        }
        return max(matches, key=precedence.__getitem__)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    tenant_id: str
    business_id: str
    memory_type: MemoryType
    statement: str
    source_type: str
    source_ref: str
    confidence: Decimal
    verification_status: VerificationStatus
    created_at: datetime
    observed_at: datetime
    evidence_refs: tuple[str, ...] = ()
    expires_at: datetime | None = None
    supersedes_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "memory_id",
            "tenant_id",
            "business_id",
            "statement",
            "source_type",
            "source_ref",
        ):
            _require_identifier(name, getattr(self, name))
        if not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("confidence must be between 0 and 1")
        _require_aware_datetime("created_at", self.created_at)
        _require_aware_datetime("observed_at", self.observed_at)
        if self.expires_at is not None:
            _require_aware_datetime("expires_at", self.expires_at)
