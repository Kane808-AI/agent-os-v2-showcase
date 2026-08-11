from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os.affiliate import (  # noqa: E402
    AffiliateShadowError,
    AffiliateShadowLoop,
    ContentDraft,
    Observation,
    OfferSnapshot,
    VerificationDecision,
)
from agent_os.contracts import (  # noqa: E402
    ActorIdentity, ActorType, Business, Objective, ObjectiveStatus, Tenant,
)
from agent_os.dashboard import render_dashboard  # noqa: E402
from agent_os.routing import (  # noqa: E402
    DataClass, ModelCatalogEntry, ModelRouter, ReasoningTier, RouteRequest,
)
from agent_os.shadow_runtime import (  # noqa: E402
    PromptTemplate, ProviderResponse, ShadowModelRuntime, ShadowPrompt,
)
from agent_os.storage import SQLiteStore  # noqa: E402


class Resolver:
    def resolve(self, binding):
        return "test-secret"


class Adapter:
    provider_id = "openai"

    def __init__(self, output):
        self.output = output
        self.calls = 0

    def invoke(self, request, credential):
        self.calls += 1
        return ProviderResponse(self.output, 100, 50, "req-affiliate")


class AffiliateShadowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "affiliate.db")
        self.store.initialize()
        self.now = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
        self.store.upsert_tenant(Tenant("tenant-1", "Tenant One"))
        self.store.upsert_business(Business(
            "business-1", "tenant-1", "Commerce LLC", "Commerce", "USD", "UTC"
        ))
        self.store.upsert_actor(ActorIdentity(
            "commerce-operator", "tenant-1", ActorType.AGENT,
            frozenset({"commerce"}), frozenset({"business-1"}),
        ))
        self.store.upsert_actor(ActorIdentity(
            "qa-verifier", "tenant-1", ActorType.AGENT,
            frozenset({"qa"}), frozenset({"business-1"}),
        ))
        self.store.upsert_actor(ActorIdentity(
            "other-commerce", "tenant-1", ActorType.AGENT,
            frozenset({"commerce"}), frozenset({"business-1"}),
        ))
        self.store.upsert_objective(Objective(
            objective_id="affiliate-objective", tenant_id="tenant-1",
            business_id="business-1", statement="Validate affiliate revenue.",
            metric="affiliate_sales", target=Decimal("10"),
            status=ObjectiveStatus.ACTIVE, review_interval_seconds=3600,
        ), next_review_at=self.now)
        self.store.insert_evidence(
            evidence_id="offer-evidence", tenant_id="tenant-1",
            business_id="business-1", source_type="read_only_offer_snapshot",
            source_ref="fixture:offer", statement="Offer terms were observed.",
            facts={"mode": "historical_replay"}, confidence=Decimal("0.90"),
            observed_at=self.now - timedelta(days=1),
        )
        self.loop = AffiliateShadowLoop(self.store)
        self.run_id = self.loop.start_run(
            objective_id="affiliate-objective", producer_id="commerce-operator", now=self.now
        )
        self.router = ModelRouter(self.store)
        self.router.register_catalog("1.0.0", (ModelCatalogEntry(
            model_id="openai-shadow", provider_id="openai",
            provider_model_ref="openai/gpt-5-2026-07-01",
            reasoning_tier=ReasoningTier.STANDARD, tool_use=False,
            structured_output=True, modalities=frozenset({"text"}),
            context_window_tokens=16000, allowed_data_classes=frozenset(DataClass),
            input_micros_per_million=100000, output_micros_per_million=200000,
            quality_score=90, evaluation_version="affiliate-1",
        ),), created_at=self.now - timedelta(minutes=2))
        self.router.activate_catalog("1.0.0", activation_id="active", activated_at=self.now-timedelta(minutes=1))
        self.router.bind_credential(
            credential_id="cred", tenant_id="tenant-1", business_id="business-1",
            provider_id="openai", credential_ref="env://AFFILIATE_TEST_KEY",
            created_at=self.now-timedelta(seconds=30),
        )
        self.router.revise_provider_policy(
            policy_revision_id="policy", tenant_id="tenant-1", business_id="business-1",
            provider_id="openai", credential_id="cred", enabled=True,
            allowed_data_classes=frozenset(DataClass), monthly_budget_micros=100000,
            created_at=self.now-timedelta(seconds=20),
        )
        self.config = json.loads((ROOT / "packs/northwind/affiliate-shadow-pilot.json").read_text())

    def tearDown(self):
        self.tempdir.cleanup()

    def offer(self, **changes):
        values = dict(
            offer_key="offer-a", source_system="affiliate-network-readonly",
            source_ref="fixture:network:offer-a", merchant_name="Merchant",
            channel="pinterest", destination_url="https://merchant.example/product",
            currency="USD", commission_rate_bps=1500,
            expected_order_value_minor=5000, audience_fit_score=8500,
            evidence_confidence_bps=9000, destination_healthy=True,
            terms_verified=True, disclosure_required="Affiliate link; I may earn a commission.",
            approved_claims=("Designed for small teams",),
            evidence_refs=("offer-evidence",), observed_at=self.now-timedelta(days=1),
        )
        values.update(changes)
        return OfferSnapshot(**values)

    def draft(self, **changes):
        values = dict(
            channel="pinterest", headline="A practical option for small teams",
            body="Review the documented features and decide whether they fit your workflow.",
            disclosure="Affiliate link; I may earn a commission.",
            call_to_action="Review the offer", destination_url="https://merchant.example/product",
            claims=("Designed for small teams",),
        )
        values.update(changes)
        return ContentDraft(**values)

    def prepare_experiment(self, *, minimum_clicks=2):
        self.loop.record_offer(run_id=self.run_id, snapshot=self.offer(), now=self.now)
        recommendation = self.loop.recommend(run_id=self.run_id, now=self.now)
        draft = self.draft()
        adapter = Adapter(json.dumps(draft.payload()))
        route = self.router.route(RouteRequest(
            request_id=f"content-{minimum_clicks}", tenant_id="tenant-1", business_id="business-1",
            reasoning_tier=ReasoningTier.STANDARD, data_class=DataClass.INTERNAL,
            required_modalities=frozenset({"text"}), requires_structured_output=True,
            required_context_tokens=100, estimated_input_tokens=1000, estimated_output_tokens=500,
        ), now=self.now)
        result = ShadowModelRuntime(
            self.store, credential_resolver=Resolver(), adapters=(adapter,)
        ).execute(
            decision_id=route.decision_id,
            prompt=ShadowPrompt(
                PromptTemplate("affiliate-content", "1.0.0", "Draft proposal-only affiliate content."),
                "Draft content for the selected offer.", self.config["content_output_schema"], 300,
            ), now=self.now,
        )
        proposal = self.loop.propose_content(
            recommendation_id=recommendation.recommendation_id,
            shadow_attempt_id=result.attempt_id, draft=draft, now=self.now,
        )
        experiment = self.loop.define_experiment(
            proposal_id=proposal, hypothesis="Clear matching content improves qualified conversion.",
            window_start=self.now-timedelta(days=7), window_end=self.now-timedelta(days=1),
            minimum_clicks=minimum_clicks, now=self.now,
        )
        return experiment

    def observation(self, identity, kind, subject, *, click=None, gross=0, commission=0, hour=1):
        return Observation(
            identity, kind, subject, "analytics-readonly", f"fixture:{identity}",
            self.now-timedelta(days=6)+timedelta(hours=hour), hashlib.sha256(identity.encode()).hexdigest(),
            click, gross, commission,
        )

    def test_complete_shadow_loop_creates_candidate_learning_only(self):
        experiment = self.prepare_experiment()
        for event in (
            self.observation("imp-1", "impression", "visitor-1"),
            self.observation("click-1", "click", "visitor-1", hour=2),
            self.observation("click-2", "click", "visitor-2", hour=2),
            self.observation("conv-1", "conversion", "visitor-1", click="click-1", gross=5000, commission=750, hour=3),
        ):
            self.loop.import_observation(experiment_id=experiment, observation=event, now=self.now)
        measurement = self.loop.measure(experiment_id=experiment, now=self.now)
        self.assertEqual((measurement.click_count, measurement.conversion_count), (2, 1))
        self.assertEqual(measurement.conversion_rate_bps, 5000)
        self.assertEqual(
            self.loop.verify(measurement_id=measurement.measurement_id, verifier_id="qa-verifier", now=self.now),
            VerificationDecision.VERIFIED,
        )
        memory_id = self.loop.learn(measurement_id=measurement.measurement_id, now=self.now)
        snapshot = self.store.dashboard_snapshot()
        memory = next(item for item in snapshot["memories"] if item["memory_id"] == memory_id)
        self.assertEqual(memory["verification_status"], "candidate")
        self.assertIn("not proof of incrementality", memory["statement"])
        self.assertTrue(self.store.schema_status()["migration_valid"])
        backup_path = self.store.create_backup(Path(self.tempdir.name) / "affiliate-backup.db")
        backup = SQLiteStore(backup_path)
        self.assertTrue(backup.schema_status()["migration_valid"])
        self.assertEqual(backup.dashboard_snapshot()["counts"]["affiliate_learnings"], 1)
        self.assertIn("Affiliate shadow loop", render_dashboard(snapshot))

    def test_weak_or_unverified_offers_hold_safely(self):
        self.loop.record_offer(
            run_id=self.run_id,
            snapshot=self.offer(destination_healthy=False, terms_verified=False, audience_fit_score=1000),
            now=self.now,
        )
        result = self.loop.recommend(run_id=self.run_id, now=self.now)
        self.assertEqual(result.status, "held")
        self.assertIn("destination_unhealthy", result.rejection_reasons[result.candidate_order[0]] if result.candidate_order else next(iter(result.rejection_reasons.values())))

    def test_content_cannot_drift_destination_disclosure_or_claims(self):
        self.loop.record_offer(run_id=self.run_id, snapshot=self.offer(), now=self.now)
        recommendation = self.loop.recommend(run_id=self.run_id, now=self.now)
        with self.assertRaisesRegex(AffiliateShadowError, "drifts"):
            self.loop.propose_content(
                recommendation_id=recommendation.recommendation_id,
                shadow_attempt_id="missing", draft=self.draft(destination_url="https://evil.example/"), now=self.now,
            )
        with self.assertRaisesRegex(AffiliateShadowError, "unapproved"):
            self.loop.propose_content(
                recommendation_id=recommendation.recommendation_id,
                shadow_attempt_id="missing", draft=self.draft(claims=("Guaranteed income",)), now=self.now,
            )

    def test_content_requires_matching_successful_goal11_output(self):
        self.loop.record_offer(run_id=self.run_id, snapshot=self.offer(), now=self.now)
        recommendation = self.loop.recommend(run_id=self.run_id, now=self.now)
        with self.assertRaisesRegex(AffiliateShadowError, "Goal 11"):
            self.loop.propose_content(
                recommendation_id=recommendation.recommendation_id,
                shadow_attempt_id="not-real", draft=self.draft(), now=self.now,
            )

    def test_conversion_requires_prior_same_subject_click(self):
        experiment = self.prepare_experiment()
        with self.assertRaisesRegex(AffiliateShadowError, "attribution"):
            self.loop.import_observation(
                experiment_id=experiment,
                observation=self.observation("conv-bad", "conversion", "visitor-x", click="missing", gross=100, commission=10),
                now=self.now,
            )

    def test_insufficient_sample_is_inconclusive_and_cannot_learn(self):
        experiment = self.prepare_experiment(minimum_clicks=5)
        self.loop.import_observation(
            experiment_id=experiment,
            observation=self.observation("click-one", "click", "visitor-1"), now=self.now,
        )
        measurement = self.loop.measure(experiment_id=experiment, now=self.now)
        self.assertEqual(
            self.loop.verify(measurement_id=measurement.measurement_id, verifier_id="qa-verifier", now=self.now),
            VerificationDecision.INCONCLUSIVE,
        )
        with self.assertRaisesRegex(AffiliateShadowError, "verified"):
            self.loop.learn(measurement_id=measurement.measurement_id, now=self.now)

    def test_producer_cannot_verify_own_measurement(self):
        experiment = self.prepare_experiment(minimum_clicks=1)
        self.loop.import_observation(
            experiment_id=experiment,
            observation=self.observation("click-one", "click", "visitor-1"), now=self.now,
        )
        measurement = self.loop.measure(experiment_id=experiment, now=self.now)
        with self.assertRaisesRegex(AffiliateShadowError, "independent scoped QA"):
            self.loop.verify(
                measurement_id=measurement.measurement_id,
                verifier_id="commerce-operator", now=self.now,
            )

    def test_objective_supports_repeated_isolated_shadow_runs(self):
        second_run = self.loop.start_run(
            objective_id="affiliate-objective",
            producer_id="other-commerce",
            now=self.now + timedelta(minutes=1),
        )
        self.assertNotEqual(second_run, self.run_id)
        self.assertEqual(self.store.dashboard_snapshot()["counts"]["affiliate_shadow_runs"], 2)

    def test_sources_must_be_explicitly_read_only(self):
        with self.assertRaisesRegex(AffiliateShadowError, "read-only source"):
            self.offer(source_system="affiliate-network")
        with self.assertRaisesRegex(AffiliateShadowError, "read-only source"):
            Observation(
                "click-write", "click", "visitor", "analytics",
                "fixture:click-write", self.now - timedelta(days=6),
                hashlib.sha256(b"click-write").hexdigest(),
            )

    def test_doctor_rejects_forged_measurement(self):
        experiment = self.prepare_experiment(minimum_clicks=1)
        with self.store._immediate_connection() as connection:
            connection.execute(
                "INSERT INTO affiliate_measurements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "forged-measurement", experiment, "tenant-1", "business-1",
                    9, 9, 9, 10000, 99999, 99999, 1, "0" * 64,
                    self.now.isoformat(),
                ),
            )
        status = self.store.schema_status()
        self.assertFalse(status["migration_valid"])
        self.assertTrue(any(
            "affiliate measurement differs" in error
            for error in status["migration_errors"]
        ))

    def test_pack_and_schema_expose_no_publish_link_contact_or_spend_transition(self):
        self.assertEqual(self.config["status"], "shadow-only")
        self.assertEqual(self.config["mode"], "historical_replay")
        self.assertEqual(
            self.config["offer_evaluator_version"], self.loop.EVALUATOR_VERSION
        )
        self.assertEqual(
            self.config["maximum_offer_age_days"],
            self.loop.MAXIMUM_OFFER_AGE_DAYS,
        )
        self.assertEqual(
            self.config["minimum_evidence_confidence_bps"],
            self.loop.MINIMUM_EVIDENCE_CONFIDENCE_BPS,
        )
        self.assertEqual(
            self.config["minimum_audience_fit_score"],
            self.loop.MINIMUM_AUDIENCE_FIT_SCORE,
        )
        self.assertEqual(set(self.config["forbidden_side_effects"]), {
            "external.publish", "affiliate.link.modify", "partner.contact",
            "ads.spend.execute", "payout.modify",
        })
        with self.store._connection() as connection:
            statuses = {
                row[0] for row in connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name IN ('affiliate_content_proposals','affiliate_experiments')"
                ).fetchall()
            }
        self.assertTrue(all("external" not in sql and "publish" not in sql for sql in statuses))


if __name__ == "__main__":
    unittest.main()
