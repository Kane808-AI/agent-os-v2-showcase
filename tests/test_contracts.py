from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import json
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os.contracts import (  # noqa: E402
    ActionRequest,
    ActorIdentity,
    ActorType,
    AuthorityEnvelope,
    AuthorityMode,
    AuthorityRule,
    Business,
    Event,
    MemoryRecord,
    MemoryType,
    Tenant,
    VerificationStatus,
)


class TenantContractTests(unittest.TestCase):
    def test_business_normalizes_currency(self) -> None:
        business = Business(
            business_id="business-1",
            tenant_id="tenant-1",
            legal_name="Example Company LLC",
            display_name="Example Company",
            base_currency="usd",
            timezone_name="America/Los_Angeles",
        )
        self.assertEqual(business.base_currency, "USD")

    def test_actor_cannot_cross_tenant_or_business_boundary(self) -> None:
        actor = ActorIdentity(
            actor_id="agent-1",
            tenant_id="tenant-1",
            actor_type=ActorType.AGENT,
            roles=frozenset({"business-owner"}),
            business_ids=frozenset({"business-1"}),
        )
        self.assertTrue(
            actor.can_access(tenant_id="tenant-1", business_id="business-1")
        )
        self.assertFalse(
            actor.can_access(tenant_id="tenant-2", business_id="business-1")
        )
        self.assertFalse(
            actor.can_access(tenant_id="tenant-1", business_id="business-2")
        )

    def test_tenant_requires_display_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "display_name"):
            Tenant(tenant_id="tenant-1", display_name="")


class EventContractTests(unittest.TestCase):
    def test_event_requires_timezone(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone"):
            Event(
                event_id="evt-1",
                tenant_id="tenant-1",
                business_id="business-1",
                source="slack",
                actor_id="user-1",
                kind="message.received",
                occurred_at=datetime.now(),
            )

    def test_event_requires_tenant(self) -> None:
        with self.assertRaisesRegex(ValueError, "tenant_id"):
            Event(
                event_id="evt-1",
                tenant_id="",
                business_id="business-1",
                source="dashboard",
                actor_id="user-1",
                kind="goal.created",
                occurred_at=datetime.now(timezone.utc),
            )


class AuthorityContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.envelope = AuthorityEnvelope(
            envelope_id="env-1",
            tenant_id="tenant-1",
            business_id="business-1",
            rules=(
                AuthorityRule(
                    action_type="ad.spend",
                    mode=AuthorityMode.AUTO,
                    platforms=frozenset({"meta"}),
                    accounts=frozenset({"approved-account"}),
                    roles=frozenset({"buyer"}),
                    max_amount=Decimal("25.00"),
                    currency="USD",
                ),
                AuthorityRule(
                    action_type="message.send",
                    mode=AuthorityMode.APPROVE,
                    roles=frozenset({"operator"}),
                ),
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )

    def test_matching_bounded_action_is_auto(self) -> None:
        request = ActionRequest(
            action_type="ad.spend",
            tenant_id="tenant-1",
            business_id="business-1",
            actor_id="growth-agent",
            platform="meta",
            account_id="approved-account",
            amount=Decimal("20.00"),
            currency="USD",
            actor_roles=frozenset({"buyer"}),
        )
        self.assertEqual(self.envelope.decide(request), AuthorityMode.AUTO)

    def test_over_budget_defaults_forbidden(self) -> None:
        request = ActionRequest(
            action_type="ad.spend",
            tenant_id="tenant-1",
            business_id="business-1",
            actor_id="growth-agent",
            platform="meta",
            account_id="approved-account",
            amount=Decimal("25.01"),
            currency="USD",
            actor_roles=frozenset({"buyer"}),
        )
        self.assertEqual(self.envelope.decide(request), AuthorityMode.FORBIDDEN)

    def test_cross_tenant_action_is_forbidden(self) -> None:
        request = ActionRequest(
            action_type="message.send",
            tenant_id="tenant-2",
            business_id="business-1",
            actor_id="sales-agent",
        )
        self.assertEqual(self.envelope.decide(request), AuthorityMode.FORBIDDEN)

    def test_unknown_action_is_forbidden(self) -> None:
        request = ActionRequest(
            action_type="bank.transfer",
            tenant_id="tenant-1",
            business_id="business-1",
            actor_id="finance-agent",
        )
        self.assertEqual(self.envelope.decide(request), AuthorityMode.FORBIDDEN)

    def test_money_movement_remains_forbidden_despite_explicit_auto_rule(
        self,
    ) -> None:
        envelope = AuthorityEnvelope(
            envelope_id="env-dangerous-finance-rule",
            tenant_id="tenant-1",
            business_id="business-1",
            rules=(
                AuthorityRule(
                    action_type="bank.transfer",
                    mode=AuthorityMode.AUTO,
                    roles=frozenset({"finance"}),
                ),
            ),
        )
        request = ActionRequest(
            action_type="bank.transfer",
            tenant_id="tenant-1",
            business_id="business-1",
            actor_id="finance-agent",
            actor_roles=frozenset({"finance"}),
            amount=Decimal("10.00"),
            currency="USD",
        )
        self.assertEqual(envelope.decide(request), AuthorityMode.FORBIDDEN)

    def test_overlapping_forbidden_rule_overrides_broad_auto(self) -> None:
        envelope = AuthorityEnvelope(
            envelope_id="env-overlap",
            tenant_id="tenant-1",
            business_id="business-1",
            rules=(
                AuthorityRule(
                    action_type="ad.spend",
                    mode=AuthorityMode.AUTO,
                    roles=frozenset({"buyer"}),
                ),
                AuthorityRule(
                    action_type="ad.spend",
                    mode=AuthorityMode.FORBIDDEN,
                    roles=frozenset({"buyer"}),
                    accounts=frozenset({"blocked-account"}),
                ),
            ),
        )
        request = ActionRequest(
            action_type="ad.spend",
            tenant_id="tenant-1",
            business_id="business-1",
            actor_id="buyer-1",
            actor_roles=frozenset({"buyer"}),
            account_id="blocked-account",
        )
        self.assertEqual(
            envelope.decide(request),
            AuthorityMode.FORBIDDEN,
        )

    def test_rule_without_actor_entitlement_fails_closed(self) -> None:
        envelope = AuthorityEnvelope(
            envelope_id="env-no-entitlement",
            tenant_id="tenant-1",
            business_id="business-1",
            rules=(
                AuthorityRule(
                    action_type="message.send",
                    mode=AuthorityMode.AUTO,
                ),
            ),
        )
        request = ActionRequest(
            action_type="message.send",
            tenant_id="tenant-1",
            business_id="business-1",
            actor_id="operator-1",
            actor_roles=frozenset({"operator"}),
        )
        self.assertEqual(
            envelope.decide(request),
            AuthorityMode.FORBIDDEN,
        )


class MemoryContractTests(unittest.TestCase):
    def test_confidence_is_bounded(self) -> None:
        now = datetime.now(timezone.utc)
        with self.assertRaisesRegex(ValueError, "confidence"):
            MemoryRecord(
                memory_id="mem-1",
                tenant_id="tenant-1",
                business_id="business-1",
                memory_type=MemoryType.SEMANTIC,
                statement="Unsupported claim",
                source_type="inference",
                source_ref="run-1",
                confidence=Decimal("1.1"),
                verification_status=VerificationStatus.CANDIDATE,
                created_at=now,
                observed_at=now,
            )


class MigrationInventoryTests(unittest.TestCase):
    def test_all_legacy_systems_have_distinct_names_and_policies(self) -> None:
        inventory_path = ROOT / "migrations" / "legacy-systems.json"
        inventory = json.loads(inventory_path.read_text())
        ids = [system["id"] for system in inventory["systems"]]
        names = [system["display_name"] for system in inventory["systems"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(names), len(set(names)))
        for system in inventory["systems"]:
            self.assertTrue(system["path"].startswith("/Users/operator/"))
            self.assertTrue(system["policy"])


class ProductBoundaryTests(unittest.TestCase):
    def test_kernel_contains_no_client_or_machine_specific_identifiers(self) -> None:
        forbidden = ("Northwind", "OpenClaw", "/Users/operator")
        kernel_text = "\n".join(
            path.read_text()
            for path in (ROOT / "src" / "agent_os").rglob("*.py")
        )
        for term in forbidden:
            self.assertNotIn(term, kernel_text)


if __name__ == "__main__":
    unittest.main()
