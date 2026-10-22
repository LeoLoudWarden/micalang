import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class CliTests(unittest.TestCase):
    def run_cli(self, source: Path, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "mica", str(source)],
            input=stdin,
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )

    def test_root_example(self):
        result = self.run_cli(PROJECT_ROOT / "examples" / "root.mica", "2\n3\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "23\n")

    def test_sub_vm_example(self):
        result = self.run_cli(PROJECT_ROOT / "examples" / "sub_vm.mica")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "23\n\n")

    def test_debug_sub_vm_example(self):
        result = self.run_cli(PROJECT_ROOT / "examples" / "debug_sub_vm.mica")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("'state': 'done'", result.stdout)
        self.assertTrue(result.stdout.endswith("23\n\n"))

    def test_missing_input_is_error(self):
        result = self.run_cli(PROJECT_ROOT / "examples" / "root.mica")
        self.assertEqual(result.returncode, 1)
        self.assertIn("input_error", result.stderr)


if __name__ == "__main__":
    unittest.main()
