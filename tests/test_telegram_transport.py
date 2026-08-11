from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os.cli import build_parser, seed_channel_scope  # noqa: E402
from agent_os.runtime import AgentRuntime, RunStatus  # noqa: E402
from agent_os.storage import SQLiteStore  # noqa: E402
from agent_os.telegram_inbound import (  # noqa: E402
    OwnerChannelBinding,
    TelegramInboundAdapter,
)
from agent_os.telegram_transport import (  # noqa: E402
    PollSummary,
    TelegramInboundListener,
    TelegramTransportError,
    UrllibTelegramClient,
    load_bot_token,
)

OWNER_ID = 700_123
FAKE_TOKEN = "1000000000:" + "A" * 35


def owner_update(update_id: int, text: str = "status please") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 1000,
            "date": 1_785_700_000 + update_id,
            "from": {"id": OWNER_ID, "is_bot": False},
            "chat": {"id": OWNER_ID, "type": "private"},
            "text": text,
        },
    }


def intruder_update(update_id: int) -> dict:
    update = owner_update(update_id)
    update["message"]["from"]["id"] = OWNER_ID + 1
    return update


class FakeClient:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = []

    def get_updates(self, *, offset, timeout_seconds):
        self.calls.append(offset)
        if self.batches:
            return self.batches.pop(0)
        return []


class TokenLoadingTests(unittest.TestCase):
    def write_env(self, content: str) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        env_path = Path(tempdir.name) / "telegram.env"
        env_path.write_text(content)
        return env_path

    def test_valid_token_loads(self):
        env_path = self.write_env(f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\n")
        self.assertEqual(load_bot_token(env_path), FAKE_TOKEN)

    def test_missing_file_and_key_and_bad_format_raise_without_content(self):
        cases = (
            self.write_env(""),
            self.write_env("OTHER_KEY=value\n"),
            self.write_env("TELEGRAM_BOT_TOKEN=not-a-real-token\n"),
        )
        for env_path in cases:
            with self.assertRaises(TelegramTransportError) as raised:
                load_bot_token(env_path)
            self.assertNotIn("not-a-real-token", str(raised.exception))
        with self.assertRaises(TelegramTransportError):
            load_bot_token(Path("/nonexistent/telegram.env"))

    def test_client_repr_redacts_token(self):
        client = UrllibTelegramClient(FAKE_TOKEN)
        self.assertNotIn(FAKE_TOKEN, repr(client))
        self.assertIn("redacted", repr(client))


class ListenerTests(unittest.TestCase):
    def setUp(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.store = SQLiteStore(Path(tempdir.name) / "listener.db")
        self.store.initialize()
        parser = build_parser()
        self.args = parser.parse_args(
            [
                "telegram-listen",
                "--token-env", "/dev/null",
                "--bot-ref", "agentos-atlas",
                "--owner-user-id", str(OWNER_ID),
                "--tenant-id", "tenant-local",
                "--business-id", "business-local",
            ]
        )
        seed_channel_scope(self.store, self.args)
        self.adapter = TelegramInboundAdapter(
            OwnerChannelBinding(
                bot_ref="agentos-atlas",
                owner_user_id=OWNER_ID,
                tenant_id="tenant-local",
                business_id="business-local",
                actor_id="channel-telegram-inbound",
            )
        )
        self.runtime = AgentRuntime(self.store, worker_id="telegram-test")

    def listener(self, batches) -> tuple[TelegramInboundListener, FakeClient]:
        client = FakeClient(batches)
        return (
            TelegramInboundListener(
                adapter=self.adapter,
                runtime=self.runtime,
                client=client,
                poll_timeout_seconds=1,
            ),
            client,
        )

    def test_owner_message_reaches_simulated_triage_run(self):
        listener, _ = self.listener([[owner_update(1)]])
        summary = listener.poll_once(0)
        self.assertEqual(summary, PollSummary(1, 1, 0, 0, 2))
        run = self.store.get_run_for_event("telegram-agentos-atlas-update-1")
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], RunStatus.SIMULATED.value)

    def test_intruder_updates_are_acknowledged_but_never_processed(self):
        listener, _ = self.listener([[intruder_update(7), owner_update(8)]])
        summary = listener.poll_once(0)
        self.assertEqual(summary.received, 2)
        self.assertEqual(summary.accepted, 1)
        self.assertEqual(summary.rejected, 1)
        self.assertEqual(summary.next_offset, 9)
        self.assertIsNone(
            self.store.get_run_for_event("telegram-agentos-atlas-update-7")
        )

    def test_replayed_update_is_a_duplicate_not_a_second_run(self):
        listener, _ = self.listener([[owner_update(3)], [owner_update(3)]])
        first = listener.poll_once(0)
        second = listener.poll_once(0)
        self.assertEqual(first.accepted, 1)
        self.assertEqual(second.duplicates, 1)
        self.assertEqual(second.accepted, 0)

    def test_update_without_update_id_is_a_transport_error(self):
        listener, _ = self.listener([[{"message": {}}]])
        with self.assertRaises(TelegramTransportError):
            listener.poll_once(0)

    def test_run_advances_offset_across_cycles(self):
        listener, client = self.listener(
            [[owner_update(10)], [owner_update(11)]]
        )
        final_offset = listener.run(max_cycles=3, idle_sleep_seconds=0)
        self.assertEqual(final_offset, 12)
        self.assertEqual(client.calls, [0, 11, 12])

    def test_summary_contains_no_message_content(self):
        listener, _ = self.listener(
            [[owner_update(20, text="the secret launch codes")]]
        )
        summary = listener.poll_once(0)
        self.assertNotIn("secret", repr(summary))


if __name__ == "__main__":
    unittest.main()
