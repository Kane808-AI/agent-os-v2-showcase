"""Execution truth and independent outcome verification.

This module does not execute external actions. It defines the durable boundary
that a future executor adapter must cross without turning its own narration
into completion truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
from typing import Any, Mapping

from .storage import SQLiteStore


def _require_identifier(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be a non-empty identifier")


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")


class EvidenceKind(StrEnum):
    PRECONDITION = "precondition"
    EXTERNAL_READBACK = "external_readback"
    MACHINE_CHECK = "machine_check"
    ARTIFACT = "artifact"
    EXECUTOR_REPORT = "executor_report"


class ExecutionMode(StrEnum):
    SIMULATED = "simulated"
    EXTERNAL = "external"


class VerificationDecision(StrEnum):
    VERIFIED = "verified"
    DISPROVED = "disproved"
    INCONCLUSIVE = "inconclusive"


class ExecutionTruthError(RuntimeError):
    """The requested execution-truth transition is invalid."""


class StaleStateError(ExecutionTruthError):
    """Evidence or a state precondition is no longer current enough to use."""


class IndependentVerificationError(ExecutionTruthError):
    """A producer attempted to verify its own outcome."""


class UnverifiedOutcomeError(ExecutionTruthError):
    """A completion claim was requested without verified evidence."""


@dataclass(frozen=True, slots=True)
class EvidenceReceipt:
    receipt_id: str
    work_item_id: str
    tenant_id: str
    business_id: str
    evidence_kind: EvidenceKind
    source_system: str
    source_ref: str
    captured_by: str
    observed_at: datetime
    valid_until: datetime
    payload: Mapping[str, Any]
    created_at: datetime
    issuer_version: str | None = None
    attempt_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "receipt_id",
            "work_item_id",
            "tenant_id",
            "business_id",
            "source_system",
            "source_ref",
            "captured_by",
        ):
            _require_identifier(name, getattr(self, name))
        _require_aware("observed_at", self.observed_at)
        _require_aware("valid_until", self.valid_until)
        _require_aware("created_at", self.created_at)
        if self.valid_until <= self.observed_at:
            raise ValueError("valid_until must be after observed_at")
        if (
            self.evidence_kind
            in {
                EvidenceKind.PRECONDITION,
                EvidenceKind.EXTERNAL_READBACK,
                EvidenceKind.MACHINE_CHECK,
            }
            and not self.issuer_version
        ):
            raise ValueError(
                "trusted evidence requires a registered issuer version"
            )
        try:
            json.dumps(self.payload, sort_keys=True)
        except (TypeError, ValueError) as error:
            raise ValueError("evidence payload must be JSON serializable") from error

    @property
    def content_hash(self) -> str:
        def utc(value: datetime) -> str:
            return value.astimezone(timezone.utc).isoformat()

        canonical = {
            "attempt_id": self.attempt_id,
            "business_id": self.business_id,
            "captured_by": self.captured_by,
            "created_at": utc(self.created_at),
            "evidence_kind": self.evidence_kind.value,
            "issuer_version": self.issuer_version,
            "observed_at": utc(self.observed_at),
            "payload": self.payload,
            "receipt_id": self.receipt_id,
            "source_ref": self.source_ref,
            "source_system": self.source_system,
            "tenant_id": self.tenant_id,
            "valid_until": utc(self.valid_until),
            "work_item_id": self.work_item_id,
        }
        encoded = json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def as_record(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "work_item_id": self.work_item_id,
            "attempt_id": self.attempt_id,
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "evidence_kind": self.evidence_kind.value,
            "issuer_version": self.issuer_version,
            "source_system": self.source_system,
            "source_ref": self.source_ref,
            "captured_by": self.captured_by,
            "observed_at": self.observed_at,
            "valid_until": self.valid_until,
            "payload": dict(self.payload),
            "content_hash": self.content_hash,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class ExecutionAttempt:
    attempt_id: str
    work_item_id: str
    tenant_id: str
    business_id: str
    producer_id: str
    execution_mode: ExecutionMode
    action_type: str
    target_ref: str
    idempotency_key: str
    summary: str
    attempted_at: datetime
    precondition_receipt_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "attempt_id",
            "work_item_id",
            "tenant_id",
            "business_id",
            "producer_id",
            "action_type",
            "target_ref",
            "idempotency_key",
            "summary",
        ):
            _require_identifier(name, getattr(self, name))
        _require_aware("attempted_at", self.attempted_at)
        if (
            self.execution_mode is ExecutionMode.EXTERNAL
            and not self.precondition_receipt_id
        ):
            raise ValueError(
                "external execution requires a precondition receipt"
            )

    def as_record(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "work_item_id": self.work_item_id,
            "tenant_id": self.tenant_id,
            "business_id": self.business_id,
            "producer_id": self.producer_id,
            "execution_mode": self.execution_mode.value,
            "action_type": self.action_type,
            "target_ref": self.target_ref,
            "idempotency_key": self.idempotency_key,
            "precondition_receipt_id": self.precondition_receipt_id,
            "summary": self.summary,
            "attempted_at": self.attempted_at,
        }


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    verification_id: str
    attempt_id: str
    verifier_id: str
    decision: VerificationDecision
    evidence_receipt_ids: tuple[str, ...]
    expected_facts: Mapping[str, Any]
    rationale: str
    policy_version: str
    decided_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "verification_id",
            "attempt_id",
            "verifier_id",
            "rationale",
            "policy_version",
        ):
            _require_identifier(name, getattr(self, name))
        _require_aware("decided_at", self.decided_at)
        if not self.evidence_receipt_ids:
            raise ValueError("verification requires evidence receipts")
        if len(self.evidence_receipt_ids) != len(
            set(self.evidence_receipt_ids)
        ):
            raise ValueError("verification evidence receipts must be unique")
        if (
            self.decision is VerificationDecision.VERIFIED
            and not self.expected_facts
        ):
            raise ValueError(
                "verified decisions require explicit expected facts"
            )


@dataclass(frozen=True, slots=True)
class CompletionClaim:
    work_item_id: str
    attempt_id: str
    observed_result: Mapping[str, Any]
    evidence_refs: tuple[str, ...]
    verified_at: datetime
    verification_state: VerificationDecision
    verifier_id: str


class OutcomeVerificationService:
    """Enforce execution evidence, freshness, and verifier separation."""

    VERIFIER_ROLES = frozenset({"qa", "qa-verifier", "verifier"})
    AUTHORITATIVE_EVIDENCE = frozenset(
        {
            EvidenceKind.EXTERNAL_READBACK.value,
            EvidenceKind.MACHINE_CHECK.value,
        }
    )

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def _require_actor(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        business_id: str,
    ) -> Any:
        actor = self.store.get_actor(actor_id)
        if actor is None or not actor.can_access(
            tenant_id=tenant_id,
            business_id=business_id,
        ):
            raise ExecutionTruthError(
                "actor is outside the execution identity boundary"
            )
        return actor

    def _validate_receipt_integrity(
        self,
        receipt: Mapping[str, Any],
    ) -> None:
        materialized = EvidenceReceipt(
            receipt_id=receipt["receipt_id"],
            work_item_id=receipt["work_item_id"],
            attempt_id=receipt["attempt_id"],
            tenant_id=receipt["tenant_id"],
            business_id=receipt["business_id"],
            evidence_kind=EvidenceKind(receipt["evidence_kind"]),
            issuer_version=receipt["issuer_version"],
            source_system=receipt["source_system"],
            source_ref=receipt["source_ref"],
            captured_by=receipt["captured_by"],
            observed_at=receipt["observed_at"],
            valid_until=receipt["valid_until"],
            payload=receipt["payload"],
            created_at=receipt["created_at"],
        )
        if materialized.content_hash != receipt["content_hash"]:
            raise ExecutionTruthError(
                "evidence receipt content hash does not match its payload"
            )

    def capture_precondition(self, receipt: EvidenceReceipt) -> bool:
        if receipt.evidence_kind is not EvidenceKind.PRECONDITION:
            raise ExecutionTruthError(
                "precondition capture requires precondition evidence"
            )
        if receipt.attempt_id is not None:
            raise ExecutionTruthError(
                "precondition evidence must precede an attempt"
            )
        self._require_actor(
            actor_id=receipt.captured_by,
            tenant_id=receipt.tenant_id,
            business_id=receipt.business_id,
        )
        try:
            return self.store.record_precondition_receipt(
                receipt.as_record()
            )
        except ValueError as error:
            raise ExecutionTruthError(str(error)) from error

    def register_evidence_issuer(
        self,
        *,
        tenant_id: str,
        business_id: str,
        source_system: str,
        evidence_kind: EvidenceKind,
        actor_id: str,
        issuer_version: str,
    ) -> None:
        try:
            self.store.register_evidence_issuer(
                tenant_id=tenant_id,
                business_id=business_id,
                source_system=source_system,
                evidence_kind=evidence_kind.value,
                actor_id=actor_id,
                issuer_version=issuer_version,
            )
        except ValueError as error:
            raise ExecutionTruthError(str(error)) from error

    def begin_attempt(
        self,
        attempt: ExecutionAttempt,
        *,
        worker_id: str,
    ) -> bool:
        self._require_actor(
            actor_id=attempt.producer_id,
            tenant_id=attempt.tenant_id,
            business_id=attempt.business_id,
        )
        if attempt.execution_mode is ExecutionMode.EXTERNAL:
            precondition = self.store.get_evidence_receipt(
                str(attempt.precondition_receipt_id)
            )
            if precondition is None:
                raise ExecutionTruthError(
                    "external attempt precondition receipt is missing"
                )
            self._validate_receipt_integrity(precondition)
            if (
                precondition["evidence_kind"]
                != EvidenceKind.PRECONDITION.value
                or precondition["attempt_id"] is not None
                or precondition["work_item_id"] != attempt.work_item_id
                or precondition["tenant_id"] != attempt.tenant_id
                or precondition["business_id"] != attempt.business_id
                or precondition["source_ref"] != attempt.target_ref
            ):
                raise ExecutionTruthError(
                    "precondition does not match the attempted target and scope"
                )
            if (
                precondition["observed_at"] > attempt.attempted_at
                or precondition["valid_until"] <= attempt.attempted_at
            ):
                raise StaleStateError(
                    "external attempt precondition is stale"
                )
        try:
            return self.store.begin_execution_attempt(
                attempt=attempt.as_record(),
                worker_id=worker_id,
            )
        except ValueError as error:
            raise ExecutionTruthError(str(error)) from error

    def attach_evidence(self, receipt: EvidenceReceipt) -> bool:
        if receipt.attempt_id is None:
            raise ExecutionTruthError(
                "post-attempt evidence requires an attempt ID"
            )
        attempt = self.store.get_execution_attempt(receipt.attempt_id)
        if attempt is None:
            raise ExecutionTruthError("evidence references an unknown attempt")
        self._require_actor(
            actor_id=receipt.captured_by,
            tenant_id=receipt.tenant_id,
            business_id=receipt.business_id,
        )
        if (
            receipt.work_item_id != attempt["work_item_id"]
            or receipt.tenant_id != attempt["tenant_id"]
            or receipt.business_id != attempt["business_id"]
            or receipt.source_ref != attempt["target_ref"]
        ):
            raise ExecutionTruthError(
                "evidence crosses the attempt target or identity boundary"
            )
        if receipt.observed_at < attempt["attempted_at"]:
            raise StaleStateError(
                "post-attempt evidence predates the execution attempt"
            )
        try:
            return self.store.attach_outcome_receipt(receipt.as_record())
        except ValueError as error:
            raise ExecutionTruthError(str(error)) from error

    def claim_uncertain_attempt(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int = 300,
    ) -> dict[str, Any] | None:
        return self.store.claim_uncertain_attempt(
            worker_id=worker_id,
            now=now,
            lease_seconds=lease_seconds,
        )

    def defer_uncertain_attempt(
        self,
        *,
        attempt_id: str,
        worker_id: str,
        error: str,
        now: datetime,
        retry_seconds: int = 60,
    ) -> str:
        if retry_seconds < 1:
            raise ValueError("retry_seconds must be positive")
        try:
            return self.store.fail_attempt_reconciliation(
                attempt_id=attempt_id,
                worker_id=worker_id,
                error=error,
                retry_at=now + timedelta(seconds=retry_seconds),
                now=now,
            )
        except ValueError as error_value:
            raise ExecutionTruthError(str(error_value)) from error_value

    def verify(self, request: VerificationRequest) -> bool:
        attempt = self.store.get_execution_attempt(request.attempt_id)
        if attempt is None:
            raise ExecutionTruthError("verification references an unknown attempt")
        verifier = self._require_actor(
            actor_id=request.verifier_id,
            tenant_id=attempt["tenant_id"],
            business_id=attempt["business_id"],
        )
        if request.verifier_id == attempt["producer_id"]:
            raise IndependentVerificationError(
                "the producing actor cannot verify its own work"
            )
        if not (verifier.roles & self.VERIFIER_ROLES):
            raise IndependentVerificationError(
                "the verifier lacks an independent assurance role"
            )

        receipts_by_id = {
            receipt["receipt_id"]: receipt
            for receipt in self.store.list_attempt_receipts(
                request.attempt_id
            )
        }
        try:
            receipts = [
                receipts_by_id[receipt_id]
                for receipt_id in request.evidence_receipt_ids
            ]
        except KeyError as error:
            raise ExecutionTruthError(
                "verification evidence is missing or belongs to another attempt"
            ) from error
        authoritative = [
            receipt
            for receipt in receipts
            if receipt["evidence_kind"] in self.AUTHORITATIVE_EVIDENCE
        ]
        if not authoritative:
            raise ExecutionTruthError(
                "executor narration or artifacts cannot verify an outcome"
            )
        for receipt in authoritative:
            self._validate_receipt_integrity(receipt)
            if (
                receipt["observed_at"] < attempt["attempted_at"]
                or receipt["observed_at"] > request.decided_at
                or receipt["valid_until"] <= request.decided_at
            ):
                raise StaleStateError(
                    "verification evidence is stale or future-dated"
                )

        observed_result: dict[str, Any] = {}
        for receipt in authoritative:
            observed_result.update(receipt["payload"])
        if request.decision is VerificationDecision.VERIFIED:
            if attempt["execution_mode"] == ExecutionMode.SIMULATED.value:
                raise ExecutionTruthError(
                    "simulated execution cannot become a verified completion"
                )
            if attempt["status"] != "observed":
                raise ExecutionTruthError(
                    "an outcome must be externally observed before verification"
                )
            mismatches = {
                key: expected
                for key, expected in request.expected_facts.items()
                if observed_result.get(key) != expected
            }
            if mismatches:
                raise ExecutionTruthError(
                    "observed result does not satisfy the expected facts"
                )

        record = {
            "verification_id": request.verification_id,
            "attempt_id": request.attempt_id,
            "work_item_id": attempt["work_item_id"],
            "tenant_id": attempt["tenant_id"],
            "business_id": attempt["business_id"],
            "verifier_id": request.verifier_id,
            "decision": request.decision.value,
            "evidence_receipt_ids": list(request.evidence_receipt_ids),
            "expected_facts": dict(request.expected_facts),
            "rationale": request.rationale,
            "policy_version": request.policy_version,
            "decided_at": request.decided_at,
        }
        try:
            return self.store.record_outcome_verification(record)
        except ValueError as error:
            raise ExecutionTruthError(str(error)) from error

    def completion_claim(self, work_item_id: str) -> CompletionClaim:
        work = self.store.get_work_item(work_item_id)
        verification = self.store.get_latest_verification_for_work(
            work_item_id
        )
        if (
            work is None
            or work["status"] != VerificationDecision.VERIFIED.value
            or verification is None
            or verification["decision"]
            != VerificationDecision.VERIFIED.value
        ):
            raise UnverifiedOutcomeError(
                "work has no independently verified completion"
            )
        attempt = self.store.get_execution_attempt(
            verification["attempt_id"]
        )
        if attempt is None or attempt["status"] != "verified":
            raise UnverifiedOutcomeError(
                "verification has no matching terminal attempt"
            )
        if attempt["execution_mode"] != ExecutionMode.EXTERNAL.value:
            raise UnverifiedOutcomeError(
                "simulated attempt cannot support completion"
            )
        if not verification["evidence_receipt_ids"]:
            raise UnverifiedOutcomeError(
                "verified completion has no evidence receipts"
            )
        receipts = {
            receipt["receipt_id"]: receipt
            for receipt in self.store.list_attempt_receipts(
                verification["attempt_id"]
            )
        }
        observed_result: dict[str, Any] = {}
        attested_receipts: list[dict[str, Any]] = []
        for receipt_id in verification["evidence_receipt_ids"]:
            receipt = receipts.get(receipt_id)
            if (
                receipt is not None
                and receipt["evidence_kind"] in self.AUTHORITATIVE_EVIDENCE
                and receipt["attempt_id"] == attempt["attempt_id"]
                and receipt["source_ref"] == attempt["target_ref"]
                and receipt["observed_at"] >= attempt["attempted_at"]
                and receipt["observed_at"] <= verification["decided_at"]
                and receipt["valid_until"] > verification["decided_at"]
            ):
                self._validate_receipt_integrity(receipt)
                attested_receipts.append(receipt)
                observed_result.update(receipt["payload"])
        if (
            len(attested_receipts)
            != len(verification["evidence_receipt_ids"])
            or not self.store.verify_completion_attestation(
                verification,
                attempt,
                attested_receipts,
            )
            or not observed_result
            or any(
                observed_result.get(key) != value
                for key, value in verification["expected_facts"].items()
            )
        ):
            raise UnverifiedOutcomeError(
                "verified completion attestation or evidence is invalid"
            )
        return CompletionClaim(
            work_item_id=work_item_id,
            attempt_id=verification["attempt_id"],
            observed_result=observed_result,
            evidence_refs=tuple(verification["evidence_receipt_ids"]),
            verified_at=verification["decided_at"],
            verification_state=VerificationDecision.VERIFIED,
            verifier_id=verification["verifier_id"],
        )
