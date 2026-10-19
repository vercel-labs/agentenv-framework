"""Exercise the launcher process boundary without contacting Vercel."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


LAUNCHER = Path(__file__).resolve().parents[2] / "vercel-eval"


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vercel launcher test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "example repo"
        self.repo.mkdir()
        self.launcher = self.repo / "vercel-eval"
        shutil.copy2(LAUNCHER, self.launcher)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("python3", "vercel"):
            (self.bin / name).symlink_to(sys.executable)
        self.env = dict(os.environ, PATH=str(self.bin), TMPDIR=str(self.root),
                        AGENT_ENV_CONFIG="/unrelated/config.toml")

    def install(self):
        (self.repo / ".venv/bin").mkdir(parents=True)
        (self.repo / ".venv/bin/python").symlink_to(sys.executable)
        example = self.repo / "examples/vercel-sandbox"
        example.mkdir(parents=True)
        (example / "with_vercel_login.py").write_text(
            "import json, os, sys\n"
            "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), "
            "'config': os.environ['AGENT_ENV_CONFIG']}))\n"
            "sys.exit(int(os.environ.get('TEST_CHILD_EXIT', '0')))\n"
        )

    def run_launcher(self, *args):
        return subprocess.run([str(self.launcher), *args], cwd=self.root,
                              env=self.env, capture_output=True, text=True)

    def test_help_without_install(self):
        result = self.run_launcher("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--project", result.stdout)
        self.assertIn("temporary directory", result.stdout)
        self.assertEqual(list(self.root.glob("agentenv-vercel-*")), [])

    def test_missing_install(self):
        result = self.run_launcher("--scope", "team", "--project", "project")
        self.assertEqual(result.returncode, 2)
        self.assertIn("uv sync --frozen --extra vercel", result.stderr)
        self.assertEqual(list(self.root.glob("agentenv-vercel-*")), [])

    def test_missing_cli(self):
        self.install()
        (self.bin / "vercel").unlink()
        result = self.run_launcher("--scope", "team", "--project", "project")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Install Vercel CLI", result.stderr)

    def test_launch_from_other_directory_and_unique_results(self):
        self.install()
        outputs = []
        for _ in range(2):
            result = self.run_launcher("--scope", "team", "--project", "project")
            self.assertEqual(result.returncode, 0, result.stderr)
            location, target, payload = result.stdout.splitlines()
            self.assertIn("team/project", target)
            output = Path(location.removeprefix("Results: "))
            self.assertFalse(output.exists())
            self.assertTrue(output.parent.is_dir())
            outputs.append(output)
            child = json.loads(payload)
            example = self.repo / "examples/vercel-sandbox"
            self.assertEqual(child['config'], str(example / "config.toml"))
            self.assertEqual(child['cwd'], str(self.repo))
            self.assertEqual(child['argv'], ["--scope", "team", "--project", "project", "--",
                str(self.repo / ".venv/bin/python"), str(example / "run_e2e.py"),
                "--repo", str(self.repo), "--output", str(output)])
        self.assertNotEqual(*outputs)

    def test_child_failure_exit_code(self):
        self.install()
        self.env['TEST_CHILD_EXIT'] = '7'
        result = self.run_launcher("--scope", "team", "--project", "project")
        self.assertEqual(result.returncode, 7)


if __name__ == "__main__":
    unittest.main()
