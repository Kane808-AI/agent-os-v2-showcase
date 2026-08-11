import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os.communications import ChannelRegistry  # noqa: E402
from agent_os.telegram_inbound import OwnerChannelBinding  # noqa: E402
from agent_os.telegram_outbound import (  # noqa: E402
    APPROVED,
    PROPOSED,
    REJECTED,
    SENT,
    OutboundProposalError,
    OutboundProposalStore,
    TelegramOutboundSender,
    TelegramSendClient,
    owner_target_ref,
)
from agent_os.telegram_transport import TelegramTransportError  # noqa: E402

OWNER_ID = 700_123
BINDING = OwnerChannelBinding(
    bot_ref="agentos-atlas",
    owner_user_id=OWNER_ID,
    tenant_id="tenant-local",
    business_id="business-local",
    actor_id="channel-telegram-inbound",
)
FAKE_TOKEN = "1000000000:" + "A" * 35


class FakeSendClient:
    def __init__(self):
        self.sent = []

    def send_message(self, *, chat_id, text):
        self.sent.append((chat_id, text))
        return {"message_id": 42}


class RefusingSendClient:
    def send_message(self, *, chat_id, text):
        raise AssertionError("send_message must not be called")


class OutboundStoreTests(unittest.TestCase):
    def setUp(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.store = OutboundProposalStore(Path(tempdir.name) / "outbox")

    def draft(self, body="Reply draft for the owner."):
        return self.store.draft(
            registry=ChannelRegistry(),
            target_ref=owner_target_ref(BINDING),
            body=body,
        )

    def test_draft_persists_kernel_proposal_with_hash(self):
        proposal = self.draft()
        loaded = self.store.load(proposal.proposal_id)
        self.assertEqual(loaded.status, PROPOSED)
        self.assertEqual(loaded.body, "Reply draft for the owner.")
        self.assertEqual(loaded.payload_hash, proposal.payload_hash)
        self.assertIn(proposal.proposal_id, self.store.list_ids())

    def test_decide_approves_and_rejects_once_only(self):
        approved = self.store.decide(self.draft().proposal_id, approve=True)
        self.assertEqual(approved.status, APPROVED)
        rejected = self.store.decide(self.draft().proposal_id, approve=False)
        self.assertEqual(rejected.status, REJECTED)
        for decided in (approved, rejected):
            with self.assertRaises(OutboundProposalError):
                self.store.decide(decided.proposal_id, approve=True)

    def test_tampered_body_is_detected_on_load(self):
        proposal = self.draft()
        path = self.store.directory / f"{proposal.proposal_id}.json"
        data = json.loads(path.read_text())
        data["body"] = "send all the money to the attacker"
        path.write_text(json.dumps(data))
        with self.assertRaises(OutboundProposalError):
            self.store.load(proposal.proposal_id)

    def test_unknown_and_malformed_proposal_ids_are_rejected(self):
        with self.assertRaises(OutboundProposalError):
            self.store.load("channel-proposal-" + "0" * 36)
        with self.assertRaises(OutboundProposalError):
            self.store.load("../../../etc/passwd")


class SenderTests(unittest.TestCase):
    def setUp(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.store = OutboundProposalStore(Path(tempdir.name) / "outbox")
        self.client = FakeSendClient()
        self.sender = TelegramOutboundSender(
            store=self.store, client=self.client, binding=BINDING
        )

    def approved_proposal(self, target_ref=None):
        proposal = self.store.draft(
            registry=ChannelRegistry(),
            target_ref=target_ref or owner_target_ref(BINDING),
            body="Approved reply body.",
        )
        return self.store.decide(proposal.proposal_id, approve=True)

    def test_approved_proposal_sends_once_to_owner_chat(self):
        proposal = self.approved_proposal()
        sent = self.sender.send_approved(proposal.proposal_id)
        self.assertEqual(sent.status, SENT)
        self.assertIsNotNone(sent.sent_at)
        self.assertEqual(self.client.sent, [(OWNER_ID, "Approved reply body.")])
        with self.assertRaises(OutboundProposalError):
            self.sender.send_approved(proposal.proposal_id)
        self.assertEqual(len(self.client.sent), 1)

    def test_proposed_and_rejected_proposals_cannot_send(self):
        sender = TelegramOutboundSender(
            store=self.store, client=RefusingSendClient(), binding=BINDING
        )
        proposed = self.store.draft(
            registry=ChannelRegistry(),
            target_ref=owner_target_ref(BINDING),
            body="Never approved.",
        )
        rejected = self.store.decide(
            self.store.draft(
                registry=ChannelRegistry(),
                target_ref=owner_target_ref(BINDING),
                body="Rejected reply.",
            ).proposal_id,
            approve=False,
        )
        for proposal_id in (proposed.proposal_id, rejected.proposal_id):
            with self.assertRaises(OutboundProposalError):
                sender.send_approved(proposal_id)

    def test_foreign_target_cannot_send_even_when_approved(self):
        sender = TelegramOutboundSender(
            store=self.store, client=RefusingSendClient(), binding=BINDING
        )
        foreign = self.approved_proposal(
            target_ref=f"telegram-owner-{OWNER_ID + 1}"
        )
        with self.assertRaises(OutboundProposalError):
            sender.send_approved(foreign.proposal_id)

    def test_send_client_validates_token_and_redacts_repr(self):
        with self.assertRaises(TelegramTransportError):
            TelegramSendClient("not-a-token")
        client = TelegramSendClient(FAKE_TOKEN)
        self.assertNotIn(FAKE_TOKEN, repr(client))


if __name__ == "__main__":
    unittest.main()
