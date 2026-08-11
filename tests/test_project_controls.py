from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProjectControlTests(unittest.TestCase):
    def check_ignored(self, path: str) -> bool:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", path],
            cwd=ROOT,
            check=False,
        )
        return result.returncode == 0

    def test_terraform_generated_and_sensitive_files_are_ignored(self) -> None:
        for path in (
            "deployment/gcp/pilot/.terraform/provider-cache",
            "deployment/gcp/pilot/terraform.tfstate",
            "deployment/gcp/pilot/terraform.tfstate.backup",
            "deployment/gcp/pilot/private.auto.tfvars",
            "deployment/gcp/pilot/private.tfvars.json",
            "deployment/gcp/pilot/review.tfplan",
            "deployment/gcp/pilot/crash.log",
            "deployment/gcp/pilot/local_override.tf",
        ):
            with self.subTest(path=path):
                self.assertTrue(self.check_ignored(path))
        self.assertFalse(
            self.check_ignored("deployment/gcp/pilot/terraform.tfvars.example")
        )
        self.assertFalse(
            self.check_ignored("deployment/gcp/pilot/.terraform.lock.hcl")
        )

    def test_goal_16_and_project_controls_are_registered(self) -> None:
        plan = (ROOT / "docs" / "BUILD_PLAN.md").read_text(encoding="utf-8")
        self.assertIn("16. Pilot operations and project control", plan)
        registry = json.loads(
            (ROOT / "docs" / "requirements" / "registry.json").read_text()
        )
        requirements = {item["id"]: item for item in registry["requirements"]}
        self.assertEqual(requirements["AOS-PM-001"]["status"], "verified")
        self.assertEqual(requirements["AOS-SCM-001"]["status"], "verified")
        self.assertEqual(requirements["AOS-CI-001"]["status"], "implemented")
        self.assertEqual(requirements["AOS-CI-002"]["status"], "implemented")
        for identifier in (
            "AOS-SCM-001",
            "AOS-CI-001",
            "AOS-CI-002",
            "AOS-PILOT-005",
        ):
            self.assertEqual(requirements[identifier]["goal"], 16)
        self.assertEqual(requirements["AOS-PILOT-005"]["status"], "captured")

    def test_executive_operating_contract_is_repository_wide(self) -> None:
        contract = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for control in (
            "Technical Lead and Integrator",
            "President-only escalation",
            "independent, read-only security and release-test review",
            "docs/BUILD_PLAN.md",
            "docs/requirements/registry.json",
            "docs/PROJECT_STATUS.md",
            "docs/operations/project-management.md",
            "Do not end a turn at a status update",
            "safe, reversible, in-scope next task",
            "Continue executing the sequence automatically",
            "resume the gated action and the following safe steps",
            "Stop only for a president-only",
            "spending, publishing, production activation",
            "proactively recommend a fresh Nimbalyst session",
            "Use a sibling session by default",
            "isolated top-level session",
            "Independently choose a new worktree",
            "isolated session without a new worktree still shares",
            "never depend on chat history alone",
        ):
            with self.subTest(control=control):
                self.assertIn(control, contract)

        claude_entrypoint = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("[AGENTS.md](AGENTS.md)", claude_entrypoint)

        runbook = (
            ROOT / "docs" / "operations" / "project-management.md"
        ).read_text(encoding="utf-8")
        self.assertIn("## Executive operating mode", runbook)
        self.assertIn(
            "president, not the daily implementation coordinator", runbook
        )
        self.assertIn("root `AGENTS.md`", runbook)
        self.assertIn("A progress report is not a stopping condition", runbook)
        self.assertIn("continues the ordered sequence", runbook)
        self.assertIn("intermediate gate resumes that action", runbook)
        self.assertIn("manages session boundaries", runbook)
        self.assertIn("context pressure could reduce accuracy", runbook)
        self.assertIn("Sibling sessions are the default", runbook)
        self.assertIn("Worktree choice is independent", runbook)
        self.assertIn("isolated session otherwise inherits", runbook)
        self.assertIn("repository-and-tracker checkpoint", runbook)

    def test_milestone_gate_requires_remote_security_tests_and_backup(self) -> None:
        runbook = (
            ROOT / "docs" / "operations" / "project-management.md"
        ).read_text(encoding="utf-8")
        for control in (
            "remote branch SHA equals local `HEAD`",
            "secret scan",
            "PostgreSQL integration",
            "checksum-verified off-device copy",
            "A local commit is recoverable history, but it is not an off-device",
        ):
            with self.subTest(control=control):
                self.assertIn(control, runbook)

    def test_ci_is_least_privilege_pinned_and_runs_every_release_gate(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("pull-requests: write", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("python -m unittest discover -s tests -v", workflow)
        self.assertIn("python -m unittest tests.test_postgresql_pilot -v", workflow)
        self.assertIn("gitleaks_8.30.1_linux_x64.tar.gz", workflow)
        self.assertIn(
            "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
            workflow,
        )
        self.assertIn("hadolint-linux-x86_64", workflow)
        self.assertIn(
            "c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507",
            workflow,
        )
        self.assertIn("--failure-threshold warning", workflow)
        self.assertNotIn("uses: hadolint/", workflow)
        self.assertIn("sha256sum --check --strict", workflow)
        hadolint_install = re.search(
            r"- name: Install checksum-pinned Hadolint\n"
            r"(?P<body>.*?)(?=\n      - name:)",
            workflow,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(hadolint_install)
        hadolint_body = hadolint_install.group("body")
        for control in (
            'HADOLINT_SHA256: "c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507"',
            'binary="${RUNNER_TEMP}/hadolint"',
            'echo "${HADOLINT_SHA256}  ${binary}" | sha256sum --check --strict',
            'chmod 0500 "${binary}"',
        ):
            with self.subTest(hadolint_control=control):
                self.assertIn(control, hadolint_body)
        self.assertIn('--log-opts="--all"', workflow)
        self.assertIn(
            "postgres@sha256:4e6e670bb069649261c9c18031f0aded7bb249a5b6664ddec29c013a89310d50",
            workflow,
        )
        action_refs = re.findall(r"uses: [^\s]+@([^\s]+)", workflow)
        self.assertGreaterEqual(len(action_refs), 2)
        for action_ref in action_refs:
            with self.subTest(action_ref=action_ref):
                self.assertRegex(action_ref, r"^[0-9a-f]{40}$")

        evaluation = (
            ROOT / "docs" / "reviews" / "goal-16-build-assurance-evaluation.md"
        ).read_text(encoding="utf-8")
        for control in (
            "The only gate added in this slice is Hadolint",
            "Superpowers remains held for execution",
            "binary-only lock with hashes for every direct and transitive wheel",
            "Never scan the live local image",
            "reconcile `AOS-CI-002` status and evidence",
            "current project board and next action",
        ):
            with self.subTest(control=control):
                self.assertIn(control, evaluation)

    def test_zero_cost_private_ci_limitation_is_explicit(self) -> None:
        decision = (
            ROOT
            / "docs"
            / "decisions"
            / "0022-keep-private-github-controls-zero-cost.md"
        ).read_text(encoding="utf-8")
        status = (ROOT / "docs" / "PROJECT_STATUS.md").read_text(encoding="utf-8")
        for control in (
            "do not purchase GitHub Pro",
            "Making the repository public is not an acceptable",
            "never force-pushes or pushes feature work directly to",
            "`AOS-CI-001` remains `implemented`, not `verified`",
        ):
            with self.subTest(control=control):
                self.assertIn(control, decision)
        self.assertIn("Private branch enforcement | amber/held", status)
        self.assertIn("HTTP 403", status)


if __name__ == "__main__":
    unittest.main()
