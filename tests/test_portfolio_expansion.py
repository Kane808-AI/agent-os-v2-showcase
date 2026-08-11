from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os.contracts import (  # noqa: E402
    ActorIdentity,
    ActorType,
    Business,
    Tenant,
)
from agent_os.dashboard import render_dashboard  # noqa: E402
from agent_os.communications import (  # noqa: E402
    ChannelAdapterDescriptor,
    ChannelKind,
    ChannelRegistry,
    CommunicationError,
    SAFE_CHANNEL_CAPABILITIES,
)
from agent_os.portfolio import (  # noqa: E402
    AggregatePerformanceService,
    AggregateSnapshot,
    AggregateVerificationDecision,
    CapabilityPackCatalog,
    PackDecision,
    PortfolioError,
)
from agent_os.storage import SQLiteStore  # noqa: E402


class PortfolioExpansionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "portfolio.db"
        self.store = SQLiteStore(self.database)
        self.store.initialize()
        self.now = datetime(2026, 7, 31, 18, tzinfo=timezone.utc)
        self.store.upsert_tenant(Tenant("tenant-1", "Tenant One"))
        self.store.upsert_business(Business(
            "business-1", "tenant-1", "Commerce LLC", "Commerce", "USD", "UTC"
        ))
        self.store.upsert_tenant(Tenant("tenant-2", "Tenant Two"))
        self.store.upsert_business(Business(
            "business-2", "tenant-2", "Other LLC", "Commerce", "USD", "UTC"
        ))
        self.store.upsert_actor(ActorIdentity(
            "producer", "tenant-1", ActorType.AGENT,
            frozenset({"commerce", "marketing"}), frozenset({"business-1"}),
        ))
        self.store.upsert_actor(ActorIdentity(
            "qa-verifier", "tenant-1", ActorType.AGENT,
            frozenset({"qa"}), frozenset({"business-1"}),
        ))
        self.store.upsert_actor(ActorIdentity(
            "other-producer", "tenant-1", ActorType.AGENT,
            frozenset({"research"}), frozenset({"business-1"}),
        ))
        self.store.upsert_actor(ActorIdentity(
            "producer-2", "tenant-2", ActorType.AGENT,
            frozenset({"commerce"}), frozenset({"business-2"}),
        ))
        self.store.insert_evidence(
            evidence_id="pinterest-evidence", tenant_id="tenant-1",
            business_id="business-1", source_type="pinterest_aggregate",
            source_ref="pinterest:pin:live-window",
            statement="Pinterest aggregate performance was observed read-only.",
            facts={
                "impressions": 136, "engagements": 3,
                "content_clicks": 2, "outbound_clicks": 1,
            },
            confidence=Decimal("0.90"), observed_at=self.now-timedelta(hours=1),
        )
        self.store.insert_evidence(
            evidence_id="amazon-evidence", tenant_id="tenant-1",
            business_id="business-1", source_type="affiliate_report",
            source_ref="amazon:offer:live-window",
            statement="Affiliate offer and aggregate conversions were observed read-only.",
            facts={
                "conversions": 0, "gross_revenue_minor": 0,
                "commission_minor": 0,
            },
            confidence=Decimal("0.90"), observed_at=self.now-timedelta(hours=1),
        )
        self.catalog = CapabilityPackCatalog(
            ROOT / "departments", ROOT / "agents"
        )
        self.service = AggregatePerformanceService(self.store)

    def tearDown(self):
        self.tempdir.cleanup()

    def snapshot(self, **changes):
        values = dict(
            channel="pinterest", offer_key="amazon-B08M94BTYC",
            source_system="pinterest-amazon-readonly",
            source_ref="live:2026-07", window_start=self.now-timedelta(days=30),
            window_end=self.now-timedelta(days=1), impressions=136,
            engagements=3, content_clicks=2, outbound_clicks=1,
            conversions=0, gross_revenue_minor=0, commission_minor=0,
            minimum_outbound_clicks=1,
            evidence_refs=("pinterest-evidence", "amazon-evidence"),
        )
        values.update(changes)
        return AggregateSnapshot(**values)

    def test_all_required_packs_pass_deterministic_acceptance(self):
        results = self.catalog.evaluate_all(store=self.store, now=self.now)
        self.assertEqual(len(results), 13)
        self.assertTrue(all(result.passed for result in results))
        self.assertEqual(
            {result.pack_id for result in results},
            set(self.catalog.policy["required_pack_ids"]),
        )
        snapshot = self.store.dashboard_snapshot()
        self.assertEqual(snapshot["counts"]["capability_pack_acceptances"], 13)
        html = render_dashboard(snapshot)
        self.assertIn("Portfolio capability packs", html)
        self.assertIn("digital-marketing-consulting", html)
        self.assertTrue(self.store.schema_status()["migration_valid"])

    def test_pack_acceptance_replay_is_idempotent(self):
        self.catalog.evaluate_all(store=self.store, now=self.now)
        self.catalog.evaluate_all(store=self.store, now=self.now+timedelta(minutes=1))
        self.assertEqual(
            self.store.dashboard_snapshot()["counts"]["capability_pack_acceptances"],
            13,
        )

    def test_every_pack_is_business_neutral_and_non_executing(self):
        forbidden = self.catalog.policy["global_forbidden_actions"]
        for pack_id, pack in self.catalog.packs.items():
            self.assertEqual(pack["execution_boundary"], "shadow-only")
            self.assertNotEqual(pack["owner_role"], pack["verifier_role"])
            self.assertTrue(all(
                source["mode"] == "read_only" for source in pack["input_sources"]
            ))
            for capability in pack["capabilities"]:
                self.assertLessEqual(
                    set(capability["allowed_modes"]),
                    {"read_only", "proposal", "simulated"},
                )
                for action in forbidden:
                    self.assertEqual(
                        self.catalog.decide(
                            pack_id=pack_id,
                            capability_id=capability["capability_id"],
                            requested_mode="external",
                            action_type=action,
                        ),
                        PackDecision.HELD,
                    )
            self.assertNotIn("northwind", json.dumps(pack).lower())

    def test_finance_and_accounting_remain_separate(self):
        finance = self.catalog.packs["finance"]
        accounting = self.catalog.packs["accounting"]
        self.assertEqual(finance["owner_role"], "finance-lead")
        self.assertEqual(accounting["owner_role"], "accounting-controller")
        self.assertNotEqual(
            {item["capability_id"] for item in finance["capabilities"]},
            {item["capability_id"] for item in accounting["capabilities"]},
        )
        self.assertEqual(
            self.catalog.decide(
                pack_id="finance", capability_id="finance-cash-position-review",
                requested_mode="external", action_type="banking.money-movement",
            ),
            PackDecision.HELD,
        )

    def test_northwind_mapping_covers_every_pack_without_activation(self):
        mapping = json.loads(
            (ROOT / "packs/northwind/portfolio-capabilities.json").read_text()
        )
        assignments = json.loads(
            (ROOT / "packs/northwind/agent-assignments.json").read_text()
        )
        self.assertEqual(mapping["status"], "accepted-not-activated")
        self.assertFalse(mapping["external_side_effects_enabled"])
        self.assertEqual(
            set(mapping["business_capability_packs"]),
            {business["business_key"] for business in assignments["businesses"]},
        )
        covered = {
            pack_id
            for pack_ids in mapping["business_capability_packs"].values()
            for pack_id in pack_ids
        }
        self.assertEqual(covered, set(self.catalog.packs))

    def test_pack_tampering_or_missing_coverage_is_rejected(self):
        copied = Path(self.tempdir.name) / "departments"
        shutil.copytree(ROOT / "departments", copied)
        marketing_path = copied / "marketing" / "capability-pack.json"
        marketing = json.loads(marketing_path.read_text())
        marketing["verifier_role"] = marketing["owner_role"]
        marketing_path.write_text(json.dumps(marketing))
        with self.assertRaisesRegex(PortfolioError, "independent verification"):
            CapabilityPackCatalog(copied, ROOT / "agents")

        shutil.rmtree(copied / "marketing")
        policy_path = copied / "capability-pack-policy.json"
        policy = json.loads(policy_path.read_text())
        policy["required_pack_ids"] = [
            pack_id for pack_id in policy["required_pack_ids"]
            if pack_id != "digital-marketing-consulting"
        ]
        policy_path.write_text(json.dumps(policy))
        with self.assertRaisesRegex(PortfolioError, "coverage is incomplete"):
            CapabilityPackCatalog(copied, ROOT / "agents")

    def test_live_shaped_aggregate_is_verified_but_directional_only(self):
        result = self.service.import_snapshot(
            tenant_id="tenant-1", business_id="business-1",
            producer_id="producer", snapshot=self.snapshot(), now=self.now,
        )
        self.assertEqual(result.metrics["outbound_click_rate_bps"], 73)
        self.assertEqual(result.metrics["conversion_rate_bps"], 0)
        self.assertTrue(result.metrics["sufficient_sample"])
        self.assertEqual(result.evidence_class, "directional_aggregate")
        self.assertEqual(
            self.service.verify(
                snapshot_id=result.snapshot_id, verifier_id="qa-verifier",
                now=self.now,
            ),
            AggregateVerificationDecision.VERIFIED,
        )
        dashboard = self.store.dashboard_snapshot()
        self.assertEqual(dashboard["counts"]["aggregate_performance_snapshots"], 1)
        self.assertIn("directional_aggregate", render_dashboard(dashboard))
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT * FROM aggregate_performance_snapshots"
            ).fetchone()
        self.assertIn("does not identify people", row["limitation"])
        self.assertNotIn("subject", row.keys())

    def test_small_aggregate_is_inconclusive_and_cannot_claim_causality(self):
        result = self.service.import_snapshot(
            tenant_id="tenant-1", business_id="business-1",
            producer_id="producer",
            snapshot=self.snapshot(minimum_outbound_clicks=10), now=self.now,
        )
        self.assertEqual(
            self.service.verify(
                snapshot_id=result.snapshot_id, verifier_id="qa-verifier",
                now=self.now,
            ),
            AggregateVerificationDecision.INCONCLUSIVE,
        )
        self.assertFalse(result.metrics["sufficient_sample"])
        with self.store._connection() as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'aggregate_%'"
                ).fetchall()
            }
        self.assertEqual(tables, {
            "aggregate_performance_snapshots",
            "aggregate_performance_verifications",
        })

    def test_aggregate_rejects_self_verification_and_cross_scope_evidence(self):
        result = self.service.import_snapshot(
            tenant_id="tenant-1", business_id="business-1",
            producer_id="producer", snapshot=self.snapshot(), now=self.now,
        )
        with self.assertRaisesRegex(PortfolioError, "independent scoped QA"):
            self.service.verify(
                snapshot_id=result.snapshot_id, verifier_id="producer", now=self.now,
            )
        with self.assertRaisesRegex(PortfolioError, "missing or crosses scope"):
            self.service.import_snapshot(
                tenant_id="tenant-2", business_id="business-2",
                producer_id="producer-2", snapshot=self.snapshot(), now=self.now,
            )

    def test_aggregate_rejects_non_read_only_future_or_impossible_funnel(self):
        with self.assertRaisesRegex(PortfolioError, "read-only"):
            self.snapshot(source_system="pinterest-api")
        with self.assertRaisesRegex(PortfolioError, "inconsistent"):
            self.snapshot(engagements=1, content_clicks=2)
        with self.assertRaisesRegex(PortfolioError, "complete"):
            self.service.import_snapshot(
                tenant_id="tenant-1", business_id="business-1",
                producer_id="producer",
                snapshot=self.snapshot(window_end=self.now+timedelta(days=1)),
                now=self.now,
            )

    def test_aggregate_counts_must_match_normalized_evidence(self):
        with self.assertRaisesRegex(PortfolioError, "normalized evidence"):
            self.service.import_snapshot(
                tenant_id="tenant-1", business_id="business-1",
                producer_id="producer",
                snapshot=self.snapshot(impressions=137), now=self.now,
            )

    def test_doctor_rejects_forged_aggregate_measurement(self):
        limitation = (
            "Aggregate evidence does not identify people or prove incrementality."
        )
        with self.store._immediate_connection() as connection:
            connection.execute(
                """
                INSERT INTO aggregate_performance_snapshots VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    "forged", "tenant-1", "business-1", "producer",
                    "pinterest", "offer", "pinterest-readonly", "forged-ref",
                    (self.now-timedelta(days=30)).isoformat(),
                    (self.now-timedelta(days=1)).isoformat(),
                    136, 3, 2, 1, 0, 0, 0, 1,
                    json.dumps(["pinterest-evidence", "amazon-evidence"]),
                    "directional_aggregate", "0" * 64,
                    self.now.isoformat(), limitation,
                ),
            )
        status = self.store.schema_status()
        self.assertFalse(status["migration_valid"])
        self.assertTrue(any(
            "aggregate performance hash" in error
            for error in status["migration_errors"]
        ))

    def test_goal12_event_attribution_is_not_weakened(self):
        with self.store._connection() as connection:
            affiliate_sql = " ".join(
                row[0] or "" for row in connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                    WHERE name LIKE 'affiliate_%' OR name LIKE 'enforce_affiliate_%'
                    """
                ).fetchall()
            )
            aggregate_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(aggregate_performance_snapshots)"
                ).fetchall()
            }
        self.assertNotIn("aggregate_performance", affiliate_sql)
        self.assertNotIn("memory_id", aggregate_columns)
        self.assertNotIn("subject_key", aggregate_columns)

    def test_backup_preserves_acceptances_and_aggregate_evidence(self):
        self.catalog.evaluate_all(store=self.store, now=self.now)
        self.service.import_snapshot(
            tenant_id="tenant-1", business_id="business-1",
            producer_id="producer", snapshot=self.snapshot(), now=self.now,
        )
        backup_path = self.store.create_backup(
            Path(self.tempdir.name) / "portfolio-backup.db"
        )
        backup = SQLiteStore(backup_path)
        self.assertTrue(backup.schema_status()["migration_valid"])
        counts = backup.dashboard_snapshot()["counts"]
        self.assertEqual(counts["capability_pack_acceptances"], 13)
        self.assertEqual(counts["aggregate_performance_snapshots"], 1)

    def test_dashboard_is_canonical_and_external_channels_are_replaceable(self):
        registry = ChannelRegistry()
        self.assertEqual(set(ChannelKind), {
            ChannelKind.DASHBOARD, ChannelKind.SLACK, ChannelKind.TELEGRAM,
            ChannelKind.DISCORD, ChannelKind.TEAMS, ChannelKind.EMAIL,
        })
        for channel in ChannelKind:
            descriptor = registry.descriptor(channel)
            self.assertLessEqual(descriptor.capabilities, SAFE_CHANNEL_CAPABILITIES)
            self.assertEqual(
                descriptor.canonical_control_plane,
                channel is ChannelKind.DASHBOARD,
            )
        replacement = ChannelAdapterDescriptor(
            "tenant-slack-proposal", "2.0.0", ChannelKind.SLACK,
            frozenset({"inbound.read", "outbound.propose"}),
        )
        replaced = registry.replace(ChannelKind.SLACK, replacement)
        self.assertEqual(
            replaced.descriptor(ChannelKind.SLACK).adapter_id,
            "tenant-slack-proposal",
        )
        self.assertEqual(
            replaced.descriptor(ChannelKind.DASHBOARD).adapter_id,
            "local-dashboard",
        )

    def test_channel_proposal_hashes_body_and_cannot_send(self):
        proposal = ChannelRegistry().propose(
            channel=ChannelKind.EMAIL, target_ref="contact-ref-17",
            body="Draft only; do not send.",
        )
        self.assertEqual(proposal.status, "proposed")
        self.assertTrue(proposal.requires_human_approval)
        self.assertEqual(len(proposal.payload_hash), 64)
        self.assertFalse(hasattr(proposal, "body"))
        self.assertFalse(hasattr(ChannelRegistry(), "send"))

    def test_channel_registry_rejects_executing_or_misdirected_adapters(self):
        with self.assertRaisesRegex(CommunicationError, "executing capability"):
            ChannelAdapterDescriptor(
                "unsafe-slack", "1.0.0", ChannelKind.SLACK,
                frozenset({"external.publish"}),
            )
        registry = ChannelRegistry()
        with self.assertRaisesRegex(CommunicationError, "canonical dashboard"):
            registry.replace(
                ChannelKind.DASHBOARD,
                registry.descriptor(ChannelKind.DASHBOARD),
            )
        with self.assertRaisesRegex(CommunicationError, "another channel"):
            registry.replace(
                ChannelKind.SLACK,
                registry.descriptor(ChannelKind.EMAIL),
            )


if __name__ == "__main__":
    unittest.main()
