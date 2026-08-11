"""Local CLI for the first Agent OS v2 runtime slice."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import time
from uuid import uuid4

from .autonomy import AutonomousLoop
from .contracts import (
    ActorIdentity,
    ActorType,
    AuthorityEnvelope,
    AuthorityMode,
    AuthorityRule,
    Business,
    Event,
    Objective,
    ObjectiveStatus,
    Tenant,
)
from .dashboard import serve_dashboard, write_dashboard
from .intelligence import IntelligenceRuntime, Playbook
from .production import (
    ProductionReadinessService,
    TenantDeploymentManifest,
    TenantPackageBuilder,
)
from .runtime import AgentRuntime
from .storage import SQLiteStore
from .communications import ChannelRegistry
from .shadow_runtime import (
    AnthropicMessagesAdapter,
    EnvironmentCredentialResolver,
    ShadowModelRuntime,
)
from .telegram_brain import (
    draft_model_reply,
    gather_owner_context,
    latest_owner_message,
    load_env_secret,
    seed_reply_route,
)
from .telegram_hands import seed_owner_request_objective
from .telegram_inbound import OwnerChannelBinding, TelegramInboundAdapter
from .telegram_outbound import (
    OutboundProposalStore,
    TelegramOutboundSender,
    TelegramSendClient,
    owner_target_ref,
)
from .telegram_transport import (
    TelegramInboundListener,
    UrllibTelegramClient,
    load_bot_token,
)


DEFAULT_DB = Path("state/agent-os-v2.db")
DEFAULT_HTML = Path("state/dashboard.html")
DEFAULT_GROWTH_PLAYBOOK = (
    Path(__file__).resolve().parents[2]
    / "packs"
    / "northwind"
    / "qualified-lead-growth.json"
)


def seed_demo(store: SQLiteStore) -> None:
    store.upsert_tenant(Tenant(tenant_id="demo-tenant", display_name="Demo Tenant"))
    store.upsert_business(
        Business(
            business_id="demo-business",
            tenant_id="demo-tenant",
            legal_name="Demo Business LLC",
            display_name="Demo Business",
            base_currency="USD",
            timezone_name="America/Los_Angeles",
        )
    )
    store.upsert_actor(
        ActorIdentity(
            actor_id="demo-owner",
            tenant_id="demo-tenant",
            actor_type=ActorType.HUMAN,
            roles=frozenset({"owner"}),
            business_ids=frozenset({"demo-business"}),
        )
    )
    store.upsert_actor(
        ActorIdentity(
            actor_id="atlas",
            tenant_id="demo-tenant",
            actor_type=ActorType.AGENT,
            roles=frozenset({"orchestrator"}),
            business_ids=frozenset({"demo-business"}),
        )
    )
    store.upsert_authority_envelope(
        AuthorityEnvelope(
            envelope_id="demo-envelope",
            tenant_id="demo-tenant",
            business_id="demo-business",
            rules=(
                AuthorityRule(
                    action_type="portfolio.review",
                    mode=AuthorityMode.AUTO,
                    roles=frozenset({"orchestrator"}),
                ),
                AuthorityRule(
                    action_type="experiment.plan",
                    mode=AuthorityMode.NOTIFY,
                    roles=frozenset({"orchestrator"}),
                ),
                AuthorityRule(
                    action_type="message.send",
                    mode=AuthorityMode.APPROVE,
                    roles=frozenset({"orchestrator"}),
                ),
                AuthorityRule(
                    action_type="marketing.pipeline.review",
                    mode=AuthorityMode.AUTO,
                    roles=frozenset({"marketing"}),
                ),
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )


def seed_autonomy_demo(store: SQLiteStore) -> None:
    seed_demo(store)
    store.upsert_actor(
        ActorIdentity(
            actor_id="demo-marketing-agent",
            tenant_id="demo-tenant",
            actor_type=ActorType.AGENT,
            roles=frozenset({"marketing"}),
            business_ids=frozenset({"demo-business"}),
        )
    )
    store.upsert_objective(
        Objective(
            objective_id="demo-qualified-leads",
            tenant_id="demo-tenant",
            business_id="demo-business",
            statement="Generate a reliable flow of qualified leads.",
            metric="qualified_leads",
            target=Decimal("25"),
            current_value=Decimal("0"),
            status=ObjectiveStatus.ACTIVE,
            priority=10,
            review_interval_seconds=3600,
        ),
        next_review_at=datetime.now(timezone.utc),
    )


def seed_intelligence_demo(store: SQLiteStore) -> str:
    seed_autonomy_demo(store)
    raw = json.loads(DEFAULT_GROWTH_PLAYBOOK.read_text())
    capability = raw["capability"]
    store.upsert_capability(
        capability_id=capability["capability_id"],
        display_name=capability["display_name"],
        description=capability["description"],
        required_role=capability["required_role"],
        action_types=tuple(capability["action_types"]),
    )
    store.assign_capability(
        tenant_id="demo-tenant",
        business_id="demo-business",
        actor_id="demo-marketing-agent",
        capability_id=capability["capability_id"],
    )
    store.upsert_authority_envelope(
        AuthorityEnvelope(
            envelope_id="demo-envelope",
            tenant_id="demo-tenant",
            business_id="demo-business",
            rules=(
                AuthorityRule(
                    action_type="portfolio.review",
                    mode=AuthorityMode.AUTO,
                    roles=frozenset({"orchestrator"}),
                ),
                AuthorityRule(
                    action_type="experiment.plan",
                    mode=AuthorityMode.NOTIFY,
                    roles=frozenset({"orchestrator"}),
                ),
                AuthorityRule(
                    action_type="message.send",
                    mode=AuthorityMode.APPROVE,
                    roles=frozenset({"orchestrator"}),
                ),
                AuthorityRule(
                    action_type="marketing.pipeline.review",
                    mode=AuthorityMode.AUTO,
                    roles=frozenset({"marketing"}),
                ),
                AuthorityRule(
                    action_type="growth.funnel.diagnose",
                    mode=AuthorityMode.AUTO,
                    capability_ids=frozenset(
                        {capability["capability_id"]}
                    ),
                ),
                AuthorityRule(
                    action_type="growth.experiment.design",
                    mode=AuthorityMode.AUTO,
                    capability_ids=frozenset(
                        {capability["capability_id"]}
                    ),
                ),
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    evidence_id = f"evidence-{uuid4().hex}"
    store.insert_evidence(
        evidence_id=evidence_id,
        tenant_id="demo-tenant",
        business_id="demo-business",
        source_type="local_fixture",
        source_ref="qualified-lead-demo",
        statement="The example visitor-to-lead conversion rate is 0.4%.",
        facts={"conversion_rate": "0.4%"},
        confidence=Decimal("0.90"),
        observed_at=datetime.now(timezone.utc),
    )
    return evidence_id


def command_init(args: argparse.Namespace) -> None:
    store = SQLiteStore(args.db)
    store.initialize()
    print(f"Initialized {store.path}")


def command_demo(args: argparse.Namespace) -> None:
    store = SQLiteStore(args.db)
    store.initialize()
    seed_demo(store)
    event = Event(
        event_id=f"evt-{uuid4().hex}",
        tenant_id="demo-tenant",
        business_id="demo-business",
        source="local-cli",
        actor_id="demo-owner",
        kind="objective.review.requested",
        occurred_at=datetime.now(timezone.utc),
        payload={"objective": "Find the highest-value work to pursue next."},
        idempotency_key=f"demo-{uuid4().hex}",
    )
    result = AgentRuntime(store).process(event)
    output_path = write_dashboard(store, args.html)
    print(
        f"{result.status.value}: {result.summary}\n"
        f"run_id={result.run_id}\n"
        f"dashboard={output_path.resolve()}"
    )


def command_render(args: argparse.Namespace) -> None:
    store = SQLiteStore(args.db)
    store.initialize()
    output_path = write_dashboard(store, args.html)
    print(output_path.resolve())


def command_autonomy_demo(args: argparse.Namespace) -> None:
    store = SQLiteStore(args.db)
    store.initialize()
    seed_autonomy_demo(store)
    report = AutonomousLoop(
        store,
        worker_id="demo-autonomous-worker",
    ).run_cycle(max_work=args.max_work)
    output_path = write_dashboard(store, args.html)
    print(
        f"{json.dumps(asdict(report), sort_keys=True)}\n"
        f"dashboard={output_path.resolve()}"
    )


def command_cycle(args: argparse.Namespace) -> None:
    store = SQLiteStore(args.db)
    store.initialize()
    report = AutonomousLoop(
        store,
        worker_id=args.worker_id,
    ).run_cycle(max_work=args.max_work)
    print(json.dumps(asdict(report), sort_keys=True))


def command_intelligence_demo(args: argparse.Namespace) -> None:
    store = SQLiteStore(args.db)
    store.initialize()
    evidence_id = seed_intelligence_demo(store)
    planning = IntelligenceRuntime(store).plan_objective(
        objective_id="demo-qualified-leads",
        actor_id="demo-marketing-agent",
        evidence_ids=(evidence_id,),
        playbook=Playbook.from_path(DEFAULT_GROWTH_PLAYBOOK),
    )
    cycle = AutonomousLoop(
        store,
        worker_id="demo-bounded-worker",
    ).run_cycle(max_work=args.max_work)
    output_path = write_dashboard(store, args.html)
    print(
        f"planning={json.dumps(asdict(planning), sort_keys=True)}\n"
        f"cycle={json.dumps(asdict(cycle), sort_keys=True)}\n"
        f"dashboard={output_path.resolve()}"
    )


def command_worker(args: argparse.Namespace) -> None:
    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds must be positive")
    store = SQLiteStore(args.db)
    store.initialize()
    loop = AutonomousLoop(store, worker_id=args.worker_id)
    print(
        f"Agent OS v2 autonomous worker {loop.worker_id} started "
        f"(poll={args.poll_seconds}s)"
    )
    try:
        while True:
            try:
                report = loop.run_cycle(max_work=args.max_work)
                if any(asdict(report).values()):
                    print(json.dumps(asdict(report), sort_keys=True))
            except Exception as error:
                print(
                    json.dumps(
                        {
                            "error": f"{type(error).__name__}: {error}",
                            "worker_id": loop.worker_id,
                        },
                        sort_keys=True,
                    )
                )
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("Agent OS v2 autonomous worker stopped")


def command_serve(args: argparse.Namespace) -> None:
    store = SQLiteStore(args.db)
    store.initialize()
    serve_dashboard(store, host=args.host, port=args.port)


def command_doctor(args: argparse.Namespace) -> None:
    store = SQLiteStore(args.db)
    report = store.schema_status()
    print(json.dumps(report, sort_keys=True))
    if (
        report["integrity"] != "ok"
        or not report["migration_valid"]
        or report["current_version"] != report["expected_version"]
    ):
        raise SystemExit(1)


def command_backup(args: argparse.Namespace) -> None:
    store = SQLiteStore(args.db)
    destination = args.output
    if destination is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = Path("state/backups") / f"agent-os-v2-{timestamp}.db"
    backup_path = store.create_backup(destination)
    report = SQLiteStore(backup_path).schema_status()
    if not report["migration_valid"]:
        raise RuntimeError(
            "backup schema or durable-truth key validation failed"
        )
    print(
        json.dumps(
            {
                "backup": str(backup_path.resolve()),
                "integrity": report["integrity"],
                "schema_version": report["current_version"],
                "truth_key": report["truth_key"],
            },
            sort_keys=True,
        )
    )


def command_migrate(args: argparse.Namespace) -> None:
    store = SQLiteStore(args.db)
    before = store.schema_status()
    if not before["exists"]:
        raise FileNotFoundError(
            "database does not exist; use init for new state"
        )
    if not before["migration_valid"]:
        raise RuntimeError(
            "database migration ledger is invalid; refusing migration"
        )
    destination = args.backup
    if destination is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = (
            Path("state/backups")
            / f"pre-migration-{timestamp}.db"
        )
    backup_path = store.migrate(destination)
    after = store.schema_status()
    print(
        json.dumps(
            {
                "backup": str(backup_path.resolve()),
                "from_version": before["current_version"],
                "integrity": after["integrity"],
                "to_version": after["current_version"],
            },
            sort_keys=True,
        )
    )


def command_build_tenant_package(args: argparse.Namespace) -> None:
    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("tenant package manifest must be a JSON object")
    manifest = TenantDeploymentManifest(**payload)
    package = TenantPackageBuilder().build(manifest, args.output)
    print(json.dumps({
        "external_side_effects_enabled": False,
        "manifest_hash": manifest.manifest_hash,
        "package": str(package.resolve()),
    }, sort_keys=True))


def command_production_readiness(args: argparse.Namespace) -> None:
    store = SQLiteStore(args.db)
    status = store.schema_status()
    if not status["migration_valid"]:
        raise RuntimeError("database is not current and attested")
    result = ProductionReadinessService(store).readiness(
        tenant_id=args.tenant_id,
        business_id=args.business_id,
        release_version=args.release_version,
    )
    print(json.dumps(result, sort_keys=True))


def seed_channel_scope(store: SQLiteStore, args: argparse.Namespace) -> None:
    """Seed the tenant scope one owner channel needs for safe triage runs."""
    store.upsert_tenant(
        Tenant(tenant_id=args.tenant_id, display_name=args.tenant_id)
    )
    store.upsert_business(
        Business(
            business_id=args.business_id,
            tenant_id=args.tenant_id,
            legal_name=args.business_id,
            display_name=args.business_id,
            base_currency="USD",
            timezone_name="UTC",
        )
    )
    store.upsert_actor(
        ActorIdentity(
            actor_id=args.channel_actor_id,
            tenant_id=args.tenant_id,
            actor_type=ActorType.SERVICE,
            roles=frozenset({"channel-intake"}),
            business_ids=frozenset({args.business_id}),
        )
    )
    store.upsert_actor(
        ActorIdentity(
            actor_id="atlas",
            tenant_id=args.tenant_id,
            actor_type=ActorType.AGENT,
            roles=frozenset({"orchestrator"}),
            business_ids=frozenset({args.business_id}),
        )
    )
    store.upsert_authority_envelope(
        AuthorityEnvelope(
            envelope_id=f"channel-triage-{args.tenant_id}",
            tenant_id=args.tenant_id,
            business_id=args.business_id,
            rules=(
                AuthorityRule(
                    action_type="event.triage",
                    mode=AuthorityMode.AUTO,
                    roles=frozenset({"orchestrator"}),
                ),
                # Owner-filed research and drafting run AUTO by explicit owner
                # decision (2026-08-03): both are read-only or proposal-only,
                # so the consequential gates live at publish/spend/connection
                # boundaries, not at filing. Approval fatigue is a failure
                # mode; reserve APPROVE for irreversible or outward actions.
                AuthorityRule(
                    action_type="affiliate.offer.research",
                    mode=AuthorityMode.AUTO,
                    roles=frozenset({"orchestrator"}),
                ),
                AuthorityRule(
                    action_type="affiliate.content.draft",
                    mode=AuthorityMode.AUTO,
                    roles=frozenset({"orchestrator"}),
                ),
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    seed_owner_request_objective(
        store,
        binding=OwnerChannelBinding(
            bot_ref=args.bot_ref,
            owner_user_id=args.owner_user_id,
            tenant_id=args.tenant_id,
            business_id=args.business_id,
            actor_id=args.channel_actor_id,
        ),
    )


def command_telegram_listen(args: argparse.Namespace) -> None:
    token = load_bot_token(args.token_env)
    store = SQLiteStore(args.db)
    store.initialize()
    if args.seed:
        seed_channel_scope(store, args)
    binding = OwnerChannelBinding(
        bot_ref=args.bot_ref,
        owner_user_id=args.owner_user_id,
        tenant_id=args.tenant_id,
        business_id=args.business_id,
        actor_id=args.channel_actor_id,
    )
    listener = TelegramInboundListener(
        adapter=TelegramInboundAdapter(binding),
        runtime=AgentRuntime(store, worker_id=f"telegram-{args.bot_ref}"),
        client=UrllibTelegramClient(token),
        poll_timeout_seconds=args.poll_timeout,
    )
    print(
        f"Telegram inbound listener started for bot_ref={args.bot_ref} "
        f"(read-only getUpdates; no send operation exists)"
    )
    try:
        listener.run(
            max_cycles=args.cycles,
            on_summary=lambda summary: (
                print(json.dumps(asdict(summary), sort_keys=True))
                if summary.received
                else None
            ),
        )
    except KeyboardInterrupt:
        print("Telegram inbound listener stopped")


def _outbound_binding(args: argparse.Namespace) -> OwnerChannelBinding:
    return OwnerChannelBinding(
        bot_ref=args.bot_ref,
        owner_user_id=args.owner_user_id,
        tenant_id=args.tenant_id,
        business_id=args.business_id,
        actor_id=args.channel_actor_id,
    )


def command_telegram_draft(args: argparse.Namespace) -> None:
    binding = _outbound_binding(args)
    store = OutboundProposalStore(args.outbox)
    proposal = store.draft(
        registry=ChannelRegistry(),
        target_ref=owner_target_ref(binding),
        body=args.body.strip(),
    )
    print(f"drafted {proposal.proposal_id} (status={proposal.status})")
    print(f"sha256={proposal.payload_hash}")
    print("--- body ---")
    print(proposal.body)


def command_telegram_outbox(args: argparse.Namespace) -> None:
    store = OutboundProposalStore(args.outbox)
    for proposal_id in store.list_ids():
        proposal = store.load(proposal_id)
        print(f"{proposal.status:>9}  {proposal.proposal_id}")
        if args.show_bodies:
            print(f"           {proposal.body}")


def command_telegram_decide(args: argparse.Namespace) -> None:
    store = OutboundProposalStore(args.outbox)
    proposal = store.load(args.proposal_id)
    print("--- body under decision ---")
    print(proposal.body)
    decided = store.decide(args.proposal_id, approve=args.approve)
    print(f"{decided.proposal_id} is now {decided.status}")


def command_telegram_reply_model(args: argparse.Namespace) -> None:
    import os

    binding = _outbound_binding(args)
    store = SQLiteStore(args.db)
    store.initialize()
    key_name = "ANTHROPIC_API_KEY"
    os.environ[key_name] = load_env_secret(args.anthropic_env, key_name)
    if args.seed_route:
        seed_reply_route(
            store,
            binding=binding,
            credential_env_name=key_name,
            provider_model_ref=args.model_ref,
            monthly_budget_micros=args.budget_micros,
        )
    event_id, message_text = latest_owner_message(store, binding=binding)
    runtime = ShadowModelRuntime(
        store,
        credential_resolver=EnvironmentCredentialResolver(),
        adapters=(AnthropicMessagesAdapter(),),
    )
    context = gather_owner_context(
        store, binding=binding, status_doc=args.status_doc
    )
    draft = draft_model_reply(
        store=store,
        runtime=runtime,
        outbox=OutboundProposalStore(args.outbox),
        binding=binding,
        message_text=message_text,
        source_event_id=event_id,
        context=context,
    )
    proposal = draft.proposal
    print(f"answering event {event_id} with {len(context)} context items")
    print(f"drafted {proposal.proposal_id} (status={proposal.status})")
    if draft.work_request is not None:
        print(f"work request flagged: {draft.work_request['action_type']}")
    print("--- proposed reply (unsent until approved) ---")
    print(proposal.body)


def command_telegram_chat(args: argparse.Namespace) -> None:
    import os

    from .telegram_chat import OwnerChatLoop

    binding = _outbound_binding(args)
    store = SQLiteStore(args.db)
    store.initialize()
    if args.seed:
        seed_channel_scope(store, args)
    if args.model_provider == "cli":
        from .claude_cli import ClaudeCLIAdapter
        from .telegram_brain import seed_cli_reply_route

        # A non-secret marker: subscription auth belongs to the CLI login.
        marker_name = "CLAUDE_CLI_SUBSCRIPTION"
        os.environ[marker_name] = "cli-subscription-auth"
        cli_model_ref = (
            f"anthropic-cli/{args.model_ref.split('/', 1)[-1]}"
        )
        seed_cli_reply_route(
            store,
            binding=binding,
            credential_env_name=marker_name,
            provider_model_ref=cli_model_ref,
            monthly_budget_micros=args.budget_micros,
        )
        model_adapter = ClaudeCLIAdapter(claude_bin=args.claude_bin)
    else:
        key_name = "ANTHROPIC_API_KEY"
        os.environ[key_name] = load_env_secret(args.anthropic_env, key_name)
        seed_reply_route(
            store,
            binding=binding,
            credential_env_name=key_name,
            provider_model_ref=args.model_ref,
            monthly_budget_micros=args.budget_micros,
        )
        model_adapter = AnthropicMessagesAdapter()
    token = load_bot_token(args.token_env)

    def interactive_decide(body: str) -> bool:
        print("--- Atlas drafted (unsent) ---")
        print(body)
        answer = input("send this reply? [y/N] ").strip().lower()
        return answer in ("y", "yes")

    def context_provider():
        return gather_owner_context(
            store, binding=binding, status_doc=args.status_doc
        )

    typing_client = TelegramSendClient(token)

    loop = OwnerChatLoop(
        store=store,
        binding=binding,
        inbound_client=UrllibTelegramClient(token),
        model_runtime=ShadowModelRuntime(
            store,
            credential_resolver=EnvironmentCredentialResolver(),
            adapters=(model_adapter,),
        ),
        outbox=OutboundProposalStore(args.outbox),
        sender=TelegramOutboundSender(
            store=OutboundProposalStore(args.outbox),
            client=TelegramSendClient(token),
            binding=binding,
        ),
        decide=interactive_decide,
        standing_owner_approval=args.standing_approval,
        poll_timeout_seconds=args.poll_timeout,
        execute_owner_work=not args.no_owner_work,
        context_provider=context_provider,
        typing_notifier=lambda: typing_client.send_chat_action(
            chat_id=binding.owner_user_id
        ),
    )
    mode = (
        "standing owner approval (this launch grants approval for replies "
        "to your own chat only)"
        if args.standing_approval
        else "interactive approval per reply"
    )
    print(f"Atlas chat loop started — {mode}. Ctrl-C to stop.")
    from .telegram_transport import TelegramTransportError

    offset = 0
    consecutive_failures = 0
    try:
        cycles = 0
        while args.cycles is None or cycles < args.cycles:
            try:
                offset, turns = loop.run_cycle(offset)
            except TelegramTransportError as error:
                consecutive_failures += 1
                print(f"transport error ({consecutive_failures}/5): {error}")
                if consecutive_failures >= 5:
                    raise SystemExit(
                        "five consecutive transport failures; stopping"
                    )
                time.sleep(2 * consecutive_failures)
                cycles += 1
                continue
            consecutive_failures = 0
            cycles += 1
            for turn in turns:
                line = (
                    f"answered {turn.event_id}: decision={turn.decision} "
                    f"sent={turn.sent}"
                )
                if turn.note:
                    line += f" note={turn.note}"
                print(line)
    except KeyboardInterrupt:
        print("Atlas chat loop stopped")


def command_telegram_send(args: argparse.Namespace) -> None:
    sender = TelegramOutboundSender(
        store=OutboundProposalStore(args.outbox),
        client=TelegramSendClient(load_bot_token(args.token_env)),
        binding=_outbound_binding(args),
    )
    sent = sender.send_approved(args.proposal_id)
    print(f"{sent.proposal_id} sent at {sent.sent_at}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.set_defaults(command=None)
    subparsers = parser.add_subparsers(dest="command_name")

    init_parser = subparsers.add_parser("init", help="initialize local state")
    init_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    init_parser.set_defaults(command=command_init)

    demo_parser = subparsers.add_parser("demo", help="run a safe local simulation")
    demo_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    demo_parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    demo_parser.set_defaults(command=command_demo)

    autonomy_demo_parser = subparsers.add_parser(
        "autonomy-demo",
        help="seed one objective and run a safe autonomous cycle",
    )
    autonomy_demo_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    autonomy_demo_parser.add_argument(
        "--html",
        type=Path,
        default=DEFAULT_HTML,
    )
    autonomy_demo_parser.add_argument("--max-work", type=int, default=10)
    autonomy_demo_parser.set_defaults(command=command_autonomy_demo)

    cycle_parser = subparsers.add_parser(
        "run-cycle",
        help="discover and execute one bounded autonomous cycle",
    )
    cycle_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    cycle_parser.add_argument("--worker-id", default="local-cycle-worker")
    cycle_parser.add_argument("--max-work", type=int, default=10)
    cycle_parser.set_defaults(command=command_cycle)

    intelligence_demo_parser = subparsers.add_parser(
        "intelligence-demo",
        help="run the evidence, plan, evaluation, and learning pilot",
    )
    intelligence_demo_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
    )
    intelligence_demo_parser.add_argument(
        "--html",
        type=Path,
        default=DEFAULT_HTML,
    )
    intelligence_demo_parser.add_argument("--max-work", type=int, default=10)
    intelligence_demo_parser.set_defaults(command=command_intelligence_demo)

    worker_parser = subparsers.add_parser(
        "run-worker",
        help="continuously poll the durable autonomous work loop",
    )
    worker_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    worker_parser.add_argument("--worker-id", default="local-headless-worker")
    worker_parser.add_argument("--poll-seconds", type=float, default=30.0)
    worker_parser.add_argument("--max-work", type=int, default=10)
    worker_parser.set_defaults(command=command_worker)

    render_parser = subparsers.add_parser(
        "render-dashboard", help="write a static dashboard snapshot"
    )
    render_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    render_parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    render_parser.set_defaults(command=command_render)

    serve_parser = subparsers.add_parser(
        "serve-dashboard", help="serve the read-only local dashboard"
    )
    serve_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.set_defaults(command=command_serve)

    doctor_parser = subparsers.add_parser(
        "doctor", help="verify local database integrity and schema version"
    )
    doctor_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    doctor_parser.set_defaults(command=command_doctor)

    migrate_parser = subparsers.add_parser(
        "migrate-state",
        help="back up and explicitly migrate an existing local database",
    )
    migrate_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    migrate_parser.add_argument("--backup", type=Path)
    migrate_parser.set_defaults(command=command_migrate)

    backup_parser = subparsers.add_parser(
        "backup-state", help="create an integrity-checked local database backup"
    )
    backup_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    backup_parser.add_argument("--output", type=Path)
    backup_parser.set_defaults(command=command_backup)

    package_parser = subparsers.add_parser(
        "build-tenant-package",
        help="build an atomic isolated tenant package containing no secret values",
    )
    package_parser.add_argument("--manifest", type=Path, required=True)
    package_parser.add_argument("--output", type=Path, required=True)
    package_parser.set_defaults(command=command_build_tenant_package)

    readiness_parser = subparsers.add_parser(
        "production-readiness",
        help="read the fail-closed production qualification decision",
    )
    readiness_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    readiness_parser.add_argument("--tenant-id", required=True)
    readiness_parser.add_argument("--business-id", required=True)
    readiness_parser.add_argument("--release-version", required=True)
    readiness_parser.set_defaults(command=command_production_readiness)

    telegram_parser = subparsers.add_parser(
        "telegram-listen",
        help="poll one owner-verified Telegram bot into intake (read-only)",
    )
    telegram_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    telegram_parser.add_argument("--token-env", type=Path, required=True)
    telegram_parser.add_argument("--bot-ref", required=True)
    telegram_parser.add_argument("--owner-user-id", type=int, required=True)
    telegram_parser.add_argument("--tenant-id", required=True)
    telegram_parser.add_argument("--business-id", required=True)
    telegram_parser.add_argument(
        "--channel-actor-id", default="channel-telegram-inbound"
    )
    telegram_parser.add_argument("--poll-timeout", type=int, default=25)
    telegram_parser.add_argument("--cycles", type=int, default=None)
    telegram_parser.add_argument(
        "--seed",
        action="store_true",
        help="seed tenant, business, actors, and the event.triage authority rule",
    )
    telegram_parser.set_defaults(command=command_telegram_listen)

    def add_binding_arguments(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--bot-ref", required=True)
        sub.add_argument("--owner-user-id", type=int, required=True)
        sub.add_argument("--tenant-id", required=True)
        sub.add_argument("--business-id", required=True)
        sub.add_argument(
            "--channel-actor-id", default="channel-telegram-inbound"
        )
        sub.add_argument(
            "--outbox", type=Path, default=Path("state/telegram-outbox")
        )

    draft_parser = subparsers.add_parser(
        "telegram-draft",
        help="draft one outbound reply as an unsendable proposal",
    )
    add_binding_arguments(draft_parser)
    draft_parser.add_argument("--body", required=True)
    draft_parser.set_defaults(command=command_telegram_draft)

    outbox_parser = subparsers.add_parser(
        "telegram-outbox", help="list outbound proposals and their statuses"
    )
    outbox_parser.add_argument(
        "--outbox", type=Path, default=Path("state/telegram-outbox")
    )
    outbox_parser.add_argument("--show-bodies", action="store_true")
    outbox_parser.set_defaults(command=command_telegram_outbox)

    decide_parser = subparsers.add_parser(
        "telegram-decide",
        help="approve or reject one proposed outbound message",
    )
    decide_parser.add_argument(
        "--outbox", type=Path, default=Path("state/telegram-outbox")
    )
    decide_parser.add_argument("--proposal-id", required=True)
    decision = decide_parser.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--reject", dest="approve", action="store_false")
    decide_parser.set_defaults(command=command_telegram_decide)

    reply_parser = subparsers.add_parser(
        "telegram-reply-model",
        help="draft a model-written reply to the latest owner message",
    )
    add_binding_arguments(reply_parser)
    reply_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    reply_parser.add_argument("--anthropic-env", type=Path, required=True)
    reply_parser.add_argument(
        "--model-ref", default="anthropic/claude-sonnet-5"
    )
    reply_parser.add_argument(
        "--budget-micros", type=int, default=20_000_000,
        help="monthly provider budget in micros (default $20)",
    )
    reply_parser.add_argument("--seed-route", action="store_true")
    reply_parser.add_argument(
        "--status-doc", type=Path, default=Path("docs/PROJECT_STATUS.md")
    )
    reply_parser.set_defaults(command=command_telegram_reply_model)

    chat_parser = subparsers.add_parser(
        "telegram-chat",
        help="continuous owner chat: listen, draft, decide, send",
    )
    add_binding_arguments(chat_parser)
    chat_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    chat_parser.add_argument("--token-env", type=Path, required=True)
    chat_parser.add_argument("--anthropic-env", type=Path, required=True)
    chat_parser.add_argument(
        "--model-ref", default="anthropic/claude-sonnet-5"
    )
    chat_parser.add_argument("--budget-micros", type=int, default=20_000_000)
    chat_parser.add_argument("--poll-timeout", type=int, default=25)
    chat_parser.add_argument("--cycles", type=int, default=None)
    chat_parser.add_argument("--seed", action="store_true")
    chat_parser.add_argument(
        "--status-doc", type=Path, default=Path("docs/PROJECT_STATUS.md")
    )
    chat_parser.add_argument(
        "--standing-approval",
        action="store_true",
        help=(
            "grant approval for model replies to your own chat for this "
            "process lifetime; every other target remains impossible"
        ),
    )
    chat_parser.add_argument(
        "--no-owner-work",
        action="store_true",
        help="disable executing ready owner-filed work items in this loop",
    )
    chat_parser.add_argument(
        "--model-provider",
        choices=("api", "cli"),
        default="api",
        help=(
            "api: Anthropic Messages API with the local key; cli: the "
            "owner's Claude Code CLI subscription (catalog 2.0.0)"
        ),
    )
    chat_parser.add_argument(
        "--claude-bin",
        default="claude",
        help="Claude Code CLI executable used when --model-provider is cli",
    )
    chat_parser.set_defaults(command=command_telegram_chat)

    send_parser = subparsers.add_parser(
        "telegram-send",
        help="send one approved proposal to the bound owner, exactly once",
    )
    add_binding_arguments(send_parser)
    send_parser.add_argument("--token-env", type=Path, required=True)
    send_parser.add_argument("--proposal-id", required=True)
    send_parser.set_defaults(command=command_telegram_send)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        raise SystemExit(2)
    args.command(args)


if __name__ == "__main__":
    main()
