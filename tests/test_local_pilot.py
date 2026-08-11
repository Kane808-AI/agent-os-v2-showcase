from pathlib import Path
import stat
import tempfile
import unittest

from agent_os.local_pilot import (
    DEFAULT_MAXIMUM_DATABASE_BYTES,
    DEFAULT_MINIMUM_FREE_BYTES,
    evaluate_storage_guard,
    initialize_secrets,
    rotate_backups,
)


ROOT = Path(__file__).resolve().parents[1]


class LocalPilotTests(unittest.TestCase):
    def test_storage_guard_holds_before_host_or_database_is_full(self):
        self.assertTrue(evaluate_storage_guard(
            free_bytes=DEFAULT_MINIMUM_FREE_BYTES,
            database_bytes=DEFAULT_MAXIMUM_DATABASE_BYTES,
        ).allowed)
        low_host = evaluate_storage_guard(
            free_bytes=DEFAULT_MINIMUM_FREE_BYTES - 1,
            database_bytes=0,
        )
        self.assertFalse(low_host.allowed)
        self.assertIn("host free space", low_host.reasons[0])
        large_database = evaluate_storage_guard(
            free_bytes=DEFAULT_MINIMUM_FREE_BYTES,
            database_bytes=DEFAULT_MAXIMUM_DATABASE_BYTES + 1,
        )
        self.assertFalse(large_database.allowed)
        self.assertIn("size ceiling", large_database.reasons[0])

    def test_local_secrets_are_complete_private_and_not_rotated_silently(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "secrets"
            created = initialize_secrets(directory)
            self.assertTrue(created["created"])
            before = {name: (directory / name).read_bytes() for name in created["files"]}
            repeated = initialize_secrets(directory)
            self.assertFalse(repeated["created"])
            self.assertEqual(
                before,
                {name: (directory / name).read_bytes() for name in created["files"]},
            )
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            for name in created["files"]:
                self.assertEqual(stat.S_IMODE((directory / name).stat().st_mode), 0o600)

    def test_backup_rotation_keeps_seven_newest(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for index in range(9):
                (directory / f"agent-os-local-pilot-{index:02d}.dump").write_bytes(b"x")
            result = rotate_backups(directory)
            self.assertEqual(result["kept"], 7)
            self.assertEqual(len(result["removed"]), 2)
            self.assertEqual(len(list(directory.glob("*.dump"))), 7)

    def test_shell_boundary_has_no_host_port_or_automatic_canary(self):
        script = (ROOT / "scripts/local_pilot.sh").read_text()
        docker_ignore = (ROOT / ".dockerignore").read_text().splitlines()
        self.assertIn("data", docker_ignore)
        self.assertIn("docker network create --internal", script)
        self.assertNotIn("--publish", script)
        self.assertIn("--memory=512m", script)
        self.assertIn("--log-opt max-size=10m", script)
        self.assertIn("maximum_database_bytes", script)
        self.assertNotIn("scheduler", script.lower())


if __name__ == "__main__":
    unittest.main()
