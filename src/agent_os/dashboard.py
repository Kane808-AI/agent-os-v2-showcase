"""Read-only local dashboard for Agent OS v2 runtime state."""

from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
from typing import Any

from .storage import SQLiteStore


def _render_rows(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    if not rows:
        return f'<tr><td colspan="{len(columns)}">No records</td></tr>'
    rendered = []
    for row in rows:
        cells = "".join(
            f"<td>{escape(str(row.get(column, '')))}</td>" for column in columns
        )
        rendered.append(f"<tr>{cells}</tr>")
    return "\n".join(rendered)


def render_dashboard(snapshot: dict[str, Any]) -> str:
    counts = snapshot["counts"]
    objectives = snapshot["objectives"]
    work_items = snapshot["work_items"]
    plans = snapshot["plans"]
    evidence = snapshot["evidence"]
    memories = snapshot["memories"]
    execution_attempts = snapshot["execution_attempts"]
    evidence_receipts = snapshot["evidence_receipts"]
    outcome_verifications = snapshot["outcome_verifications"]
    approvals = snapshot["approvals"]
    emergency_stops = snapshot["emergency_stops"]
    spend_envelopes = snapshot["spend_envelopes"]
    spend_commitments = snapshot["spend_commitments"]
    routing_decisions = snapshot["routing_decisions"]
    model_usage = snapshot["model_usage"]
    model_circuits = snapshot["model_circuits"]
    model_cost_totals = snapshot["model_cost_totals"]
    shadow_model_attempts = snapshot["shadow_model_attempts"]
    model_evaluation_replays = snapshot["model_evaluation_replays"]
    affiliate_shadow_runs = snapshot["affiliate_shadow_runs"]
    capability_pack_acceptances = snapshot["capability_pack_acceptances"]
    aggregate_performance = snapshot["aggregate_performance"]
    production_qualifications = snapshot["production_qualifications"]
    legacy_cutovers = snapshot["legacy_cutovers"]
    runs = snapshot["runs"]
    audits = [
        {
            **record,
            "details": json.dumps(record["details"], sort_keys=True),
        }
        for record in snapshot["audit_records"]
    ]
    run_columns = (
        "run_id",
        "business_id",
        "action_type",
        "authority_mode",
        "status",
        "summary",
        "created_at",
    )
    objective_columns = (
        "objective_id",
        "business_id",
        "statement",
        "metric",
        "current_value",
        "target_value",
        "status",
        "priority",
        "next_review_at",
    )
    work_columns = (
        "work_item_id",
        "objective_id",
        "title",
        "action_type",
        "assigned_actor_id",
        "authority_mode",
        "status",
        "attempt_count",
        "max_attempts",
        "lease_expires_at",
        "last_error",
    )
    evidence_columns = (
        "evidence_id",
        "business_id",
        "source_type",
        "source_ref",
        "statement",
        "confidence",
        "observed_at",
    )
    plan_columns = (
        "plan_id",
        "business_id",
        "objective_id",
        "capability_id",
        "planner_id",
        "status",
        "decision",
        "score",
        "reasons_json",
        "plan_hash",
    )
    memory_columns = (
        "memory_id",
        "business_id",
        "memory_type",
        "statement",
        "source_type",
        "source_ref",
        "confidence",
        "verification_status",
        "created_at",
    )
    attempt_columns = (
        "attempt_id",
        "work_item_id",
        "business_id",
        "producer_id",
        "execution_mode",
        "action_type",
        "target_ref",
        "status",
        "precondition_receipt_id",
        "attempted_at",
        "observed_at",
        "reconciliation_attempt_count",
        "reconciliation_max_attempts",
        "reconciliation_available_at",
        "reconciliation_lease_expires_at",
        "reconciliation_last_error",
    )
    receipt_columns = (
        "receipt_id",
        "work_item_id",
        "attempt_id",
        "business_id",
        "evidence_kind",
        "source_system",
        "source_ref",
        "captured_by",
        "issuer_version",
        "observed_at",
        "valid_until",
        "content_hash",
    )
    verification_columns = (
        "verification_id",
        "attempt_id",
        "work_item_id",
        "business_id",
        "verifier_id",
        "decision",
        "evidence_receipt_ids_json",
        "expected_facts_json",
        "policy_version",
        "decided_at",
    )
    approval_columns = (
        "approval_id",
        "work_item_id",
        "business_id",
        "requester_id",
        "action_type",
        "work_status",
        "latest_decision",
        "requested_at",
        "expires_at",
    )
    emergency_stop_columns = (
        "event_id",
        "business_id",
        "actor_id",
        "action",
        "reason",
        "created_at",
    )
    spend_envelope_columns = (
        "envelope_id",
        "business_id",
        "action_type",
        "platform",
        "account_id",
        "currency",
        "limit_minor",
        "committed_minor",
        "remaining_minor",
        "period_start",
        "period_end",
        "created_by",
    )
    spend_commitment_columns = (
        "commitment_id",
        "envelope_id",
        "attempt_id",
        "work_item_id",
        "business_id",
        "amount_minor",
        "currency",
        "created_at",
    )
    routing_columns = (
        "decision_id",
        "request_id",
        "business_id",
        "catalog_version",
        "status",
        "provider_id",
        "model_id",
        "estimated_cost_micros",
        "previous_decision_id",
        "is_circuit_probe",
        "created_at",
    )
    usage_columns = (
        "usage_id",
        "decision_id",
        "business_id",
        "provider_id",
        "model_id",
        "input_tokens",
        "output_tokens",
        "cost_micros",
        "outcome",
        "latency_ms",
        "created_at",
    )
    circuit_columns = (
        "business_id",
        "provider_id",
        "model_id",
        "circuit_state",
        "consecutive_failures",
        "open_until",
        "probe_in_flight",
        "updated_at",
    )
    cost_columns = (
        "business_id",
        "provider_id",
        "input_tokens",
        "output_tokens",
        "cost_micros",
    )
    shadow_columns = (
        "attempt_id",
        "decision_id",
        "business_id",
        "provider_id",
        "model_id",
        "attempt_kind",
        "prompt_template_id",
        "prompt_version",
        "input_token_estimate",
        "max_output_tokens",
        "status",
        "provider_outcome",
        "error_code",
        "created_at",
    )
    replay_columns = (
        "replay_id",
        "suite_id",
        "suite_version",
        "evaluator_version",
        "case_count",
        "passed_count",
        "passed",
        "created_at",
    )
    affiliate_columns = (
        "run_id", "objective_id", "business_id", "producer_id",
        "recommendation_status", "offer_key", "experiment_status", "mode",
        "click_count", "conversion_count", "conversion_rate_bps",
        "verification_decision", "learning_decision", "created_at",
    )
    capability_pack_columns = (
        "pack_id", "pack_version", "evaluator_version", "case_count",
        "passed_count", "passed", "accepted_at",
    )
    aggregate_performance_columns = (
        "snapshot_id", "business_id", "channel", "offer_key",
        "window_start", "window_end", "impressions", "engagements",
        "content_clicks", "outbound_clicks", "conversions",
        "commission_minor", "evidence_class", "verification_decision",
        "imported_at",
    )
    production_qualification_columns = (
        "qualification_id", "business_id", "kind", "release_version",
        "artifact_hash", "producer_id", "verifier_id", "decision",
        "external_side_effects_enabled", "qualified_at",
    )
    legacy_cutover_columns = (
        "plan_id", "business_id", "source_system", "capability_id", "mode",
        "latest_stage", "legacy_disable_allowed",
        "external_side_effects_enabled", "created_at",
    )
    audit_columns = (
        "record_type",
        "business_id",
        "run_id",
        "details",
        "created_at",
    )
    count_cards = "".join(
        (
            '<div class="card">'
            f"<span>{escape(label.replace('_', ' ').title())}</span>"
            f"<strong>{int(value)}</strong>"
            "</div>"
        )
        for label, value in counts.items()
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent OS v2 Runtime</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, system-ui, sans-serif; }}
    body {{ margin: 0; background: #090d14; color: #e7edf6; }}
    main {{ max-width: 1400px; margin: auto; padding: 32px; }}
    header {{ display: flex; justify-content: space-between; align-items: end; }}
    h1 {{ margin: 0; font-size: 32px; }}
    .eyebrow {{ color: #65d6ad; text-transform: uppercase; letter-spacing: .12em; }}
    .muted {{ color: #91a0b5; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
             gap: 12px; margin: 24px 0; }}
    .card {{ background: #111925; border: 1px solid #263245; border-radius: 12px;
             padding: 16px; }}
    .card span {{ display: block; color: #91a0b5; font-size: 13px; }}
    .card strong {{ display: block; margin-top: 8px; font-size: 28px; }}
    section {{ margin-top: 32px; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid #263245; border-radius: 12px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #263245;
              vertical-align: top; }}
    th {{ color: #91a0b5; background: #111925; }}
    td {{ max-width: 460px; overflow-wrap: anywhere; }}
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <div class="eyebrow">Local control plane</div>
      <h1>Agent OS v2 Runtime</h1>
    </div>
    <div class="muted">Generated {escape(snapshot["generated_at"])}</div>
  </header>
  <div class="grid">{count_cards}</div>
  <section>
    <h2>Production qualification</h2>
    <p class="muted">Independent evidence qualifies isolated deployment and operations; qualification never activates external side effects.</p>
    <div class="table-wrap"><table>
      <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in production_qualification_columns)}</tr></thead>
      <tbody>{_render_rows(production_qualifications, production_qualification_columns)}</tbody>
    </table></div>
  </section>
  <section>
    <h2>Legacy cutover rehearsals</h2>
    <p class="muted">Capability-by-capability, rollback-first plans remain read-only, proposal-only, or shadow; legacy disable is unavailable.</p>
    <div class="table-wrap"><table>
      <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in legacy_cutover_columns)}</tr></thead>
      <tbody>{_render_rows(legacy_cutovers, legacy_cutover_columns)}</tbody>
    </table></div>
  </section>
  <section>
    <h2>Portfolio capability packs</h2>
    <p class="muted">Deterministically accepted Goal 13 modules remain read-only, proposal-only, or simulated.</p>
    <div class="table-wrap"><table>
      <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in capability_pack_columns)}</tr></thead>
      <tbody>{_render_rows(capability_pack_acceptances, capability_pack_columns)}</tbody>
    </table></div>
  </section>
  <section>
    <h2>Aggregate performance evidence</h2>
    <p class="muted">Privacy-safe platform totals are directional, non-causal, and cannot substitute for Goal 12 event-level attribution.</p>
    <div class="table-wrap"><table>
      <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in aggregate_performance_columns)}</tr></thead>
      <tbody>{_render_rows(aggregate_performance, aggregate_performance_columns)}</tbody>
    </table></div>
  </section>
  <section>
    <h2>Affiliate shadow loop</h2>
    <p class="muted">Read-only offer research and historical replay produce verified candidate learning only; no publishing, link mutation, contact, or spend.</p>
    <div class="table-wrap"><table>
      <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in affiliate_columns)}</tr></thead>
      <tbody>{_render_rows(affiliate_shadow_runs, affiliate_columns)}</tbody>
    </table></div>
  </section>
  <section>
    <h2>Shadow model attempts</h2>
    <p class="muted">Proposal-only calls use exact routed models with tools disabled. Prompt, context, output, and secret material are not retained.</p>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in shadow_columns)}</tr></thead>
        <tbody>{_render_rows(shadow_model_attempts, shadow_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Model evaluation replay</h2>
    <p class="muted">Offline fixture replay verifies deterministic structured-output behavior without provider calls.</p>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in replay_columns)}</tr></thead>
        <tbody>{_render_rows(model_evaluation_replays, replay_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Model routing decisions</h2>
    <p class="muted">Every selection or hold binds a versioned catalog and explicit compatibility result.</p>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in routing_columns)}</tr></thead>
        <tbody>{_render_rows(routing_decisions, routing_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Model cost telemetry</h2>
    <p class="muted">Costs are derived from immutable catalog rates and actual token counts, in micro-units of currency.</p>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in cost_columns)}</tr></thead>
        <tbody>{_render_rows(model_cost_totals, cost_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Model usage</h2>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in usage_columns)}</tr></thead>
        <tbody>{_render_rows(model_usage, usage_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Model circuits</h2>
    <p class="muted">Open and half-open routes are excluded; only one cooldown probe may run.</p>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in circuit_columns)}</tr></thead>
        <tbody>{_render_rows(model_circuits, circuit_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Approval queue</h2>
    <p class="muted">Approval identity, current decision, and expiry are durable.</p>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in approval_columns)}</tr></thead>
        <tbody>{_render_rows(approvals, approval_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Emergency-stop history</h2>
    <p class="muted">The latest event for each business controls execution.</p>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in emergency_stop_columns)}</tr></thead>
        <tbody>{_render_rows(emergency_stops, emergency_stop_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Spend envelopes</h2>
    <p class="muted">Amounts are stored in integer minor currency units. Commitments are conservative and append-only.</p>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in spend_envelope_columns)}</tr></thead>
        <tbody>{_render_rows(spend_envelopes, spend_envelope_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Spend commitments</h2>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in spend_commitment_columns)}</tr></thead>
        <tbody>{_render_rows(spend_commitments, spend_commitment_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Business objectives</h2>
    <p class="muted">Status totals: {escape(json.dumps(snapshot["objective_statuses"], sort_keys=True))}</p>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in objective_columns)}</tr></thead>
        <tbody>{_render_rows(objectives, objective_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Autonomous work</h2>
    <p class="muted">Status totals: {escape(json.dumps(snapshot["work_statuses"], sort_keys=True))}</p>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in work_columns)}</tr></thead>
        <tbody>{_render_rows(work_items, work_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Evidence</h2>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in evidence_columns)}</tr></thead>
        <tbody>{_render_rows(evidence, evidence_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Plan evaluations</h2>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in plan_columns)}</tr></thead>
        <tbody>{_render_rows(plans, plan_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Candidate memory</h2>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in memory_columns)}</tr></thead>
        <tbody>{_render_rows(memories, memory_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Execution attempts</h2>
    <p class="muted">Attempted work is not completion.</p>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in attempt_columns)}</tr></thead>
        <tbody>{_render_rows(execution_attempts, attempt_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Evidence receipts</h2>
    <p class="muted">Immutable preconditions and post-attempt observations.</p>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in receipt_columns)}</tr></thead>
        <tbody>{_render_rows(evidence_receipts, receipt_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Outcome verifications</h2>
    <p class="muted">Only independent verified decisions support completion claims.</p>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in verification_columns)}</tr></thead>
        <tbody>{_render_rows(outcome_verifications, verification_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Workflow runs</h2>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in run_columns)}</tr></thead>
        <tbody>{_render_rows(runs, run_columns)}</tbody>
      </table>
    </div>
  </section>
  <section>
    <h2>Audit records</h2>
    <div class="table-wrap">
      <table>
        <thead><tr>{"".join(f"<th>{escape(c)}</th>" for c in audit_columns)}</tr></thead>
        <tbody>{_render_rows(audits, audit_columns)}</tbody>
      </table>
    </div>
  </section>
</main>
</body>
</html>
"""


def write_dashboard(store: SQLiteStore, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard(store.dashboard_snapshot()))
    return path


def make_handler(store: SQLiteStore) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/healthz":
                body = json.dumps({"status": "ok"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            elif self.path == "/":
                body = render_dashboard(store.dashboard_snapshot()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
            else:
                body = b"Not found"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return DashboardHandler


def serve_dashboard(
    store: SQLiteStore,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    loopback = host == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
    if not loopback:
        raise ValueError(
            "dashboard must remain loopback-only until authentication "
            "and tenant-scoped projections exist"
        )
    server = ThreadingHTTPServer((host, port), make_handler(store))
    print(f"Agent OS v2 dashboard: http://{host}:{port}")
    server.serve_forever()
