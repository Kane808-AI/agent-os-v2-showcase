from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os.shadow_runtime import ShadowModelRuntime  # noqa: E402
from agent_os.storage import SQLiteStore  # noqa: E402
from agent_os.telegram_brain import seed_reply_route  # noqa: E402
from agent_os.telegram_chat import OwnerChatLoop  # noqa: E402
from agent_os.telegram_hands import (  # noqa: E402
    OwnerRequestError,
    file_owner_request,
    owner_objective_id,
)
from agent_os.telegram_inbound import OwnerChannelBinding  # noqa: E402
from agent_os.telegram_outbound import (  # noqa: E402
    OutboundProposalStore,
    TelegramOutboundSender,
)
from tests.test_telegram_brain import (  # noqa: E402
    ScriptedAnthropicAdapter,
    StaticResolver,
)
from tests.test_telegram_outbound import FakeSendClient  # noqa: E402
from tests.test_telegram_transport import FakeClient, owner_update  # noqa: E402

OWNER_ID = 700_123
BINDING = OwnerChannelBinding(
    bot_ref="agentos-atlas",
    owner_user_id=OWNER_ID,
    tenant_id="tenant-local",
    business_id="business-local",
    actor_id="channel-telegram-inbound",
)

RESEARCH_REQUEST = {
    "requested": True,
    "action_type": "affiliate.offer.research",
    "title": "Research trending gadget offers",
    "rationale": "Owner asked for current affiliate offer research.",
}


class HandsTestBase(unittest.TestCase):
    def setUp(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.base = Path(tempdir.name)
        self.store = SQLiteStore(self.base / "hands.db")
        self.store.initialize()
        from agent_os.cli import build_parser, seed_channel_scope

        args = build_parser().parse_args(
            [
                "telegram-listen",
                "--token-env", "/dev/null",
                "--bot-ref", "agentos-atlas",
                "--owner-user-id", str(OWNER_ID),
                "--tenant-id", "tenant-local",
                "--business-id", "business-local",
            ]
        )
        seed_channel_scope(self.store, args)
        seed_reply_route(
            self.store,
            binding=BINDING,
            credential_env_name="ANTHROPIC_TEST_KEY",
            provider_model_ref="anthropic/claude-sonnet-5",
            monthly_budget_micros=20_000_000,
        )


class FileOwnerRequestTests(HandsTestBase):
    def test_owner_request_files_directly_to_ready(self):
        filed = file_owner_request(
            self.store,
            binding=BINDING,
            action_type="affiliate.offer.research",
            title="Research trending gadget offers",
            rationale="Owner asked over the live channel.",
            source_event_id="telegram-agentos-atlas-update-90",
        )
        self.assertTrue(filed["filed"])
        self.assertEqual(filed["status"], "ready")
        work = self.store.get_work_item(filed["work_item_id"])
        self.assertEqual(work["status"], "ready")
        self.assertEqual(work["objective_id"], owner_objective_id(BINDING))
        self.assertEqual(work["assigned_actor_id"], "atlas")

    def test_unruled_action_type_still_holds_or_rejects(self):
        # The AUTO grant is per action type; anything without a rule falls to
        # the envelope default, proving the owner grant is not a blanket one.
        from agent_os.telegram_hands import OWNER_REQUEST_ACTIONS

        self.assertEqual(
            OWNER_REQUEST_ACTIONS,
            ("affiliate.offer.research", "affiliate.content.draft"),
        )

    def test_same_source_event_cannot_file_twice(self):
        for expected_first in (True, False):
            filed = file_owner_request(
                self.store,
                binding=BINDING,
                action_type="affiliate.content.draft",
                title="Draft a product blurb",
                rationale="Owner request.",
                source_event_id="telegram-agentos-atlas-update-91",
            )
            self.assertEqual(filed["filed"], expected_first)
        self.assertTrue(filed["duplicate"])

    def test_disallowed_action_types_are_refused(self):
        for action_type in (
            "finance.transfer_funds",
            "message.send",
            "event.triage",
            "",
        ):
            with self.assertRaises(OwnerRequestError):
                file_owner_request(
                    self.store,
                    binding=BINDING,
                    action_type=action_type,
                    title="x",
                    rationale="y",
                    source_event_id="telegram-agentos-atlas-update-92",
                )

    def test_blank_title_or_rationale_is_refused(self):
        with self.assertRaises(OwnerRequestError):
            file_owner_request(
                self.store,
                binding=BINDING,
                action_type="affiliate.offer.research",
                title="   ",
                rationale="y",
                source_event_id="telegram-agentos-atlas-update-93",
            )


class ChatFilingTests(HandsTestBase):
    def loop(self, batches, *, work_request=None):
        self.send_client = FakeSendClient()
        return OwnerChatLoop(
            store=self.store,
            binding=BINDING,
            inbound_client=FakeClient(batches),
            model_runtime=ShadowModelRuntime(
                self.store,
                credential_resolver=StaticResolver(),
                adapters=(
                    ScriptedAnthropicAdapter(
                        reply="Filed it for your approval.",
                        work_request=work_request,
                    ),
                ),
            ),
            outbox=OutboundProposalStore(self.base / "outbox"),
            sender=TelegramOutboundSender(
                store=OutboundProposalStore(self.base / "outbox"),
                client=self.send_client,
                binding=BINDING,
            ),
            decide=lambda body: True,
            standing_owner_approval=True,
            poll_timeout_seconds=1,
        )

    def test_chat_request_files_work_and_still_replies(self):
        loop = self.loop(
            [[owner_update(95, text="research trending gadget offers")]],
            work_request=dict(RESEARCH_REQUEST),
        )
        _, turns = loop.run_cycle(0)
        self.assertTrue(turns[0].sent)
        self.assertIn("filed work-", turns[0].note)
        self.assertIn("ready", turns[0].note)
        work_items = [
            self.store.get_work_item(turns[0].note.split()[1])
        ]
        self.assertEqual(work_items[0]["action_type"], "affiliate.offer.research")

    def test_plain_question_files_nothing(self):
        loop = self.loop([[owner_update(96, text="how are you?")]])
        _, turns = loop.run_cycle(0)
        self.assertTrue(turns[0].sent)
        self.assertIsNone(turns[0].note)


if __name__ == "__main__":
    unittest.main()
