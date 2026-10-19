"""Check the authentication wrapper without a Vercel account or network."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


WRAPPER = Path(__file__).with_name("with_vercel_login.py")


class LoginTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agentenv-login-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        cli = self.root / "vercel"
        cli.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            "print(os.environ['TEST_CLI_RESPONSE'])\n"
            "sys.exit(int(os.environ.get('TEST_CLI_EXIT', '0')))\n"
        )
        cli.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.root),
                        TEST_CLI_RESPONSE=json.dumps({"token": "synthetic-test-token"}))

    def run_wrapper(self, child="pass"):
        return subprocess.run(
            [sys.executable, str(WRAPPER), "--scope", "team", "--project", "project", "--",
             sys.executable, "-c", child],
            env=self.env, capture_output=True, text=True, timeout=5,
        )

    def test_token_only_reaches_child_and_exit_code_survives(self):
        result = self.run_wrapper(
            "import os, sys; assert os.environ['VERCEL_OIDC_TOKEN'] == 'synthetic-test-token'; sys.exit(7)"
        )
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertNotIn("synthetic-test-token", result.stdout + result.stderr)
        self.assertEqual([p.name for p in self.root.iterdir()], ["vercel"])

    def test_cli_failure_gives_action_without_echoing_response(self):
        self.env["TEST_CLI_EXIT"] = "1"
        result = self.run_wrapper("print('CHILD RAN')")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("vercel login", result.stderr)
        self.assertIn("--scope", result.stderr)
        self.assertNotIn("synthetic-test-token", result.stdout + result.stderr)
        self.assertNotIn("CHILD RAN", result.stdout)

    def test_malformed_responses_fail_without_traceback_or_child(self):
        for response in ("not-json", "{}", "null", '[]', '{"token": null}', '{"token": ""}'):
            with self.subTest(response=response):
                self.env["TEST_CLI_RESPONSE"] = response
                result = self.run_wrapper("print('CHILD RAN')")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("vercel login", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("CHILD RAN", result.stdout)


if __name__ == "__main__":
    unittest.main()
