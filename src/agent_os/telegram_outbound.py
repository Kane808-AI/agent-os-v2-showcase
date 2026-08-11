"""Approval-gated Telegram outbound: draft, human decision, one-shot send.

Goal 17 slice 3. Every outbound message exists first as a kernel
``OutboundChannelProposal`` persisted to a local outbox with its body and
SHA-256 hash. A separate, explicit human decision approves or rejects it.
Only an approved proposal can be sent, only to the bound owner's private
chat, only once. The body hash is re-verified at decision time and again at
send time, so a tampered outbox file can never be sent. The sender client
can call ``sendMessage`` and nothing else, and it exists only in this
transport layer; the kernel remains proposal-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping
import urllib.request

from .communications import ChannelKind, ChannelRegistry, OutboundChannelProposal
from .telegram_inbound import OwnerChannelBinding
from .telegram_transport import TelegramTransportError, _TOKEN_PATTERN

PROPOSED = "proposed"
APPROVED = "approved"
REJECTED = "rejected"
SENDING = "sending"
SENT = "sent"

_DECIDABLE = frozenset({PROPOSED})
_SENDABLE = frozenset({APPROVED})
_PROPOSAL_ID_PATTERN = re.compile(r"channel-proposal-[0-9a-f-]{36}")


class OutboundProposalError(RuntimeError):
    """Raised when an outbound proposal transition would be unsafe."""


def _body_hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class StoredProposal:
    proposal_id: str
    target_ref: str
    payload_hash: str
    body: str
    status: str
    created_at: str
    decided_at: str | None = None
    sent_at: str | None = None

    def verify_integrity(self) -> None:
        if _body_hash(self.body) != self.payload_hash:
            raise OutboundProposalError(
                f"proposal {self.proposal_id} body does not match its recorded hash"
            )


class OutboundProposalStore:
    """File-backed outbox with enforced one-way status transitions."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, proposal_id: str) -> Path:
        if not _PROPOSAL_ID_PATTERN.fullmatch(proposal_id):
            raise OutboundProposalError("proposal id has an unexpected format")
        return self.directory / f"{proposal_id}.json"

    def _write(self, proposal: StoredProposal) -> None:
        path = self._path(proposal.proposal_id)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(
                {
                    "proposal_id": proposal.proposal_id,
                    "target_ref": proposal.target_ref,
                    "payload_hash": proposal.payload_hash,
                    "body": proposal.body,
                    "status": proposal.status,
                    "created_at": proposal.created_at,
                    "decided_at": proposal.decided_at,
                    "sent_at": proposal.sent_at,
                },
                indent=2,
                sort_keys=True,
            )
        )
        temp.replace(path)

    def load(self, proposal_id: str) -> StoredProposal:
        path = self._path(proposal_id)
        if not path.is_file():
            raise OutboundProposalError(f"proposal {proposal_id} does not exist")
        data = json.loads(path.read_text())
        proposal = StoredProposal(**data)
        proposal.verify_integrity()
        return proposal

    def list_ids(self) -> list[str]:
        return sorted(
            path.stem for path in self.directory.glob("channel-proposal-*.json")
        )

    def draft(
        self,
        *,
        registry: ChannelRegistry,
        target_ref: str,
        body: str,
    ) -> StoredProposal:
        kernel_proposal: OutboundChannelProposal = registry.propose(
            channel=ChannelKind.TELEGRAM,
            target_ref=target_ref,
            body=body,
        )
        stored = StoredProposal(
            proposal_id=kernel_proposal.proposal_id,
            target_ref=kernel_proposal.target_ref,
            payload_hash=kernel_proposal.payload_hash,
            body=body,
            status=PROPOSED,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        stored.verify_integrity()
        self._write(stored)
        return stored

    def decide(self, proposal_id: str, *, approve: bool) -> StoredProposal:
        proposal = self.load(proposal_id)
        if proposal.status not in _DECIDABLE:
            raise OutboundProposalError(
                f"proposal {proposal_id} is {proposal.status}; only a proposed "
                "message can be decided"
            )
        decided = StoredProposal(
            proposal_id=proposal.proposal_id,
            target_ref=proposal.target_ref,
            payload_hash=proposal.payload_hash,
            body=proposal.body,
            status=APPROVED if approve else REJECTED,
            created_at=proposal.created_at,
            decided_at=datetime.now(timezone.utc).isoformat(),
        )
        self._write(decided)
        return decided

    def mark(self, proposal_id: str, *, status: str) -> StoredProposal:
        proposal = self.load(proposal_id)
        allowed = {
            SENDING: _SENDABLE,
            SENT: frozenset({SENDING}),
        }.get(status)
        if allowed is None or proposal.status not in allowed:
            raise OutboundProposalError(
                f"proposal {proposal_id} cannot move from "
                f"{proposal.status} to {status}"
            )
        updated = StoredProposal(
            proposal_id=proposal.proposal_id,
            target_ref=proposal.target_ref,
            payload_hash=proposal.payload_hash,
            body=proposal.body,
            status=status,
            created_at=proposal.created_at,
            decided_at=proposal.decided_at,
            sent_at=(
                datetime.now(timezone.utc).isoformat()
                if status == SENT
                else proposal.sent_at
            ),
        )
        self._write(updated)
        return updated


class TelegramSendClient:
    """Minimal outbound client; ``send_message`` is the only content path.

    ``send_chat_action`` transmits a presence signal ("typing") and no
    content; it exists so the owner can see the assistant working.
    """

    def __init__(self, token: str, *, api_base: str = "https://api.telegram.org"):
        if not _TOKEN_PATTERN.fullmatch(token):
            raise TelegramTransportError("bot token has an unexpected format")
        self._token = token
        self._api_base = api_base.rstrip("/")

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "TelegramSendClient(token=<redacted>)"

    def send_chat_action(self, *, chat_id: int) -> None:
        """Best-effort typing indicator; failures are deliberately ignored."""
        request = urllib.request.Request(
            f"{self._api_base}/bot{self._token}/sendChatAction",
            data=json.dumps({"chat_id": chat_id, "action": "typing"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10):
                pass
        except Exception:
            pass

    def send_message(self, *, chat_id: int, text: str) -> Mapping[str, Any]:
        request = urllib.request.Request(
            f"{self._api_base}/bot{self._token}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.load(response)
        except Exception as error:
            raise TelegramTransportError(
                f"sendMessage request failed: {type(error).__name__}"
            ) from None
        if not isinstance(body, Mapping) or body.get("ok") is not True:
            raise TelegramTransportError("sendMessage returned a non-ok response")
        result = body.get("result")
        if not isinstance(result, Mapping):
            raise TelegramTransportError("sendMessage result is malformed")
        return result


def owner_target_ref(binding: OwnerChannelBinding) -> str:
    """The only outbound target this slice permits: the bound owner's chat."""
    return f"telegram-owner-{binding.owner_user_id}"


class TelegramOutboundSender:
    """Execute exactly one approved proposal, exactly once, to the owner only."""

    def __init__(
        self,
        *,
        store: OutboundProposalStore,
        client: TelegramSendClient,
        binding: OwnerChannelBinding,
    ) -> None:
        self.store = store
        self.client = client
        self.binding = binding

    def send_approved(self, proposal_id: str) -> StoredProposal:
        proposal = self.store.load(proposal_id)
        if proposal.status != APPROVED:
            raise OutboundProposalError(
                f"proposal {proposal_id} is {proposal.status}; only an approved "
                "proposal can be sent"
            )
        if proposal.target_ref != owner_target_ref(self.binding):
            raise OutboundProposalError(
                f"proposal {proposal_id} targets a chat outside the owner binding"
            )
        proposal.verify_integrity()
        self.store.mark(proposal_id, status=SENDING)
        self.client.send_message(
            chat_id=self.binding.owner_user_id,
            text=proposal.body,
        )
        return self.store.mark(proposal_id, status=SENT)
