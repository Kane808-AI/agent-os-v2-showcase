"""Provider adapter that runs model calls through the Claude Code CLI.

The owner's Claude subscription covers CLI usage, so routing shadow calls
through ``claude -p`` removes per-token API billing while keeping every other
layer unchanged: the router still selects a versioned catalog entry, the
shadow runtime still assembles prompts, checks scope, validates structured
output, and records attempts. This adapter is the transport only.

Boundaries preserved:

- The subprocess runs with all workspace tools denied and only the CLI's
  structured-output mechanism allowed, so the model call can read and write
  nothing on this machine. When the routed template carries the
  owner-approved ``web_access`` grant, read-only WebSearch/WebFetch join the
  allowlist for that call only; shell, filesystem, and delegation tools stay
  denied unconditionally, and fetched web content remains untrusted data
  whose only effect is the schema-validated proposal output.
- The user prompt travels over stdin, never argv, so untrusted content does
  not appear in the process list.
- Subscription authentication belongs to the CLI's own login; the kernel
  credential for this provider is a non-secret marker resolved through the
  same tenant-scoped binding path as any other credential.
- Failures map to the same fixed, content-free error codes the HTTP adapter
  uses; CLI output never leaks into an exception message.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
from typing import Any, Mapping, Protocol

from .routing import ProviderOutcome
from .shadow_runtime import (
    ProviderCallError,
    ProviderRequest,
    ProviderResponse,
    _native_model_ref,
)

CLI_PROVIDER_ID = "anthropic-cli"
CLI_TIMEOUT_SECONDS = 180.0
# Web calls spend several real network round-trips inside their turn budget;
# 180s was sized for sealed 3-turn calls. 240s keeps a web call inside the
# 300s work lease with claim/draft overhead to spare.
CLI_WEB_TIMEOUT_SECONDS = 240.0
# Workspace capability is denied unconditionally: no request flag can grant
# shell, filesystem, or delegation access to the subprocess.
_ALWAYS_DENIED_TOOLS = (
    "Bash,Read,Write,Edit,Glob,Grep,Task,NotebookEdit,TodoWrite"
)
# Read-only web retrieval is denied by default and moved to the allowlist
# only when the routed template carries the owner-approved web_access grant
# (research executor only, decision of 2026-08-04).
_WEB_TOOLS = "WebSearch,WebFetch"
# A sealed structured-output call needs almost no agentic turns; a web call
# needs enough for several search/fetch rounds before the final output.
_SEALED_MAX_TURNS = "3"
_WEB_MAX_TURNS = "8"


@dataclass(frozen=True, slots=True)
class CompletedCall:
    """The subset of subprocess output the adapter interprets."""

    exit_code: int
    stdout: str


class SubprocessRunner(Protocol):
    def run(
        self,
        arguments: list[str],
        *,
        stdin_text: str,
        timeout_seconds: float,
    ) -> CompletedCall:
        """Execute the CLI once and capture its stdout."""


class DefaultSubprocessRunner:
    """Run the CLI as a real subprocess with output captured."""

    def run(
        self,
        arguments: list[str],
        *,
        stdin_text: str,
        timeout_seconds: float,
    ) -> CompletedCall:
        completed = subprocess.run(
            arguments,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return CompletedCall(
            exit_code=completed.returncode, stdout=completed.stdout
        )


def _usage_total(usage: Mapping[str, Any], *names: str) -> int:
    total = 0
    for name in names:
        value = usage.get(name, 0)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            total += value
    return total


class ClaudeCLIAdapter:
    """Invoke one structured-output model call via ``claude -p``."""

    provider_id = CLI_PROVIDER_ID

    def __init__(
        self,
        *,
        claude_bin: str = "claude",
        runner: SubprocessRunner | None = None,
        timeout_seconds: float = CLI_TIMEOUT_SECONDS,
    ) -> None:
        if not claude_bin:
            raise ValueError("claude_bin must be a command name or path")
        self.claude_bin = claude_bin
        self.runner = runner or DefaultSubprocessRunner()
        self.timeout_seconds = timeout_seconds

    def invoke(self, request: ProviderRequest, credential: str) -> ProviderResponse:
        # The credential is a tenant-scoped marker; subscription auth lives
        # in the CLI's own login state and no secret is forwarded here.
        if request.web_access:
            allowed_tools = "StructuredOutput," + _WEB_TOOLS
            denied_tools = _ALWAYS_DENIED_TOOLS
            max_turns = _WEB_MAX_TURNS
            timeout_seconds = max(self.timeout_seconds, CLI_WEB_TIMEOUT_SECONDS)
        else:
            allowed_tools = "StructuredOutput"
            denied_tools = _ALWAYS_DENIED_TOOLS + "," + _WEB_TOOLS
            max_turns = _SEALED_MAX_TURNS
            timeout_seconds = self.timeout_seconds
        arguments = [
            self.claude_bin,
            "-p",
            "--output-format", "json",
            "--model", _native_model_ref(
                self.provider_id, request.provider_model_ref
            ),
            "--system-prompt", request.system_prompt,
            "--json-schema", json.dumps(request.output_schema, sort_keys=True),
            "--allowedTools", allowed_tools,
            "--disallowedTools", denied_tools,
            "--max-turns", max_turns,
        ]
        try:
            completed = self.runner.run(
                arguments,
                stdin_text=request.user_prompt,
                timeout_seconds=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise ProviderCallError(
                ProviderOutcome.TIMEOUT, "cli_timeout"
            ) from error
        except OSError as error:
            raise ProviderCallError(
                ProviderOutcome.SERVER_ERROR, "cli_not_executable"
            ) from error
        if completed.exit_code != 0:
            raise ProviderCallError(
                ProviderOutcome.SERVER_ERROR,
                f"cli_exit_{completed.exit_code}",
            )
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise ProviderCallError(
                ProviderOutcome.INVALID_RESPONSE, "cli_envelope_not_json"
            ) from error
        if not isinstance(envelope, Mapping):
            raise ProviderCallError(
                ProviderOutcome.INVALID_RESPONSE, "cli_envelope_not_object"
            )
        usage = envelope.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        input_tokens = _usage_total(
            usage,
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
        output_tokens = _usage_total(usage, "output_tokens")
        if envelope.get("is_error") or envelope.get("subtype") != "success":
            raise ProviderCallError(
                ProviderOutcome.SERVER_ERROR,
                f"cli_result_{envelope.get('subtype') or 'unknown'}"[:64],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        result = envelope.get("result")
        if not isinstance(result, str) or not result.strip():
            raise ProviderCallError(
                ProviderOutcome.INVALID_RESPONSE,
                "empty_provider_output",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return ProviderResponse(
            output_text=result,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_id=str(envelope.get("session_id") or "") or None,
        )
