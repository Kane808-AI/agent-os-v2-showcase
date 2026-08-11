from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os.contracts import Business, Tenant  # noqa: E402
from agent_os.dashboard import render_dashboard  # noqa: E402
from agent_os.routing import (  # noqa: E402
    DataClass,
    ModelCatalogEntry,
    ModelRouter,
    ProviderOutcome,
    ReasoningTier,
    RouteRequest,
)
from agent_os.shadow_runtime import (  # noqa: E402
    AnthropicMessagesAdapter,
    CanaryCase,
    CredentialBinding,
    CredentialResolutionError,
    EvaluationFixture,
    HTTPResponse,
    JSONSchemaValidator,
    OpenAIResponsesAdapter,
    PromptContext,
    PromptControlError,
    PromptTemplate,
    ProviderCallError,
    ProviderResponse,
    ShadowEvaluationReplay,
    ShadowModelRuntime,
    ShadowPrompt,
    ShadowRuntimeError,
    StructuredOutputError,
)
from agent_os.storage import SQLiteStore  # noqa: E402


PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "proposal": {"type": "string", "minLength": 1},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
    },
    "required": ["proposal", "confidence"],
    "additionalProperties": False,
}


class RecordingResolver:
    def __init__(self, secret: str = "super-secret") -> None:
        self.secret = secret
        self.bindings: list[CredentialBinding] = []

    def resolve(self, binding: CredentialBinding) -> str:
        self.bindings.append(binding)
        if not self.secret:
            raise CredentialResolutionError("credential_not_available")
        return self.secret


class RecordingAdapter:
    def __init__(
        self,
        provider_id: str,
        *,
        output: str = '{"proposal":"observe only","confidence":90}',
    ) -> None:
        self.provider_id = provider_id
        self.output = output
        self.requests = []
        self.credentials = []
        self.error: Exception | None = None

    def invoke(self, request, credential):
        self.requests.append(request)
        self.credentials.append(credential)
        if self.error is not None:
            raise self.error
        return ProviderResponse(
            output_text=self.output,
            input_tokens=80,
            output_tokens=20,
            request_id=f"{self.provider_id}-request",
        )


class CrashAdapter(RecordingAdapter):
    def invoke(self, request, credential):
        self.requests.append(request)
        raise KeyboardInterrupt("simulated process loss")


class RecordingTransport:
    def __init__(self, response: HTTPResponse) -> None:
        self.response = response
        self.calls = []

    def post(self, url, *, headers, payload, timeout_seconds):
        self.calls.append((url, headers, payload, timeout_seconds))
        return self.response


class ShadowRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "shadow.db"
        self.store = SQLiteStore(self.database)
        self.store.initialize()
        self.now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
        self.store.upsert_tenant(Tenant("tenant-1", "Tenant One"))
        self.store.upsert_business(
            Business(
                business_id="business-1",
                tenant_id="tenant-1",
                legal_name="Business One LLC",
                display_name="Business One",
                base_currency="USD",
                timezone_name="UTC",
            )
        )
        self.router = ModelRouter(self.store)
        self.entries = (
            ModelCatalogEntry(
                model_id="openai-standard",
                provider_id="openai",
                provider_model_ref="openai/gpt-5-2026-07-01",
                reasoning_tier=ReasoningTier.STANDARD,
                tool_use=True,
                structured_output=True,
                modalities=frozenset({"text"}),
                context_window_tokens=16_000,
                allowed_data_classes=frozenset(DataClass),
                input_micros_per_million=100_000,
                output_micros_per_million=200_000,
                quality_score=90,
                evaluation_version="shadow-eval-1.0.0",
            ),
            ModelCatalogEntry(
                model_id="anthropic-standard",
                provider_id="anthropic",
                provider_model_ref="anthropic/claude-sonnet-4-6-20260701",
                reasoning_tier=ReasoningTier.STANDARD,
                tool_use=True,
                structured_output=True,
                modalities=frozenset({"text"}),
                context_window_tokens=16_000,
                allowed_data_classes=frozenset(DataClass),
                input_micros_per_million=200_000,
                output_micros_per_million=400_000,
                quality_score=95,
                evaluation_version="shadow-eval-1.0.0",
            ),
        )
        self.router.register_catalog(
            "1.0.0", self.entries, created_at=self.now - timedelta(minutes=2)
        )
        self.router.activate_catalog(
            "1.0.0", activation_id="active-shadow", activated_at=self.now - timedelta(minutes=1)
        )
        for provider in ("openai", "anthropic"):
            credential_id = f"credential-{provider}"
            self.router.bind_credential(
                credential_id=credential_id,
                tenant_id="tenant-1",
                business_id="business-1",
                provider_id=provider,
                credential_ref=f"env://{provider.upper()}_TEST_KEY",
                created_at=self.now - timedelta(seconds=30),
            )
            self.router.revise_provider_policy(
                policy_revision_id=f"policy-{provider}",
                tenant_id="tenant-1",
                business_id="business-1",
                provider_id=provider,
                credential_id=credential_id,
                enabled=True,
                allowed_data_classes=frozenset(DataClass),
                monthly_budget_micros=100_000,
                created_at=self.now - timedelta(seconds=20),
            )
        self.template = PromptTemplate(
            template_id="shadow-proposal",
            version="1.0.0",
            system_instruction="Create one bounded recommendation.",
        )
        self.resolver = RecordingResolver()
        self.openai = RecordingAdapter("openai")
        self.anthropic = RecordingAdapter("anthropic")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def route(self, request_id="request-1", *, data_class=DataClass.INTERNAL):
        return self.router.route(
            RouteRequest(
                request_id=request_id,
                tenant_id="tenant-1",
                business_id="business-1",
                reasoning_tier=ReasoningTier.STANDARD,
                data_class=data_class,
                required_modalities=frozenset({"text"}),
                requires_tool_use=False,
                requires_structured_output=True,
                required_context_tokens=100,
                estimated_input_tokens=1000,
                estimated_output_tokens=200,
            ),
            now=self.now,
        )

    def prompt(self, *, context=(), max_output_tokens=100):
        return ShadowPrompt(
            template=self.template,
            user_input="Recommend a reversible experiment.",
            output_schema=PROPOSAL_SCHEMA,
            max_output_tokens=max_output_tokens,
            context=tuple(context),
        )

    def runtime(self, *, adapters=None, resolver=None):
        return ShadowModelRuntime(
            self.store,
            credential_resolver=resolver or self.resolver,
            adapters=adapters or (self.openai, self.anthropic),
        )

    def test_selected_route_executes_once_and_persists_only_safe_evidence(self):
        decision = self.route()
        result = self.runtime().execute(
            decision_id=decision.decision_id,
            prompt=self.prompt(
                context=(
                    PromptContext(
                        source_ref="fixture:metrics",
                        tenant_id="tenant-1",
                        business_id="business-1",
                        data_class=DataClass.INTERNAL,
                        content="Conversion rate is 2 percent.",
                    ),
                )
            ),
            now=self.now,
        )
        self.assertEqual(result.model_id, decision.model_id)
        self.assertEqual(result.parsed_output["confidence"], 90)
        self.assertEqual(len(self.openai.requests), 1)
        self.assertEqual(len(self.anthropic.requests), 0)
        self.assertEqual(self.resolver.bindings[0].credential_id, decision.credential_id)
        self.assertIn("proposal-only shadow mode", self.openai.requests[0].system_prompt)
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(snapshot["counts"]["shadow_model_attempts"], 1)
        self.assertEqual(snapshot["counts"]["shadow_model_outcomes"], 1)
        self.assertEqual(snapshot["model_usage"][0]["outcome"], "success")
        self.assertEqual(snapshot["shadow_model_attempts"][0]["status"], "succeeded")
        durable_bytes = self.database.read_bytes()
        self.assertNotIn(b"super-secret", durable_bytes)
        self.assertNotIn(b"Conversion rate is 2 percent", durable_bytes)
        self.assertNotIn(b"observe only", durable_bytes)
        self.assertTrue(self.store.schema_status()["migration_valid"])

    def test_invalid_output_records_failure_before_explicit_fallback(self):
        decision = self.route()
        self.openai.output = '{"proposal":"missing confidence"}'
        with self.assertRaisesRegex(ShadowRuntimeError, "failed closed"):
            self.runtime().execute(
                decision_id=decision.decision_id,
                prompt=self.prompt(),
                now=self.now,
            )
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(snapshot["model_usage"][0]["outcome"], "invalid_response")
        self.assertEqual(snapshot["shadow_model_attempts"][0]["status"], "failed")
        self.assertEqual(len(self.anthropic.requests), 0)
        fallback = self.router.route_fallback(
            decision.decision_id,
            request_id="request-fallback",
            now=self.now + timedelta(seconds=1),
        )
        self.assertEqual(fallback.provider_id, "anthropic")
        self.assertEqual(fallback.previous_decision_id, decision.decision_id)

    def test_credential_failure_is_auth_telemetry_and_never_calls_adapter(self):
        decision = self.route()
        resolver = RecordingResolver(secret="")
        with self.assertRaises(ShadowRuntimeError):
            self.runtime(resolver=resolver).execute(
                decision_id=decision.decision_id,
                prompt=self.prompt(),
                now=self.now,
            )
        self.assertEqual(len(self.openai.requests), 0)
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(snapshot["model_usage"][0]["outcome"], "auth_error")
        self.assertEqual(snapshot["model_circuits"][0]["circuit_state"], "open")

    def test_context_scope_sensitivity_and_token_controls_reject_before_claim(self):
        decision = self.route(data_class=DataClass.INTERNAL)
        bad_scope = PromptContext(
            source_ref="fixture:other",
            tenant_id="tenant-1",
            business_id="business-2",
            data_class=DataClass.INTERNAL,
            content="other business data",
        )
        with self.assertRaisesRegex(PromptControlError, "crosses"):
            self.runtime().execute(
                decision_id=decision.decision_id,
                prompt=self.prompt(context=(bad_scope,)),
                now=self.now,
            )
        bad_class = PromptContext(
            source_ref="fixture:secret",
            tenant_id="tenant-1",
            business_id="business-1",
            data_class=DataClass.CONFIDENTIAL,
            content="sensitive",
        )
        with self.assertRaisesRegex(PromptControlError, "sensitivity"):
            self.runtime().execute(
                decision_id=decision.decision_id,
                prompt=self.prompt(context=(bad_class,)),
                now=self.now,
            )
        with self.assertRaisesRegex(PromptControlError, "output limit"):
            self.runtime().execute(
                decision_id=decision.decision_id,
                prompt=self.prompt(max_output_tokens=201),
                now=self.now,
            )
        self.assertEqual(self.store.dashboard_snapshot()["counts"]["shadow_model_attempts"], 0)

    def test_decision_claim_prevents_duplicate_provider_call(self):
        decision = self.route()
        runtime = self.runtime()
        runtime.execute(decision_id=decision.decision_id, prompt=self.prompt(), now=self.now)
        with self.assertRaisesRegex(ShadowRuntimeError, "already claimed"):
            runtime.execute(decision_id=decision.decision_id, prompt=self.prompt(), now=self.now)
        self.assertEqual(len(self.openai.requests), 1)
        with self.assertRaisesRegex(ShadowRuntimeError, "already has"):
            runtime.isolate_uncertain_attempt(decision_id=decision.decision_id, now=self.now)

    def test_abandoned_claim_is_isolated_without_second_call(self):
        decision = self.route()
        crash = CrashAdapter("openai")
        runtime = self.runtime(adapters=(crash, self.anthropic))
        with self.assertRaises(KeyboardInterrupt):
            runtime.execute(decision_id=decision.decision_id, prompt=self.prompt(), now=self.now)
        self.assertEqual(len(crash.requests), 1)
        with self.assertRaisesRegex(ShadowRuntimeError, "already claimed"):
            runtime.execute(decision_id=decision.decision_id, prompt=self.prompt(), now=self.now)
        runtime.isolate_uncertain_attempt(
            decision_id=decision.decision_id,
            now=self.now + timedelta(seconds=1),
        )
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(snapshot["model_usage"][0]["outcome"], "invalid_response")
        self.assertEqual(snapshot["shadow_model_attempts"][0]["status"], "isolated")
        self.assertEqual(len(crash.requests), 1)

    def test_canary_requires_public_synthetic_context_free_route(self):
        canary = CanaryCase(
            canary_id="json-health",
            template=self.template,
            prompt="Return a bounded health proposal.",
            output_schema=PROPOSAL_SCHEMA,
            max_output_tokens=100,
        )
        internal = self.route("internal-canary")
        with self.assertRaisesRegex(PromptControlError, "public-data"):
            self.runtime().run_canary(
                decision_id=internal.decision_id, canary=canary, now=self.now
            )
        public = self.route("public-canary", data_class=DataClass.PUBLIC)
        result = self.runtime().run_canary(
            decision_id=public.decision_id, canary=canary, now=self.now
        )
        self.assertEqual(result.decision_id, public.decision_id)
        self.assertEqual(self.store.dashboard_snapshot()["shadow_model_attempts"][0]["attempt_kind"], "canary")

    def test_canary_refuses_web_granted_templates(self):
        from dataclasses import replace

        canary = CanaryCase(
            canary_id="web-health",
            template=replace(self.template, web_access=True),
            prompt="Return a bounded health proposal.",
            output_schema=PROPOSAL_SCHEMA,
            max_output_tokens=100,
        )
        public = self.route("public-web-canary", data_class=DataClass.PUBLIC)
        with self.assertRaisesRegex(PromptControlError, "web-granted"):
            self.runtime().run_canary(
                decision_id=public.decision_id, canary=canary, now=self.now
            )

    def test_evaluation_replay_is_offline_deterministic_and_durable(self):
        fixtures = (
            EvaluationFixture(
                case_id="valid",
                output_text='{ "confidence": 90, "proposal": "observe" }',
                output_schema=PROPOSAL_SCHEMA,
                expected_valid=True,
            ),
            EvaluationFixture(
                case_id="invalid",
                output_text='{"proposal":"observe"}',
                output_schema=PROPOSAL_SCHEMA,
                expected_valid=False,
            ),
        )
        replay = ShadowEvaluationReplay(self.store)
        first = replay.replay(
            suite_id="shadow-contract", suite_version="1.0.0", fixtures=fixtures, now=self.now
        )
        second = replay.replay(
            suite_id="shadow-contract", suite_version="1.0.0", fixtures=fixtures, now=self.now
        )
        self.assertTrue(first.passed)
        self.assertEqual(first.replay_id, second.replay_id)
        self.assertEqual(len(self.openai.requests), 0)
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(snapshot["counts"]["model_evaluation_replays"], 1)
        self.assertIn("Model evaluation replay", render_dashboard(snapshot))

    def test_validator_rejects_extra_fields_and_unsupported_schema(self):
        validator = JSONSchemaValidator()
        with self.assertRaises(StructuredOutputError):
            validator.parse(
                '{"proposal":"ok","confidence":90,"action":"publish"}',
                PROPOSAL_SCHEMA,
            )
        with self.assertRaisesRegex(PromptControlError, "unsupported keywords"):
            validator.validate_schema({"type": "string", "$ref": "remote"})
        with self.assertRaisesRegex(StructuredOutputError, "duplicate"):
            validator.parse(
                '{"proposal":"first","proposal":"second","confidence":90}',
                PROPOSAL_SCHEMA,
            )
        with self.assertRaisesRegex(StructuredOutputError, "non_standard"):
            validator.parse(
                '{"proposal":"ok","confidence":NaN}', PROPOSAL_SCHEMA
            )

    def test_openai_adapter_uses_exact_model_structured_output_and_no_tools(self):
        transport = RecordingTransport(
            HTTPResponse(
                status=200,
                body={
                    "id": "resp-1",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": '{"proposal":"ok","confidence":80}'}
                            ],
                        }
                    ],
                    "usage": {"input_tokens": 12, "output_tokens": 8},
                },
            )
        )
        adapter = OpenAIResponsesAdapter(transport)
        response = adapter.invoke(
            self._provider_request("openai/gpt-5-2026-07-01"), "secret"
        )
        payload = transport.calls[0][2]
        self.assertEqual(payload["model"], "gpt-5-2026-07-01")
        self.assertEqual(payload["tools"], [])
        self.assertFalse(payload["store"])
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertEqual(response.input_tokens, 12)

    def test_anthropic_adapter_uses_structured_output_without_tools(self):
        transport = RecordingTransport(
            HTTPResponse(
                status=200,
                body={
                    "id": "msg-1",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": '{"proposal":"ok","confidence":80}'}],
                    "usage": {"input_tokens": 11, "output_tokens": 7},
                },
            )
        )
        adapter = AnthropicMessagesAdapter(transport)
        adapter.invoke(
            self._provider_request("anthropic/claude-sonnet-4-6-20260701"), "secret"
        )
        payload = transport.calls[0][2]
        self.assertEqual(payload["model"], "claude-sonnet-4-6-20260701")
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["output_config"]["format"]["type"], "json_schema")

    def test_anthropic_adapter_tolerates_thinking_but_rejects_tool_use(self):
        thinking_transport = RecordingTransport(
            HTTPResponse(
                status=200,
                body={
                    "id": "msg-2",
                    "stop_reason": "end_turn",
                    "content": [
                        {"type": "thinking", "thinking": "planning the reply"},
                        {"type": "text", "text": '{"proposal":"ok","confidence":80}'},
                    ],
                    "usage": {"input_tokens": 11, "output_tokens": 7},
                },
            )
        )
        response = AnthropicMessagesAdapter(thinking_transport).invoke(
            self._provider_request("anthropic/claude-sonnet-4-6-20260701"), "secret"
        )
        self.assertEqual(
            response.output_text, '{"proposal":"ok","confidence":80}'
        )
        tool_transport = RecordingTransport(
            HTTPResponse(
                status=200,
                body={
                    "id": "msg-3",
                    "stop_reason": "end_turn",
                    "content": [
                        {"type": "text", "text": '{"proposal":"ok","confidence":80}'},
                        {"type": "tool_use", "name": "run_shell", "input": {}},
                    ],
                    "usage": {"input_tokens": 11, "output_tokens": 7},
                },
            )
        )
        with self.assertRaises(ProviderCallError) as caught:
            AnthropicMessagesAdapter(tool_transport).invoke(
                self._provider_request("anthropic/claude-sonnet-4-6-20260701"),
                "secret",
            )
        self.assertEqual(caught.exception.code, "provider_tool_or_unknown_output")

    def test_http_adapters_refuse_web_access_requests_without_calling_out(self):
        from dataclasses import replace

        for adapter, model_ref in (
            (OpenAIResponsesAdapter, "openai/gpt-5-2026-07-01"),
            (AnthropicMessagesAdapter, "anthropic/claude-sonnet-4-6-20260701"),
        ):
            with self.subTest(adapter=adapter.__name__):
                transport = RecordingTransport(
                    HTTPResponse(status=200, body={})
                )
                web_request = replace(
                    self._provider_request(model_ref), web_access=True
                )
                with self.assertRaises(ProviderCallError) as caught:
                    adapter(transport).invoke(web_request, "secret")
                self.assertEqual(caught.exception.code, "web_access_unsupported")
                self.assertEqual(transport.calls, [])

    def test_provider_http_failure_mapping_is_explicit(self):
        transport = RecordingTransport(HTTPResponse(status=429, body={"error": {}}))
        with self.assertRaises(ProviderCallError) as caught:
            OpenAIResponsesAdapter(transport).invoke(
                self._provider_request("openai/gpt-5-2026-07-01"), "secret"
            )
        self.assertEqual(caught.exception.outcome, ProviderOutcome.RATE_LIMITED)

    def test_backup_preserves_shadow_evidence_and_doctor_detects_forgery(self):
        decision = self.route()
        self.runtime().execute(decision_id=decision.decision_id, prompt=self.prompt(), now=self.now)
        backup_path = self.store.create_backup(Path(self.tempdir.name) / "shadow-backup.db")
        backup = SQLiteStore(backup_path)
        self.assertTrue(backup.schema_status()["migration_valid"])
        self.assertEqual(backup.dashboard_snapshot()["counts"]["shadow_model_attempts"], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DROP TRIGGER prevent_shadow_model_outcomes_update")
            connection.execute("UPDATE shadow_model_outcomes SET model_id = 'forged'")
            connection.commit()
        status = self.store.schema_status()
        self.assertFalse(status["migration_valid"])
        self.assertTrue(any("shadow outcome" in error or "schema object" in error for error in status["migration_errors"]))

    def _provider_request(self, model_ref):
        from agent_os.shadow_runtime import ProviderRequest

        return ProviderRequest(
            provider_model_ref=model_ref,
            system_prompt="Shadow only.",
            user_prompt="{}",
            output_schema=PROPOSAL_SCHEMA,
            schema_name="proposal",
            max_output_tokens=100,
        )


if __name__ == "__main__":
    unittest.main()
