"""Offline CLI subprocess checks, including execution outside the repository.

Only help, version and inputs rejected before network access are exercised.
Summary and full JSON success paths are covered with fixtures in regress_trace.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "scripts" / "trace.py"


class CliSmoke(unittest.TestCase):
    def invoke(self, *args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        with tempfile.TemporaryDirectory(prefix="video-tracer-cli-") as cwd:
            return subprocess.run(
                [sys.executable, str(ENTRY), *args], cwd=cwd, env=env,
                capture_output=True, text=True, encoding="utf-8",
                errors="strict", timeout=30,
            )

    def test_help_explains_json_and_budget(self):
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        text = "".join(result.stdout.split())
        for phrase in ("--json", "--version", "不含初始读取", "并非严格超时"):
            self.assertIn(phrase, text)

    def test_version_from_unrelated_directory(self):
        result = self.invoke("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout.strip(), r"^trace\.py \d+\.\d+\.\d+\S*$")
        self.assertEqual(result.stderr, "")

    def test_no_arguments_is_usage_error(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_target_is_not_a_network_request(self):
        result = self.invoke("not-a-video")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("BV", result.stderr)
        self.assertIn("not-a-video", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_budget_is_rejected_before_probe(self):
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value):
                result = self.invoke("not-a-video", "--budget", value)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--budget", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_invalid_request_budget_is_rejected_before_probe(self):
        for value in ("0", "-1", "1.5"):
            with self.subTest(value=value):
                result = self.invoke("not-a-video", "--max-requests", value)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--max-requests", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_skill_entry_examples_request_json(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        commands = [line for line in skill.splitlines()
                    if line.startswith("python scripts/trace.py ")]
        self.assertTrue(commands)
        self.assertTrue(all("--json" in line.split() for line in commands))
        self.assertIn("not a strict end-to-end timeout", skill)
        self.assertIn("run the following commands from", skill)


if __name__ == "__main__":
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(CliSmoke))
    raise SystemExit(0 if result.wasSuccessful() else 1)
