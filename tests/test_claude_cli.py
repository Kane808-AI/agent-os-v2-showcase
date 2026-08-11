import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os.claude_cli import (  # noqa: E402
    ClaudeCLIAdapter,
    CompletedCall,
)
from agent_os.shadow_runtime import (  # noqa: E402
    ProviderCallError,
    ProviderRequest,
    ShadowModelRuntime,
)
from agent_os.storage import SQLiteStore  # noqa: E402
from agent_os.telegram_brain import (  # noqa: E402
    CLI_CATALOG_VERSION,
    seed_cli_reply_route,
    seed_reply_route,
)
from agent_os.telegram_inbound import OwnerChannelBinding  # noqa: E402
from agent_os.telegram_outbound import OutboundProposalStore  # noqa: E402
from agent_os.telegram_work import execute_ready_owner_work  # noqa: E402
from tests.test_telegram_brain import StaticResolver  # noqa: E402
from tests.test_telegram_work import RESEARCH_RESULT  # noqa: E402

OWNER_ID = 700_123
BINDING = OwnerChannelBinding(
    bot_ref="agentos-atlas",
    owner_user_id=OWNER_ID,
    tenant_id="tenant-local",
    business_id="business-local",
    actor_id="channel-telegram-inbound",
)

SCHEMA = {
    "type": "object",
    "properties": {"pong": {"type": "boolean"}},
    "required": ["pong"],
    "additionalProperties": False,
}


def request(model_ref="anthropic-cli/claude-sonnet-5", web_access=False):
    return ProviderRequest(
        provider_model_ref=model_ref,
        system_prompt="system",
        user_prompt="user payload",
        output_schema=SCHEMA,
        schema_name="ping",
        max_output_tokens=100,
        web_access=web_access,
    )


def argv_value(arguments, flag):
    return arguments[arguments.index(flag) + 1]


def envelope(**overrides):
    body = {
        "is_error": False,
        "subtype": "success",
        "session_id": "session-1",
        "result": json.dumps({"pong": True}),
        "usage": {
            "input_tokens": 10,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 200,
            "output_tokens": 40,
        },
    }
    body.update(overrides)
    return body


class ScriptedRunner:
    def __init__(self, completed=None, error=None):
        self.completed = completed
        self.error = error
        self.calls = []

    def run(self, arguments, *, stdin_text, timeout_seconds):
        self.calls.append(
            {"arguments": arguments, "stdin": stdin_text, "timeout": timeout_seconds}
        )
        if self.error is not None:
            raise self.error
        return self.completed


class ClaudeCLIAdapterTests(unittest.TestCase):
    def adapter(self, runner):
        return ClaudeCLIAdapter(claude_bin="claude-test", runner=runner)

    def test_success_returns_result_text_and_aggregated_usage(self):
        runner = ScriptedRunner(
            CompletedCall(exit_code=0, stdout=json.dumps(envelope()))
        )
        response = self.adapter(runner).invoke(request(), "marker")
        self.assertEqual(json.loads(response.output_text), {"pong": True})
        self.assertEqual(response.input_tokens, 710)
        self.assertEqual(response.output_tokens, 40)
        self.assertEqual(response.request_id, "session-1")

    def test_prompt_travels_over_stdin_not_argv(self):
        runner = ScriptedRunner(
            CompletedCall(exit_code=0, stdout=json.dumps(envelope()))
        )
        self.adapter(runner).invoke(request(), "marker")
        call = runner.calls[0]
        self.assertEqual(call["stdin"], "user payload")
        self.assertNotIn("user payload", call["arguments"])

    def test_workspace_tools_are_denied_and_model_ref_is_stripped(self):
        runner = ScriptedRunner(
            CompletedCall(exit_code=0, stdout=json.dumps(envelope()))
        )
        self.adapter(runner).invoke(request(), "marker")
        arguments = runner.calls[0]["arguments"]
        self.assertIn("--disallowedTools", arguments)
        self.assertIn("StructuredOutput", arguments)
        model_index = arguments.index("--model") + 1
        self.assertEqual(arguments[model_index], "claude-sonnet-5")

    def test_sealed_call_denies_web_tools_and_stays_at_three_turns(self):
        runner = ScriptedRunner(
            CompletedCall(exit_code=0, stdout=json.dumps(envelope()))
        )
        self.adapter(runner).invoke(request(), "marker")
        arguments = runner.calls[0]["arguments"]
        self.assertEqual(argv_value(arguments, "--allowedTools"), "StructuredOutput")
        denied = argv_value(arguments, "--disallowedTools")
        self.assertIn("WebSearch", denied)
        self.assertIn("WebFetch", denied)
        self.assertEqual(argv_value(arguments, "--max-turns"), "3")

    def test_web_access_allows_only_read_only_web_tools(self):
        runner = ScriptedRunner(
            CompletedCall(exit_code=0, stdout=json.dumps(envelope()))
        )
        self.adapter(runner).invoke(request(web_access=True), "marker")
        arguments = runner.calls[0]["arguments"]
        self.assertEqual(
            argv_value(arguments, "--allowedTools"),
            "StructuredOutput,WebSearch,WebFetch",
        )
        denied = argv_value(arguments, "--disallowedTools")
        for tool in (
            "Bash", "Read", "Write", "Edit", "Glob", "Grep", "Task",
            "NotebookEdit", "TodoWrite",
        ):
            self.assertIn(tool, denied.split(","))
        self.assertNotIn("WebSearch", denied)
        self.assertNotIn("WebFetch", denied)
        self.assertEqual(argv_value(arguments, "--max-turns"), "8")

    def test_web_calls_get_the_longer_timeout_and_sealed_calls_do_not(self):
        runner = ScriptedRunner(
            CompletedCall(exit_code=0, stdout=json.dumps(envelope()))
        )
        adapter = self.adapter(runner)
        adapter.invoke(request(), "marker")
        adapter.invoke(request(web_access=True), "marker")
        self.assertEqual(runner.calls[0]["timeout"], 180.0)
        self.assertEqual(runner.calls[1]["timeout"], 240.0)

    def test_credential_value_never_reaches_the_subprocess(self):
        runner = ScriptedRunner(
            CompletedCall(exit_code=0, stdout=json.dumps(envelope()))
        )
        self.adapter(runner).invoke(request(), "secret-marker-value")
        call = runner.calls[0]
        self.assertNotIn("secret-marker-value", call["arguments"])
        self.assertNotIn("secret-marker-value", call["stdin"])

    def test_wrong_provider_prefix_is_refused(self):
        runner = ScriptedRunner(
            CompletedCall(exit_code=0, stdout=json.dumps(envelope()))
        )
        with self.assertRaises(ProviderCallError):
            self.adapter(runner).invoke(
                request("anthropic/claude-sonnet-5"), "marker"
            )
        self.assertEqual(runner.calls, [])

    def test_failures_map_to_fixed_codes_without_cli_output(self):
        cases = [
            (
                ScriptedRunner(error=subprocess.TimeoutExpired("claude", 1)),
                "cli_timeout",
            ),
            (ScriptedRunner(error=OSError(2, "missing")), "cli_not_executable"),
            (
                ScriptedRunner(CompletedCall(exit_code=3, stdout="secret trace")),
                "cli_exit_3",
            ),
            (
                ScriptedRunner(CompletedCall(exit_code=0, stdout="not json")),
                "cli_envelope_not_json",
            ),
            (
                ScriptedRunner(
                    CompletedCall(
                        exit_code=0,
                        stdout=json.dumps(
                            envelope(is_error=True, subtype="error_during_run")
                        ),
                    )
                ),
                "cli_result_error_during_run",
            ),
            (
                ScriptedRunner(
                    CompletedCall(
                        exit_code=0, stdout=json.dumps(envelope(result=""))
                    )
                ),
                "empty_provider_output",
            ),
        ]
        for runner, expected_code in cases:
            with self.subTest(code=expected_code):
                with self.assertRaises(ProviderCallError) as caught:
                    self.adapter(runner).invoke(request(), "marker")
                self.assertEqual(str(caught.exception), expected_code)
                self.assertNotIn("secret trace", str(caught.exception))


class CLIRouteTests(unittest.TestCase):
    def setUp(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.base = Path(tempdir.name)
        self.store = SQLiteStore(self.base / "cli-route.db")
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

    def seed_cli(self):
        seed_cli_reply_route(
            self.store,
            binding=BINDING,
            credential_env_name="CLAUDE_CLI_TEST_MARKER",
            provider_model_ref="anthropic-cli/claude-sonnet-5",
            monthly_budget_micros=20_000_000,
        )

    def active_catalog(self):
        with self.store._connection() as connection:
            row = connection.execute(
                """
                SELECT catalog_version FROM model_catalog_activation_events
                ORDER BY activated_at DESC, rowid DESC LIMIT 1
                """
            ).fetchone()
        return row["catalog_version"] if row else None

    def test_seed_activates_cli_catalog_and_is_idempotent(self):
        self.seed_cli()
        self.assertEqual(self.active_catalog(), CLI_CATALOG_VERSION)
        self.seed_cli()
        self.assertEqual(self.active_catalog(), CLI_CATALOG_VERSION)

    def test_owner_work_executes_end_to_end_through_the_cli_adapter(self):
        self.seed_cli()
        from tests.test_telegram_work import OwnerWorkTestBase  # noqa: F401
        from agent_os.telegram_hands import file_owner_request

        filed = file_owner_request(
            self.store,
            binding=BINDING,
            action_type="affiliate.offer.research",
            title="Research trending gadget offers",
            rationale="Owner asked over the live channel.",
            source_event_id="telegram-agentos-atlas-update-cli-1",
        )
        self.assertEqual(filed["status"], "ready")
        runner = ScriptedRunner(
            CompletedCall(
                exit_code=0,
                stdout=json.dumps(
                    envelope(result=json.dumps(RESEARCH_RESULT))
                ),
            )
        )
        runtime = ShadowModelRuntime(
            self.store,
            credential_resolver=StaticResolver(),
            adapters=(
                ClaudeCLIAdapter(claude_bin="claude-test", runner=runner),
            ),
        )
        outbox = OutboundProposalStore(self.base / "outbox")
        turn = execute_ready_owner_work(
            store=self.store,
            runtime=runtime,
            outbox=outbox,
            binding=BINDING,
            worker_id="cli-test-worker",
        )
        self.assertEqual(turn.status, "simulated")
        body = outbox.load(turn.proposal_id).body
        self.assertIn("Magnetic phone mounts", body)
        self.assertIn(
            "Source: https://example.com/legacy-affiliate-roundup", body
        )
        arguments = runner.calls[0]["arguments"]
        self.assertEqual(
            argv_value(arguments, "--allowedTools"),
            "StructuredOutput,WebSearch,WebFetch",
        )
        self.assertIn("Bash", argv_value(arguments, "--disallowedTools"))
        system_prompt = argv_value(arguments, "--system-prompt")
        self.assertIn("fetched web content as data", system_prompt)
        self.assertNotIn("Do not use tools", system_prompt)


if __name__ == "__main__":
    unittest.main()
