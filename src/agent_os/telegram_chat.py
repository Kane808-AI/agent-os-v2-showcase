"""Continuous owner chat loop: listen, draft, decide, send, repeat.

Goal 17 slice 5. This composes the existing verified pieces — inbound
listener, model drafting, outbox proposals, and the one-shot sender — into
one loop. It adds no new capability: every reply still becomes a proposal
and passes an owner decision before the sender runs.

Two decision modes, chosen explicitly at launch:

- interactive (default): each draft is shown and the operator approves or
  rejects it at the terminal before anything is sent.
- standing approval: the operator grants approval for model replies to the
  bound owner's own chat for the lifetime of this process. The grant is an
  explicit launch argument, applies only to owner-chat replies, and every
  send still flows through the same proposal, hash check, and one-shot
  sender. Any other target remains impossible.

Goal 18 slice 2 adds owner work execution to the same cycle: after each poll
the loop claims ready owner-filed work items, executes them through the
shadow runtime, and delivers each result through the identical proposal,
decision, and one-shot send path. The delivery target stays the bound owner's
chat; no new capability or destination is introduced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .runtime import AgentRuntime
from .shadow_runtime import (
    PromptContext,
    ShadowModelRuntime,
    ShadowRuntimeError,
)
from .storage import SQLiteStore
from .telegram_brain import TelegramBrainError, draft_model_reply
from .telegram_hands import OwnerRequestError, file_owner_request
from .telegram_inbound import OwnerChannelBinding, TelegramInboundAdapter
from .telegram_outbound import (
    OutboundProposalStore,
    TelegramOutboundSender,
)
from .telegram_transport import TelegramInboundListener, TelegramUpdatesClient
from .autonomy import LeaseLostError
from .telegram_work import OwnerWorkTurn, execute_ready_owner_work

MAX_WORK_PER_CYCLE = 3


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """Counters and safe error codes only; message content never appears here."""

    event_id: str
    drafted: bool
    decision: str
    sent: bool
    note: str | None = None


class OwnerChatLoop:
    """Drive message -> draft -> decision -> send until stopped."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        binding: OwnerChannelBinding,
        inbound_client: TelegramUpdatesClient,
        model_runtime: ShadowModelRuntime,
        outbox: OutboundProposalStore,
        sender: TelegramOutboundSender,
        decide: Callable[[str], bool],
        standing_owner_approval: bool = False,
        poll_timeout_seconds: int = 25,
        context_provider: Callable[[], tuple[PromptContext, ...]] | None = None,
        typing_notifier: Callable[[], None] | None = None,
        execute_owner_work: bool = True,
    ) -> None:
        self.store = store
        self.binding = binding
        self.adapter = TelegramInboundAdapter(binding)
        self.runtime = AgentRuntime(
            store, worker_id=f"telegram-chat-{binding.bot_ref}"
        )
        self.listener = TelegramInboundListener(
            adapter=self.adapter,
            runtime=self.runtime,
            client=inbound_client,
            poll_timeout_seconds=poll_timeout_seconds,
        )
        self.model_runtime = model_runtime
        self.outbox = outbox
        self.sender = sender
        self.decide = decide
        self.standing_owner_approval = standing_owner_approval
        self.context_provider = context_provider
        self.typing_notifier = typing_notifier
        self.execute_owner_work = execute_owner_work
        self.work_worker_id = f"telegram-work-{binding.bot_ref}"

    def _handle_message(self, event_id: str, message_text: str) -> ChatTurn:
        if self.typing_notifier is not None:
            try:
                self.typing_notifier()
            except Exception:
                pass
        try:
            context = self.context_provider() if self.context_provider else ()
            draft = draft_model_reply(
                store=self.store,
                runtime=self.model_runtime,
                outbox=self.outbox,
                binding=self.binding,
                message_text=message_text,
                source_event_id=event_id,
                context=context,
            )
            proposal = draft.proposal
        except (TelegramBrainError, ShadowRuntimeError) as error:
            # A failed draft must not kill the loop or trigger a crash-restart
            # retry storm; the message stays answered-by-silence and the loop
            # moves on. The safe error code is surfaced for the operator log
            # and the full failure is already recorded by the shadow runtime.
            return ChatTurn(
                event_id=event_id,
                drafted=False,
                decision="none",
                sent=False,
                note=str(error)[:120],
            )
        note = None
        if draft.work_request is not None:
            try:
                filed = file_owner_request(
                    self.store,
                    binding=self.binding,
                    action_type=str(draft.work_request["action_type"]),
                    title=str(draft.work_request["title"]),
                    rationale=str(draft.work_request["rationale"]),
                    source_event_id=event_id,
                )
                note = (
                    f"filed {filed['work_item_id']} ({filed['status']})"
                    if filed["filed"]
                    else "work request was a duplicate"
                )
            except OwnerRequestError as error:
                note = f"work request refused: {error}"
        if self.standing_owner_approval:
            approved = True
        else:
            approved = self.decide(proposal.body)
        self.outbox.decide(proposal.proposal_id, approve=approved)
        if not approved:
            return ChatTurn(
                event_id=event_id,
                drafted=True,
                decision="rejected",
                sent=False,
                note=note,
            )
        self.sender.send_approved(proposal.proposal_id)
        return ChatTurn(
            event_id=event_id,
            drafted=True,
            decision="approved",
            sent=True,
            note=note,
        )

    def _run_owner_work(self) -> list[ChatTurn]:
        """Execute up to MAX_WORK_PER_CYCLE ready owner-filed work items.

        Each result proposal passes the same decision path as a reply:
        standing owner approval or the interactive decide callback, then the
        one-shot sender. A failed execution retries on the work item's own
        backoff and never interrupts the chat loop.
        """
        turns: list[ChatTurn] = []
        for _ in range(MAX_WORK_PER_CYCLE):
            context = self.context_provider() if self.context_provider else ()
            try:
                work_turn: OwnerWorkTurn | None = execute_ready_owner_work(
                    store=self.store,
                    runtime=self.model_runtime,
                    outbox=self.outbox,
                    binding=self.binding,
                    worker_id=self.work_worker_id,
                    context=context,
                )
            except LeaseLostError as error:
                turns.append(
                    ChatTurn(
                        event_id="owner-work",
                        drafted=False,
                        decision="none",
                        sent=False,
                        note=str(error)[:120],
                    )
                )
                break
            if work_turn is None:
                break
            if work_turn.proposal_id is None:
                turns.append(
                    ChatTurn(
                        event_id=work_turn.work_item_id,
                        drafted=False,
                        decision="none",
                        sent=False,
                        note=f"work {work_turn.status}: {work_turn.note}",
                    )
                )
                continue
            proposal = self.outbox.load(work_turn.proposal_id)
            if self.standing_owner_approval:
                approved = True
            else:
                approved = self.decide(proposal.body)
            self.outbox.decide(proposal.proposal_id, approve=approved)
            if approved:
                self.sender.send_approved(proposal.proposal_id)
            turns.append(
                ChatTurn(
                    event_id=work_turn.work_item_id,
                    drafted=True,
                    decision="approved" if approved else "rejected",
                    sent=approved,
                    note=f"work result {work_turn.status}",
                )
            )
        return turns

    def run_cycle(self, offset: int) -> tuple[int, list[ChatTurn]]:
        """Poll once and answer every accepted message; return new offset."""
        updates = self.listener.client.get_updates(
            offset=offset, timeout_seconds=self.listener.poll_timeout_seconds
        )
        turns: list[ChatTurn] = []
        next_offset = offset
        for update in updates:
            update_id = update.get("update_id") if hasattr(update, "get") else None
            if isinstance(update_id, int) and not isinstance(update_id, bool):
                next_offset = max(next_offset, update_id + 1)
            result = self.adapter.ingest_update(update)
            if not result.accepted:
                continue
            processed = self.runtime.process(result.event)
            if processed.duplicate:
                continue
            turns.append(
                self._handle_message(
                    result.event.event_id,
                    str(result.event.payload["message_text"]),
                )
            )
        if self.execute_owner_work:
            turns.extend(self._run_owner_work())
        return next_offset, turns
