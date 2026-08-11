"""Long-polling Telegram transport for the owner-verified inbound adapter.

Goal 17 slice 2. This module is the only place that touches the Telegram
network API, and it is read-only: it calls ``getUpdates`` and nothing else.
There is no send operation. The bot token is loaded from a local secrets file
outside version control, held only by the HTTP client, and never logged,
persisted, or included in errors. Message content never appears in summaries
or exceptions; hostile updates are acknowledged and dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping, Protocol, Sequence
import urllib.request

from .runtime import AgentRuntime
from .storage import EventProcessingInProgress
from .telegram_inbound import TelegramInboundAdapter

TOKEN_ENV_KEY = "TELEGRAM_BOT_TOKEN"
_TOKEN_PATTERN = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,50}")


class TelegramTransportError(RuntimeError):
    """Raised for transport misconfiguration; never carries secret material."""


def load_bot_token(env_path: Path) -> str:
    """Read the bot token from a local env file without exposing it.

    Error messages intentionally describe the problem without quoting file
    content, so a malformed secrets file cannot leak into logs.
    """
    if not env_path.is_file():
        raise TelegramTransportError(f"token file is missing: {env_path}")
    token: str | None = None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{TOKEN_ENV_KEY}="):
            token = line.split("=", 1)[1].strip()
    if not token:
        raise TelegramTransportError(f"{TOKEN_ENV_KEY} is missing from {env_path}")
    if not _TOKEN_PATTERN.fullmatch(token):
        raise TelegramTransportError(f"{TOKEN_ENV_KEY} has an unexpected format")
    return token


class TelegramUpdatesClient(Protocol):
    def get_updates(
        self, *, offset: int, timeout_seconds: int
    ) -> Sequence[Mapping[str, Any]]:
        """Fetch pending updates at or after ``offset``."""


class UrllibTelegramClient:
    """Minimal read-only ``getUpdates`` client with a redacted repr."""

    def __init__(self, token: str, *, api_base: str = "https://api.telegram.org"):
        if not _TOKEN_PATTERN.fullmatch(token):
            raise TelegramTransportError("bot token has an unexpected format")
        self._token = token
        self._api_base = api_base.rstrip("/")

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "UrllibTelegramClient(token=<redacted>)"

    def get_updates(
        self, *, offset: int, timeout_seconds: int
    ) -> Sequence[Mapping[str, Any]]:
        request = urllib.request.Request(
            f"{self._api_base}/bot{self._token}/getUpdates",
            data=json.dumps(
                {
                    "offset": offset,
                    "timeout": timeout_seconds,
                    "allowed_updates": ["message"],
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_seconds + 15
            ) as response:
                body = json.load(response)
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                payload = json.load(error)
                description = payload.get("description")
                if isinstance(description, str):
                    detail = f" ({description[:120]})"
            except Exception:
                pass
            raise TelegramTransportError(
                f"getUpdates request failed: HTTP {error.code}{detail}"
            ) from None
        except Exception as error:
            raise TelegramTransportError(
                f"getUpdates request failed: {type(error).__name__}"
            ) from None
        if not isinstance(body, Mapping) or body.get("ok") is not True:
            raise TelegramTransportError("getUpdates returned a non-ok response")
        result = body.get("result")
        if not isinstance(result, list):
            raise TelegramTransportError("getUpdates result is not a list")
        return result


@dataclass(frozen=True, slots=True)
class PollSummary:
    """Counters only; deliberately free of message content."""

    received: int
    accepted: int
    rejected: int
    duplicates: int
    next_offset: int


class TelegramInboundListener:
    """Feed verified updates into the intake runtime; acknowledge the rest."""

    def __init__(
        self,
        *,
        adapter: TelegramInboundAdapter,
        runtime: AgentRuntime,
        client: TelegramUpdatesClient,
        poll_timeout_seconds: int = 25,
    ) -> None:
        if poll_timeout_seconds < 1:
            raise TelegramTransportError("poll timeout must be positive")
        self.adapter = adapter
        self.runtime = runtime
        self.client = client
        self.poll_timeout_seconds = poll_timeout_seconds

    def poll_once(self, offset: int) -> PollSummary:
        updates = self.client.get_updates(
            offset=offset, timeout_seconds=self.poll_timeout_seconds
        )
        received = accepted = rejected = duplicates = 0
        next_offset = offset
        for update in updates:
            received += 1
            update_id = (
                update.get("update_id") if isinstance(update, Mapping) else None
            )
            if (
                not isinstance(update_id, int)
                or isinstance(update_id, bool)
                or update_id < 0
            ):
                raise TelegramTransportError(
                    "getUpdates returned an update without a valid update_id"
                )
            next_offset = max(next_offset, update_id + 1)
            result = self.adapter.ingest_update(update)
            if not result.accepted:
                rejected += 1
                continue
            try:
                processed = self.runtime.process(result.event)
            except EventProcessingInProgress:
                duplicates += 1
                continue
            if processed.duplicate:
                duplicates += 1
            else:
                accepted += 1
        return PollSummary(
            received=received,
            accepted=accepted,
            rejected=rejected,
            duplicates=duplicates,
            next_offset=next_offset,
        )

    def run(
        self,
        *,
        start_offset: int = 0,
        max_cycles: int | None = None,
        idle_sleep_seconds: float = 1.0,
        on_summary=None,
    ) -> int:
        """Poll until ``max_cycles`` completes; return the final offset."""
        offset = start_offset
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            summary = self.poll_once(offset)
            offset = summary.next_offset
            cycles += 1
            if on_summary is not None:
                on_summary(summary)
            if summary.received == 0 and idle_sleep_seconds > 0:
                time.sleep(idle_sleep_seconds)
        return offset
