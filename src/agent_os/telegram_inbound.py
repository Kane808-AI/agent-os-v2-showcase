"""Owner-verified Telegram inbound adapter for tenant-scoped intake events.

Goal 17 slice 1. This module is transport-free: it never holds a bot token,
never opens a network connection, and never sends. It turns one already
received Telegram Bot API update object into at most one intake ``Event``.
Inbound channel content is untrusted data; the event kind is a constant and
message text is quarantined under a single payload key so it can never select
an action.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping

from .communications import ChannelAdapterDescriptor, ChannelKind
from .contracts import Event

INBOUND_EVENT_KIND = "channel.message.received"
"""Constant event kind for inbound channel messages.

Never derived from message content, and deliberately distinct from
``command.received`` so channel text cannot reach the planner's
``requested_action`` path.
"""

_BOT_REF_PATTERN = re.compile(r"[a-z][a-z0-9-]*")


class TelegramInboundError(ValueError):
    """Raised when the adapter is configured unsafely."""


@dataclass(frozen=True, slots=True)
class OwnerChannelBinding:
    """Bind one Telegram bot identity to one verified owner and tenant scope.

    ``bot_ref`` is an opaque local identity for the v2 bot, distinct from any
    legacy bot identity. It is never a token; token-shaped values are rejected
    so a credential cannot be misused as an identifier.
    """

    bot_ref: str
    owner_user_id: int
    tenant_id: str
    business_id: str
    actor_id: str

    def __post_init__(self) -> None:
        if not _BOT_REF_PATTERN.fullmatch(self.bot_ref):
            raise TelegramInboundError(
                "bot_ref must be an opaque lowercase identifier, never a token"
            )
        if (
            not isinstance(self.owner_user_id, int)
            or isinstance(self.owner_user_id, bool)
            or self.owner_user_id <= 0
        ):
            raise TelegramInboundError("owner_user_id must be a positive integer")
        for name in ("tenant_id", "business_id", "actor_id"):
            value = getattr(self, name)
            if not value or not value.strip():
                raise TelegramInboundError(f"{name} must be a non-empty identifier")


@dataclass(frozen=True, slots=True)
class InboundResult:
    """Outcome of one update: an accepted event or a typed rejection."""

    accepted: bool
    event: Event | None = None
    rejection_reason: str | None = None


def _reject(reason: str) -> InboundResult:
    return InboundResult(accepted=False, rejection_reason=reason)


class TelegramInboundAdapter:
    """Convert owner-verified Telegram updates into intake events.

    Capabilities stay inside the proposal boundary: this adapter reads inbound
    updates and exposes no send, edit, or delete operation of any kind.
    """

    def __init__(self, binding: OwnerChannelBinding) -> None:
        self.binding = binding
        self.descriptor = ChannelAdapterDescriptor(
            adapter_id="telegram-live-inbound",
            version="1.0.0",
            channel=ChannelKind.TELEGRAM,
            capabilities=frozenset({"inbound.read", "outbound.propose"}),
        )

    def ingest_update(self, update: Any) -> InboundResult:
        """Accept one Telegram update object; reject everything unverified.

        Rejections are values, not exceptions, so a hostile or malformed
        update cannot crash the intake path.
        """
        if not isinstance(update, Mapping):
            return _reject("update_not_a_mapping")
        update_id = update.get("update_id")
        if not isinstance(update_id, int) or isinstance(update_id, bool):
            return _reject("update_id_missing_or_invalid")
        if update_id < 0:
            return _reject("update_id_missing_or_invalid")

        message = update.get("message")
        if not isinstance(message, Mapping):
            return _reject("unsupported_update_type")

        sender = message.get("from")
        if not isinstance(sender, Mapping):
            return _reject("sender_missing")
        if sender.get("is_bot") is not False:
            return _reject("sender_is_bot")
        if sender.get("id") != self.binding.owner_user_id:
            return _reject("sender_not_verified_owner")

        chat = message.get("chat")
        if not isinstance(chat, Mapping):
            return _reject("chat_missing")
        if chat.get("type") != "private":
            return _reject("chat_not_private")
        if chat.get("id") != self.binding.owner_user_id:
            return _reject("chat_not_owner_direct")

        message_date = message.get("date")
        if not isinstance(message_date, int) or isinstance(message_date, bool):
            return _reject("message_date_missing_or_invalid")
        if message_date <= 0:
            return _reject("message_date_missing_or_invalid")

        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return _reject("unsupported_content_type")

        message_id = message.get("message_id")
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            return _reject("message_id_missing_or_invalid")

        event_id = f"telegram-{self.binding.bot_ref}-update-{update_id}"
        event = Event(
            event_id=event_id,
            tenant_id=self.binding.tenant_id,
            business_id=self.binding.business_id,
            source=f"telegram-{self.binding.bot_ref}",
            actor_id=self.binding.actor_id,
            kind=INBOUND_EVENT_KIND,
            occurred_at=datetime.fromtimestamp(message_date, tz=timezone.utc),
            payload={
                "channel": ChannelKind.TELEGRAM.value,
                "adapter_id": self.descriptor.adapter_id,
                "telegram_update_id": update_id,
                "telegram_message_id": message_id,
                "message_text": text,
            },
            idempotency_key=event_id,
        )
        return InboundResult(accepted=True, event=event)
