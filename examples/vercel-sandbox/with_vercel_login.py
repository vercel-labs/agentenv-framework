"""Run a command with project OIDC from the existing Vercel CLI login."""

import argparse
import json
import os
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--project", required=True)
parser.add_argument("--scope", required=True)
parser.add_argument("command", nargs=argparse.REMAINDER)
args = parser.parse_args()
command = args.command[1:] if args.command[:1] == ["--"] else args.command
if not command:
    parser.error("a command after -- is required")
try:
    result = subprocess.run(
        ["vercel", "project", "token", args.project, "--scope", args.scope, "--json"],
        capture_output=True, text=True, timeout=60,
    )
except subprocess.TimeoutExpired:
    raise SystemExit("Vercel authentication timed out. Check your connection and run the command again.") from None
if result.returncode:
    raise SystemExit(
        "Vercel project authentication failed. Run vercel login, then check that "
        "--scope is your team slug and --project is a project you can access. "
        "This example requires Vercel CLI 62.5.0 or newer."
    )
try:
    token = json.loads(result.stdout)["token"]
except (ValueError, KeyError, TypeError):
    raise SystemExit("The Vercel CLI did not return a token. Update Vercel CLI and run vercel login again.") from None
if not isinstance(token, str) or not token:
    raise SystemExit("The Vercel CLI did not return a token. Run vercel login again.")
environment = dict(os.environ, VERCEL_OIDC_TOKEN=token)
print("Using project OIDC from your Vercel CLI login.", flush=True)
raise SystemExit(subprocess.run(command, env=environment).returncode)
