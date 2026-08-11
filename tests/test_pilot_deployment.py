from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PilotDeploymentContractTests(unittest.TestCase):
    def test_terraform_requires_new_project_and_immutable_inputs(self):
        variables = (ROOT / "deployment/gcp/pilot/variables.tf").read_text()
        self.assertIn('var.project_id != "openclaw-legacy-000000"', variables)
        self.assertIn('can(regex("agent-os-v2"', variables)
        self.assertIn('@sha256:[0-9a-f]{64}$', variables)
        self.assertEqual(variables.count('never latest'), 2)
        self.assertIn('variable "deploy_runtime"', variables)

    def test_terraform_has_no_public_invoker_or_secret_values(self):
        main = (ROOT / "deployment/gcp/pilot/main.tf").read_text()
        self.assertNotIn("allUsers", main)
        self.assertNotIn("google_secret_manager_secret_version", main)
        self.assertNotIn("password", main.lower())
        self.assertIn('max_retries     = 0', main)
        self.assertNotIn("scheduler", main.lower())
        self.assertEqual(main.count("count               = var.deploy_runtime ? 1 : 0"), 2)
        self.assertEqual(main.count("condition     = local.runtime_inputs_ready"), 2)

    def test_database_duties_kms_and_recovery_are_separate(self):
        main = (ROOT / "deployment/gcp/pilot/main.tf").read_text()
        for identity in ('"runtime"', '"migration"', '"backup"'):
            self.assertIn(identity, main)
        self.assertIn('purpose         = "ASYMMETRIC_SIGN"', main)
        self.assertIn('point_in_time_recovery_enabled = true', main)
        self.assertIn('deletion_protection = true', main)
        self.assertIn('connector_enforcement       = "REQUIRED"', main)
        self.assertIn('deletion_policy = "PREVENT"', main)
        self.assertGreaterEqual(main.count('prevent_destroy = true'), 2)
        self.assertGreaterEqual(main.count('"roles/cloudsql.client"'), 2)

    def test_container_is_pinned_unprivileged_and_has_two_entrypoints(self):
        dockerfile = (ROOT / "deployment/container/Dockerfile.pilot").read_text()
        self.assertIn("python@sha256:", dockerfile)
        self.assertIn("USER 65532:65532", dockerfile)
        self.assertIn("agent_os.pilot_service", dockerfile)
        self.assertNotIn("--editable", dockerfile)
        project = (ROOT / "pyproject.toml").read_text()
        self.assertIn("agent-os-pilot-service", project)
        self.assertIn("agent-os-pilot-canary", project)
        self.assertIn('"share/agent-os/deployment/postgresql"', project)
        self.assertIn('psycopg[binary]==3.3.4', project)

    def test_pilot_runtime_has_no_external_account_or_publish_client(self):
        source = "\n".join(
            (ROOT / "src/agent_os" / name).read_text()
            for name in ("pilot_canary.py", "pilot_service.py", "postgresql.py")
        ).lower()
        for forbidden in (
            "requests", "urllib", "boto", "pinterest api", "amazon api",
            "publish(", "message.send", "ads.",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
