from pathlib import Path
import json
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os.agents import (  # noqa: E402
    AGENT_ROOT,
    ConstitutionError,
    load_assignment_manifest,
    load_all_constitutions,
    load_constitution,
    render_agent_prompt,
)


EXPECTED_ROLES = {
    "accounting-controller",
    "acquisition-manager",
    "atlas",
    "business-owner",
    "channel-operator",
    "commerce-operator",
    "creative-producer",
    "customer-success-manager",
    "finance-lead",
    "knowledge-steward",
    "marketing-lead",
    "operations-manager",
    "platform-reliability",
    "product-manager",
    "qa-verifier",
    "research-analyst",
    "sales-manager",
    "software-engineer",
    "strategy-advisor",
}


class AgentConstitutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.constitutions = load_all_constitutions()
        cls.eval_registry = json.loads(
            (AGENT_ROOT / "evals" / "scenarios.json").read_text()
        )
        cls.assignments = json.loads(
            (ROOT / "packs" / "northwind" / "agent-assignments.json").read_text()
        )

    def test_registry_contains_the_complete_role_organization(self) -> None:
        self.assertEqual(set(self.constitutions), EXPECTED_ROLES)
        self.assertEqual(len(self.constitutions), 19)

    def test_every_role_has_a_specific_versioned_soul_and_contract(self) -> None:
        souls = set()
        for constitution_id, constitution in self.constitutions.items():
            role_root = AGENT_ROOT / "roles" / constitution_id
            self.assertTrue((role_root / "CONSTITUTION.json").is_file())
            self.assertTrue((role_root / "SOUL.md").is_file())
            self.assertRegex(constitution.version, r"^\d+\.\d+\.\d+$")
            self.assertGreaterEqual(len(constitution.soul), 160)
            souls.add(constitution.soul)
        self.assertEqual(len(souls), len(self.constitutions))

    def test_constitutions_cannot_grant_model_or_provider_selection(self) -> None:
        provider_names = {
            "anthropic",
            "claude",
            "deepseek",
            "gemini",
            "google",
            "gpt",
            "groq",
            "openai",
            "openrouter",
        }
        for path in (AGENT_ROOT / "roles").glob("*/CONSTITUTION.json"):
            raw = json.loads(path.read_text())
            values = json.dumps(raw["model_requirements"]).lower()
            self.assertFalse(
                any(name in values for name in provider_names),
                f"provider/model name leaked into {path}",
            )
            self.assertNotIn("model", raw["model_requirements"])
            self.assertNotIn("provider", raw["model_requirements"])

    def test_all_roles_are_default_deny_for_external_authority_and_learning(self) -> None:
        for constitution in self.constitutions.values():
            self.assertEqual(
                constitution.autonomy["external_action_default"],
                "policy-engine-required",
            )
            self.assertFalse(constitution.autonomy["may_verify_own_work"])
            self.assertTrue(
                constitution.tool_policy["runtime_allowlist_required"]
            )
            self.assertFalse(constitution.memory_policy["may_promote"])

    def test_executing_roles_route_to_independent_qa(self) -> None:
        for constitution in self.constitutions.values():
            if constitution.autonomy["may_execute"]:
                self.assertIn(
                    "qa-verifier",
                    constitution.handoff_targets,
                    f"{constitution.constitution_id} lacks a QA handoff",
                )

    def test_orchestration_and_assurance_do_not_execute_specialist_work(self) -> None:
        atlas = self.constitutions["atlas"]
        verifier = self.constitutions["qa-verifier"]
        self.assertTrue(atlas.autonomy["work_discovery"])
        self.assertTrue(atlas.autonomy["may_delegate"])
        self.assertFalse(atlas.autonomy["may_execute"])
        self.assertFalse(verifier.autonomy["may_execute"])
        self.assertFalse(verifier.autonomy["may_delegate"])

    def test_accounting_and_finance_are_separate_and_cannot_move_money(self) -> None:
        accounting = self.constitutions["accounting-controller"]
        finance = self.constitutions["finance-lead"]
        self.assertIn(
            "banking.money-movement",
            accounting.tool_policy["forbidden"],
        )
        self.assertIn(
            "banking.money-movement",
            finance.tool_policy["forbidden"],
        )
        self.assertTrue(accounting.autonomy["may_execute"])
        self.assertFalse(finance.autonomy["may_execute"])
        self.assertIn("finance-lead", accounting.handoff_targets)
        self.assertIn("accounting-controller", finance.handoff_targets)

    def test_every_role_has_resolved_happy_and_boundary_evaluations(self) -> None:
        scenarios = self.eval_registry["scenarios"]
        by_id = {scenario["scenario_id"]: scenario for scenario in scenarios}
        self.assertEqual(len(by_id), len(scenarios))
        for constitution in self.constitutions.values():
            self.assertEqual(len(constitution.evaluation_scenarios), 2)
            kinds = set()
            for scenario_id in constitution.evaluation_scenarios:
                self.assertIn(scenario_id, by_id)
                scenario = by_id[scenario_id]
                self.assertEqual(
                    scenario["constitution_id"],
                    constitution.constitution_id,
                )
                self.assertTrue(scenario["expected_behaviors"])
                self.assertTrue(scenario["forbidden_behaviors"])
                kinds.add(scenario["kind"])
            self.assertEqual(kinds, {"happy", "boundary"})

    def test_core_roles_contain_no_client_or_machine_specific_state(self) -> None:
        forbidden = (
            "northwind",
            "chris",
            "/users/",
            ".openclaw",
            "telegram",
            "clickup",
        )
        for path in (AGENT_ROOT / "roles").glob("*/*"):
            text = path.read_text().lower()
            for token in forbidden:
                self.assertNotIn(token, text, f"{token} leaked into {path}")

    def test_northwind_assignments_resolve_without_activating_agents(self) -> None:
        self.assertEqual(
            self.assignments["status"],
            "defined-not-activated",
        )
        businesses = {
            business["business_key"]
            for business in self.assignments["businesses"]
        }
        assignments = self.assignments["assignments"]
        actor_ids = [assignment["actor_id"] for assignment in assignments]
        self.assertEqual(len(actor_ids), len(set(actor_ids)))
        for assignment in assignments:
            self.assertIn(
                assignment["constitution_id"],
                self.constitutions,
            )
            self.assertTrue(set(assignment["business_keys"]) <= businesses)
        self.assertEqual(
            {assignment["constitution_id"] for assignment in assignments},
            EXPECTED_ROLES,
        )
        owners = [
            assignment
            for assignment in assignments
            if assignment["constitution_id"] == "business-owner"
        ]
        self.assertEqual(len(owners), len(businesses))
        self.assertEqual(
            {owner["business_keys"][0] for owner in owners},
            businesses,
        )
        loaded = load_assignment_manifest(
            ROOT / "packs" / "northwind" / "agent-assignments.json",
            constitutions=self.constitutions,
        )
        self.assertEqual(len(loaded), len(assignments))

    def test_northwind_assignments_use_roles_not_legacy_personas(self) -> None:
        deprecated_personas = {
            "alex",
            "aura",
            "echo",
            "kai",
            "koa",
            "leo",
            "muse",
            "nalu",
            "nova",
            "ryan",
            "sage",
        }
        assignments = self.assignments["assignments"]
        for assignment in assignments:
            self.assertNotIn(
                assignment["actor_id"].lower(),
                deprecated_personas,
            )
            self.assertNotIn(
                assignment["display_name"].lower(),
                deprecated_personas,
            )
            if assignment["constitution_id"] != "business-owner":
                self.assertEqual(
                    assignment["actor_id"],
                    assignment["constitution_id"],
                )

    def test_prompt_composition_includes_shared_rules_soul_and_contract(self) -> None:
        atlas = load_constitution("atlas")
        prompt = render_agent_prompt(atlas)
        self.assertIn("Agent OS Shared Agent Constitution", prompt)
        self.assertIn("Atlas — Portfolio Orchestrator", prompt)
        self.assertIn("Machine-readable role contract", prompt)
        self.assertIn('"constitution_id": "atlas"', prompt)

    def test_invalid_or_traversal_constitution_id_is_rejected(self) -> None:
        with self.assertRaises(ConstitutionError):
            load_constitution("../atlas")


if __name__ == "__main__":
    unittest.main()
