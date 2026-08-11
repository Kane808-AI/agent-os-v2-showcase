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
from agent_os.telegram_inbound import OwnerChannelBinding  # noqa: E402
from agent_os.telegram_outbound import (  # noqa: E402
    REJECTED,
    SENT,
    OutboundProposalStore,
    TelegramOutboundSender,
)
from tests.test_telegram_brain import (  # noqa: E402
    ScriptedAnthropicAdapter,
    StaticResolver,
)
from tests.test_telegram_outbound import FakeSendClient  # noqa: E402
from tests.test_telegram_transport import (  # noqa: E402
    FakeClient,
    intruder_update,
    owner_update,
)

OWNER_ID = 700_123
BINDING = OwnerChannelBinding(
    bot_ref="agentos-atlas",
    owner_user_id=OWNER_ID,
    tenant_id="tenant-local",
    business_id="business-local",
    actor_id="channel-telegram-inbound",
)


class OwnerChatLoopTests(unittest.TestCase):
    def setUp(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        base = Path(tempdir.name)
        self.store = SQLiteStore(base / "chat.db")
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
        self.outbox = OutboundProposalStore(base / "outbox")
        self.send_client = FakeSendClient()
        self.decisions: list[str] = []

    def loop(self, batches, *, approve=True, standing=False, adapter=None):
        def decide(body: str) -> bool:
            self.decisions.append(body)
            return approve

        self.typing_calls = 0

        def typing():
            self.typing_calls += 1

        return OwnerChatLoop(
            store=self.store,
            binding=BINDING,
            inbound_client=FakeClient(batches),
            model_runtime=ShadowModelRuntime(
                self.store,
                credential_resolver=StaticResolver(),
                adapters=(
                    adapter or ScriptedAnthropicAdapter(reply="Drafted answer."),
                ),
            ),
            outbox=self.outbox,
            sender=TelegramOutboundSender(
                store=self.outbox, client=self.send_client, binding=BINDING
            ),
            decide=decide,
            standing_owner_approval=standing,
            poll_timeout_seconds=1,
            typing_notifier=typing,
        )

    def test_approved_turn_drafts_decides_and_sends(self):
        loop = self.loop([[owner_update(1, text="hello atlas")]])
        offset, turns = loop.run_cycle(0)
        self.assertEqual(offset, 2)
        self.assertEqual(len(turns), 1)
        self.assertTrue(turns[0].sent)
        self.assertEqual(self.decisions, ["Drafted answer."])
        self.assertEqual(self.send_client.sent, [(OWNER_ID, "Drafted answer.")])
        proposal = self.outbox.load(self.outbox.list_ids()[0])
        self.assertEqual(proposal.status, SENT)

    def test_rejected_turn_sends_nothing(self):
        loop = self.loop([[owner_update(2)]], approve=False)
        _, turns = loop.run_cycle(0)
        self.assertEqual(turns[0].decision, "rejected")
        self.assertFalse(turns[0].sent)
        self.assertEqual(self.send_client.sent, [])
        proposal = self.outbox.load(self.outbox.list_ids()[0])
        self.assertEqual(proposal.status, REJECTED)

    def test_standing_approval_skips_prompt_but_keeps_pipeline(self):
        loop = self.loop([[owner_update(3)]], standing=True)
        _, turns = loop.run_cycle(0)
        self.assertTrue(turns[0].sent)
        self.assertEqual(self.decisions, [])
        proposal = self.outbox.load(self.outbox.list_ids()[0])
        self.assertEqual(proposal.status, SENT)

    def test_intruder_messages_never_reach_the_model_or_sender(self):
        loop = self.loop([[intruder_update(4)]])
        offset, turns = loop.run_cycle(0)
        self.assertEqual(offset, 5)
        self.assertEqual(turns, [])
        self.assertEqual(self.outbox.list_ids(), [])
        self.assertEqual(self.send_client.sent, [])

    def test_failed_draft_survives_and_sends_nothing(self):
        class FailingAdapter(ScriptedAnthropicAdapter):
            def invoke(self, request, credential):
                from agent_os.shadow_runtime import (
                    ProviderCallError,
                )
                from agent_os.routing import ProviderOutcome

                raise ProviderCallError(
                    ProviderOutcome.INVALID_RESPONSE, "provider_stop_max_tokens"
                )

        loop = self.loop(
            [[owner_update(30), owner_update(31)]], adapter=FailingAdapter()
        )
        offset, turns = loop.run_cycle(0)
        self.assertEqual(offset, 32)
        self.assertEqual(len(turns), 2)
        for turn in turns:
            self.assertFalse(turn.drafted)
            self.assertFalse(turn.sent)
        self.assertEqual(self.send_client.sent, [])

    def test_typing_notifier_fires_per_message_and_failure_is_tolerated(self):
        loop = self.loop([[owner_update(40)]])
        loop.run_cycle(0)
        self.assertEqual(self.typing_calls, 1)
        broken = self.loop([[owner_update(41)]])
        broken.typing_notifier = lambda: (_ for _ in ()).throw(RuntimeError())
        _, turns = broken.run_cycle(0)
        self.assertTrue(turns[0].sent)

    def test_replayed_message_is_answered_once(self):
        loop = self.loop([[owner_update(5)], [owner_update(5)]])
        offset, first = loop.run_cycle(0)
        _, second = loop.run_cycle(offset)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(self.send_client.sent), 1)


if __name__ == "__main__":
    unittest.main()
