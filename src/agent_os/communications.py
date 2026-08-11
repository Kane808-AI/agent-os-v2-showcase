"""Dashboard-first, proposal-only communication channel contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import re
from typing import Iterable
from uuid import uuid4


class CommunicationError(ValueError):
    """Raised when a communication adapter crosses the proposal boundary."""


class ChannelKind(StrEnum):
    DASHBOARD = "dashboard"
    SLACK = "slack"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    TEAMS = "teams"
    EMAIL = "email"


SAFE_CHANNEL_CAPABILITIES = frozenset({
    "inbound.read",
    "outbound.propose",
    "state.read",
})


@dataclass(frozen=True, slots=True)
class ChannelAdapterDescriptor:
    adapter_id: str
    version: str
    channel: ChannelKind
    capabilities: frozenset[str]
    canonical_control_plane: bool = False
    execution_boundary: str = "proposal-only"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]*", self.adapter_id):
            raise CommunicationError("adapter ID is invalid")
        if not re.fullmatch(r"[1-9][0-9]*\.[0-9]+\.[0-9]+", self.version):
            raise CommunicationError("adapter version must be semantic")
        if self.execution_boundary != "proposal-only":
            raise CommunicationError("channel adapters must remain proposal-only")
        if not self.capabilities or not self.capabilities <= SAFE_CHANNEL_CAPABILITIES:
            raise CommunicationError("channel adapter grants an executing capability")
        if self.canonical_control_plane != (
            self.channel is ChannelKind.DASHBOARD
        ):
            raise CommunicationError("only the dashboard is the canonical control plane")


@dataclass(frozen=True, slots=True)
class OutboundChannelProposal:
    proposal_id: str
    channel: ChannelKind
    adapter_id: str
    target_ref: str
    payload_hash: str
    status: str = "proposed"
    requires_human_approval: bool = True


class ChannelRegistry:
    """Select replaceable adapters without exposing a send operation."""

    def __init__(
        self,
        descriptors: Iterable[ChannelAdapterDescriptor] | None = None,
    ) -> None:
        supplied = tuple(descriptors or self.default_descriptors())
        self._descriptors: dict[ChannelKind, ChannelAdapterDescriptor] = {}
        for descriptor in supplied:
            if descriptor.channel in self._descriptors:
                raise CommunicationError("channel has multiple active adapters")
            self._descriptors[descriptor.channel] = descriptor
        if set(self._descriptors) != set(ChannelKind):
            raise CommunicationError("channel registry coverage is incomplete")
        if not self._descriptors[ChannelKind.DASHBOARD].canonical_control_plane:
            raise CommunicationError("dashboard control plane is missing")

    @staticmethod
    def default_descriptors() -> tuple[ChannelAdapterDescriptor, ...]:
        return (
            ChannelAdapterDescriptor(
                "local-dashboard", "1.0.0", ChannelKind.DASHBOARD,
                frozenset({"state.read", "outbound.propose"}), True,
            ),
            *(
                ChannelAdapterDescriptor(
                    f"{channel.value}-proposal", "1.0.0", channel,
                    frozenset({"inbound.read", "outbound.propose"}),
                )
                for channel in ChannelKind
                if channel is not ChannelKind.DASHBOARD
            ),
        )

    def descriptor(self, channel: ChannelKind) -> ChannelAdapterDescriptor:
        return self._descriptors[channel]

    def replace(
        self,
        channel: ChannelKind,
        descriptor: ChannelAdapterDescriptor,
    ) -> "ChannelRegistry":
        if channel is ChannelKind.DASHBOARD:
            raise CommunicationError("the canonical dashboard cannot be replaced")
        if descriptor.channel is not channel:
            raise CommunicationError("replacement adapter targets another channel")
        updated = dict(self._descriptors)
        updated[channel] = descriptor
        return ChannelRegistry(updated.values())

    def propose(
        self,
        *,
        channel: ChannelKind,
        target_ref: str,
        body: str,
    ) -> OutboundChannelProposal:
        if not target_ref or target_ref != target_ref.strip():
            raise CommunicationError("channel target must be a trimmed opaque reference")
        if not body or body != body.strip():
            raise CommunicationError("channel proposal body must be trimmed")
        descriptor = self.descriptor(channel)
        if "outbound.propose" not in descriptor.capabilities:
            raise CommunicationError("adapter cannot propose outbound communication")
        return OutboundChannelProposal(
            proposal_id=f"channel-proposal-{uuid4()}",
            channel=channel,
            adapter_id=descriptor.adapter_id,
            target_ref=target_ref,
            payload_hash=hashlib.sha256(body.encode()).hexdigest(),
        )
