from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from agent_os.knowledge import (
    KnowledgeCatalog,
    KnowledgeError,
    KnowledgeRecord,
    load_catalog,
    validate_source_inventory,
)


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "knowledge" / "catalog.json"
INVENTORY_PATH = ROOT / "migrations" / "knowledge-source-inventory.json"
NOW = datetime.fromisoformat("2026-07-28T21:00:00-07:00")


class KnowledgeGovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(CATALOG_PATH)
        cls.inventory = validate_source_inventory(
            INVENTORY_PATH,
            catalog=cls.catalog,
        )

    def test_selective_migration_is_candidate_and_research_only(self) -> None:
        self.assertEqual(len(self.catalog.records), 6)
        for record in self.catalog.records:
            self.assertEqual(record.status, "candidate")
            self.assertIsNone(record.reviewed_by)
            self.assertEqual(
                record.retrieval,
                {"research": True, "fact": False, "procedure": False},
            )
            self.assertEqual(record.scope.scope_type, "reference-pack")
            self.assertEqual(record.scope.tenant_key, "northwind-reference")

    def test_research_retrieval_is_purpose_and_scope_bound(self) -> None:
        research = self.catalog.query(
            "Etsy digital listing",
            purpose="research",
            tenant_key="northwind-reference",
            business_key="commerce",
            now=NOW,
        )
        self.assertTrue(research)
        self.assertEqual(
            research[0].record.knowledge_id,
            "northwind-etsy-digital-listing-guardrails",
        )
        for purpose in ("fact", "procedure"):
            self.assertEqual(
                self.catalog.query(
                    "Etsy digital listing",
                    purpose=purpose,
                    tenant_key="northwind-reference",
                    business_key="commerce",
                    now=NOW,
                ),
                [],
            )

        self.assertEqual(
            self.catalog.query(
                "Etsy digital listing",
                purpose="research",
                tenant_key="another-tenant",
                business_key="commerce",
                now=NOW,
            ),
            [],
        )
        self.assertEqual(
            self.catalog.query(
                "Etsy digital listing",
                purpose="research",
                tenant_key="northwind-reference",
                business_key="services",
                now=NOW,
            ),
            [],
        )
        self.assertEqual(
            self.catalog.query(
                "Etsy digital listing",
                purpose="research",
                tenant_key="northwind-reference",
                now=NOW,
            ),
            [],
        )

    def test_stale_record_is_visible_as_research_not_operational_truth(self) -> None:
        hits = self.catalog.query(
            "GHL WhiteLabel workflows",
            purpose="research",
            tenant_key="northwind-reference",
            business_key="services",
            now=NOW,
        )
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].stale)
        self.assertEqual(
            self.catalog.query(
                "GHL WhiteLabel workflows",
                purpose="procedure",
                tenant_key="northwind-reference",
                business_key="services",
                now=NOW,
            ),
            [],
        )

    def test_conflicting_verified_claims_cannot_satisfy_fact_query(self) -> None:
        base = self.catalog.records[0]
        first = replace(
            base,
            knowledge_id="test-claim-first",
            status="verified",
            reviewed_by="human:test-reviewer",
            retrieval={"research": True, "fact": True, "procedure": False},
            claim_key="test-platform-rule",
            claim_value="value-a",
            content="# Test claim first\n\n" + ("Evidence content. " * 12),
        )
        second = replace(
            first,
            knowledge_id="test-claim-second",
            claim_value="value-b",
            content="# Test claim second\n\n" + ("Contrary evidence. " * 12),
        )
        catalog = KnowledgeCatalog([first, second])
        self.assertEqual(
            catalog.conflict_ids(),
            {"test-claim-first", "test-claim-second"},
        )
        self.assertEqual(
            catalog.query(
                "",
                purpose="fact",
                tenant_key="northwind-reference",
                business_key="commerce",
                now=NOW,
            ),
            [],
        )
        audit = catalog.query(
            "",
            purpose="audit",
            tenant_key="northwind-reference",
            business_key="commerce",
            now=NOW,
        )
        self.assertEqual(len(audit), 2)
        self.assertTrue(all(hit.conflicted for hit in audit))

    def test_explicit_conflicts_must_be_symmetric(self) -> None:
        base = self.catalog.records[0]
        first = replace(
            base,
            knowledge_id="explicit-first",
            conflicts_with=("explicit-second",),
        )
        second = replace(
            base,
            knowledge_id="explicit-second",
            conflicts_with=(),
        )
        with self.assertRaisesRegex(KnowledgeError, "symmetric"):
            KnowledgeCatalog([first, second]).conflict_ids()

    def test_promotion_requires_reviewer_and_purpose_permission(self) -> None:
        raw = json.loads(CATALOG_PATH.read_text())["records"][0]
        content = (ROOT / "knowledge" / raw["content_path"]).read_text()

        verified_without_reviewer = dict(raw)
        verified_without_reviewer["status"] = "verified"
        verified_without_reviewer["retrieval"] = {
            "research": True,
            "fact": True,
            "procedure": False,
        }
        from agent_os.knowledge import KnowledgeRecord

        with self.assertRaisesRegex(KnowledgeError, "requires a reviewer"):
            KnowledgeRecord.from_mapping(
                verified_without_reviewer,
                content=content,
            )

        candidate_with_procedure_permission = dict(raw)
        candidate_with_procedure_permission["retrieval"] = {
            "research": True,
            "fact": False,
            "procedure": True,
        }
        with self.assertRaisesRegex(
            KnowledgeError,
            "procedure retrieval|unpromoted",
        ):
            KnowledgeRecord.from_mapping(
                candidate_with_procedure_permission,
                content=content,
            )

    def test_operational_retrieval_requires_compatible_record_kind(
        self,
    ) -> None:
        raw = json.loads(
            (ROOT / "knowledge" / "catalog.json").read_text()
        )["records"][0]
        content = (
            ROOT / "knowledge" / raw["content_path"]
        ).read_text()

        invalid_procedure = dict(raw)
        invalid_procedure["kind"] = "reference"
        invalid_procedure["status"] = "approved-procedure"
        invalid_procedure["reviewed_by"] = "qa"
        invalid_procedure["retrieval"] = {
            "research": True,
            "fact": False,
            "procedure": True,
        }
        with self.assertRaisesRegex(
            KnowledgeError,
            "requires procedure kind",
        ):
            KnowledgeRecord.from_mapping(
                invalid_procedure,
                content=content,
            )

        invalid_fact = dict(raw)
        invalid_fact["kind"] = "strategy"
        invalid_fact["status"] = "verified"
        invalid_fact["reviewed_by"] = "qa"
        invalid_fact["retrieval"] = {
            "research": True,
            "fact": True,
            "procedure": False,
        }
        with self.assertRaisesRegex(KnowledgeError, "fact kind"):
            KnowledgeRecord.from_mapping(
                invalid_fact,
                content=content,
            )

    def test_supersession_requires_target_lifecycle_transition(self) -> None:
        base = self.catalog.records[0]
        replacement = replace(
            base,
            knowledge_id="replacement-record",
            supersedes=("prior-record",),
        )
        prior = replace(base, knowledge_id="prior-record")
        with self.assertRaisesRegex(KnowledgeError, "lifecycle status"):
            KnowledgeCatalog([replacement, prior])

        transitioned = replace(prior, status="superseded")
        catalog = KnowledgeCatalog([replacement, transitioned])
        self.assertEqual(len(catalog.records), 2)

    def test_catalog_rejects_content_path_escape(self) -> None:
        raw = json.loads(CATALOG_PATH.read_text())
        raw["records"] = [dict(raw["records"][0])]
        raw["records"][0]["content_path"] = "../outside.md"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(KnowledgeError, "escapes"):
                load_catalog(path)

    def test_inventory_preserves_exclusions_and_supersession(self) -> None:
        decisions = {
            source["source_path"]: source["decision"]
            for source in self.inventory
        }
        self.assertEqual(
            decisions["knowledge/etsy-shop-playbook.md"],
            "superseded",
        )
        self.assertEqual(
            decisions["projects/northwind/ghl-inbound-build-plan.md"],
            "exclude-sensitive",
        )
        self.assertEqual(
            decisions["knowledge/TIKTOK_WORKFLOW.md"],
            "exclude-wrapper-specific",
        )
        self.assertEqual(
            decisions["memory/ghl/ghl-api-reference.md"],
            "reference-only-needs-refresh",
        )

    def test_migrated_markdown_contains_no_legacy_runtime_or_secret_material(
        self,
    ) -> None:
        prohibited = (
            "/Users/",
            ".openclaw",
            "Bearer ",
            "locationId",
            "chat ID",
            "refresh_token",
            "private network",
        )
        for record in self.catalog.records:
            content = (ROOT / "knowledge" / record.content_path).read_text()
            for pattern in prohibited:
                self.assertNotIn(pattern, content, record.content_path)

    def test_minimum_confidence_is_enforced(self) -> None:
        hits = self.catalog.query(
            "Pinterest affiliate delivery",
            purpose="research",
            tenant_key="northwind-reference",
            business_key="commerce",
            now=NOW,
            minimum_confidence=Decimal("0.75"),
        )
        self.assertEqual(
            [hit.record.knowledge_id for hit in hits],
            ["northwind-pinterest-affiliate-validation"],
        )


if __name__ == "__main__":
    unittest.main()
