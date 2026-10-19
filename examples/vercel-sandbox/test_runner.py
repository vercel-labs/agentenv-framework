"""Exercise local cancellation of the remote image-build response."""

import asyncio
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import run_e2e


class BuildDeadlineTest(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_build_response_is_cancelled_at_local_deadline(self):
        cancelled = asyncio.Event()

        async def stalled(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        builder = SimpleNamespace(_sandbox=SimpleNamespace(run_process=stalled))
        with patch.object(run_e2e, "BUILD_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(TimeoutError):
                await run_e2e.run_image_build(builder, io.StringIO(), io.StringIO())
        self.assertTrue(cancelled.is_set())


class SourceArchiveTest(unittest.TestCase):
    def test_only_tracked_source_is_copied_with_local_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            output = Path(directory) / "output"
            output.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "source.py").write_text("original")
            (repo / ".env.test").write_text("tracked-secret-sentinel")
            subprocess.run(["git", "-C", str(repo), "add", "source.py", ".env.test"], check=True)
            (repo / "source.py").write_text("local edit")
            (repo / "credentials.json").write_text("untracked-secret-sentinel")
            run_e2e.snapshot_sources(repo, output)
            with tarfile.open(output / "source.tar.gz") as archive:
                self.assertEqual(archive.getnames(), ["source.py"])
                self.assertEqual(archive.extractfile("source.py").read(), b"local edit")
            self.assertEqual(list(json.loads((output / "source-hashes.json").read_text())), ["source.py"])


class ClientCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_close_errors_and_stalls_preserve_result_and_close_other_clients(self):
        async def stalled():
            await asyncio.Event().wait()

        for primary_error in (None, RuntimeError("original failure")):
            with self.subTest(primary_error=primary_error):
                clients = [SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("close failed"))),
                           SimpleNamespace(close=AsyncMock(side_effect=stalled)),
                           SimpleNamespace(close=AsyncMock())]
                async def operation():
                    try:
                        if primary_error:
                            raise primary_error
                        return "passed"
                    finally:
                        await run_e2e.close_clients(clients)
                with patch.object(run_e2e, "CLIENT_CLOSE_TIMEOUT_SECONDS", 0.01, create=True):
                    if primary_error:
                        with self.assertRaisesRegex(RuntimeError, "original failure"):
                            await asyncio.wait_for(operation(), 0.5)
                    else:
                        self.assertEqual(await asyncio.wait_for(operation(), 0.5), "passed")
                for client in clients:
                    client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
