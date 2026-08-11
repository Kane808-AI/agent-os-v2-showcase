from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_os.pilot_canary import (  # noqa: E402
    PilotCanaryError,
    load_report,
    read_secret_file,
    validate_report,
)
from agent_os.pilot_service import build_store_from_environment  # noqa: E402


class PilotCanaryContractTests(unittest.TestCase):
    def fixture(self):
        return json.loads(
            (ROOT / "deployment/reference/pilot-canary.example.json").read_text()
        )

    def test_reference_contract_is_strict_and_read_only(self):
        report = validate_report(self.fixture())
        self.assertEqual(report["pinterest"]["impressions"], 0)
        self.assertEqual(report["mode"], "read_only")

        executing = self.fixture()
        executing["mode"] = "publish"
        with self.assertRaisesRegex(PilotCanaryError, "read_only"):
            validate_report(executing)

        excess = self.fixture()
        excess["destination_url"] = "https://example.invalid"
        with self.assertRaisesRegex(PilotCanaryError, "fields"):
            validate_report(excess)

    def test_independent_verifier_and_integer_counts_are_required(self):
        report = self.fixture()
        report["verifier_id"] = report["producer_id"]
        with self.assertRaisesRegex(PilotCanaryError, "independent"):
            validate_report(report)
        report = self.fixture()
        report["amazon"]["conversions"] = True
        with self.assertRaisesRegex(PilotCanaryError, "integer"):
            validate_report(report)

    def test_report_loader_is_bounded_and_dsn_stays_in_secret_file(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            report_path = directory / "report.json"
            report_path.write_text(json.dumps(self.fixture()))
            self.assertEqual(load_report(str(report_path))["schema_version"], 1)
            secret_path = directory / "dsn"
            secret_path.write_text("postgresql://runtime:secret@db/pilot\n")
            self.assertEqual(
                read_secret_file(str(secret_path)),
                "postgresql://runtime:secret@db/pilot",
            )

    def test_service_refuses_to_start_without_authenticated_edge(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "IAM"):
                build_store_from_environment()

    def test_service_constructs_only_a_scoped_store(self):
        with tempfile.TemporaryDirectory() as directory:
            secret_path = Path(directory) / "dsn"
            secret_path.write_text("postgresql://runtime:secret@db/pilot")
            environment = {
                "AOS_AUTH_PROXY": "cloud-run-iam",
                "AOS_POSTGRES_DSN_FILE": str(secret_path),
                "AOS_TENANT_ID": "tenant-1",
                "AOS_BUSINESS_ID": "business-1",
            }
            with patch.dict(os.environ, environment, clear=True):
                store = build_store_from_environment()
            self.assertEqual(store.tenant_id, "tenant-1")
            self.assertEqual(store.business_id, "business-1")
            self.assertNotIn("secret", repr(store))

    def test_timestamps_require_timezone(self):
        report = self.fixture()
        report["observed_at"] = datetime.now().isoformat()
        with self.assertRaisesRegex(PilotCanaryError, "timezone"):
            validate_report(report)


if __name__ == "__main__":
    unittest.main()
