"""Build fixtures and run a scored AgentEnv task on Vercel; retain any failures."""

import argparse
import asyncio
import copy
import hashlib
import hmac
import importlib.metadata
import json
import logging
import subprocess
import tarfile
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlencode

import httpx
from agentenv_protocol import client as protocol_v1
from agent_env.a2a_agent import A2AAgent
from agent_env.artifact import DockerImageArtifact, EnvironmentArtifact, FileArtifact
from agent_env.config import configure
from agent_env.env import Env, GatewayEnv, legacy_protocol
from agent_env.env.envs.mcp_server import MCPServerEnv
from agent_env.env.envs.service_db import ServiceDBEnv
from agent_env.providers.sandbox_providers.sandbox import NetworkMode, NetworkPolicy
from agent_env.providers.sandbox_providers.vercel import VercelSandbox, VercelSandboxProvider
from agent_env.store.document_store import LocalSqliteDocumentStore
from agent_env.store.object_store import LocalFilesystemObjectStore
from agent_env.task import Task
from agent_env.task.teardown import teardown_run
from agent_env.providers.sandbox_providers.sandbox_provider import SandboxNotFoundError
from agent_env.task_step.task_steps.teardown_sandboxes import TORN_DOWN_KEY
from agent_env.task_step.task_steps.deploy_agent import DeployAgentTaskStep
from agent_env.task_step.task_steps.deploy_env import DeployEnvTaskStep
from agent_env.task_step.task_steps.load_artifact import LoadArtifactTaskStep
from agent_env.task_step.task_steps.prompt_agent import PromptAgentTaskStep
from agent_env.task_step.task_steps.verifiers.agent_prompt_response_verifier import AgentPromptResponseVerifierTaskStep

HERE = Path(__file__).resolve().parent
KEY = b"agentenv-poc-public-image-fixture"
BUILD_TIMEOUT_SECONDS = 1830
CLIENT_CLOSE_TIMEOUT_SECONDS = 5


async def build_progress(started):
    while True:
        await asyncio.sleep(30)
        print(f"Preparing images... {int(time.monotonic() - started)}s elapsed. Build logs are in the results folder.", flush=True)


async def run_image_build(builder, stdout, stderr):
    async with asyncio.timeout(BUILD_TIMEOUT_SECONDS):
        return await builder._sandbox.run_process(
            "bash", ["-lc", (HERE / "build_images.sh").read_text()],
            sudo=True, kill_after=1800, stdout=stdout, stderr=stderr)


async def close_clients(clients):
    async def close(client):
        try:
            async with asyncio.timeout(CLIENT_CLOSE_TIMEOUT_SECONDS):
                await client.close()
        except Exception as error:
            logging.getLogger(__name__).warning("Client cleanup failed: %s: %s", type(error).__name__, error)

    await asyncio.gather(*(close(client) for client in clients))


def snapshot_sources(repo, output):
    tracked = subprocess.check_output(
        ["git", "-C", str(repo), "ls-files", "-z", "--cached"]
    ).decode().split("\0")
    tracked = [name for name in tracked if name and (repo / name).is_file()
               and not any(part.startswith(".env") for part in Path(name).parts)]
    (output / "source-hashes.json").write_text(json.dumps(
        {name: hashlib.sha256((repo / name).read_bytes()).hexdigest() for name in tracked}, indent=2) + "\n")
    with tarfile.open(output / "source.tar.gz", "w:gz") as archive:
        for name in tracked:
            archive.add(repo / name, arcname=name, recursive=False)


async def main():
    if not __debug__:
        raise SystemExit("Run without Python -O; this validation requires assertions")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo, output = args.repo.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    logging.basicConfig(level=logging.INFO, handlers=[
        logging.FileHandler(output / "run.log"), console,
    ])
    def save(name, value):
        (output / name).write_text(json.dumps(value, indent=2, default=str) + "\n")

    version = importlib.metadata.version("agentenv-framework")
    save("packages.json", {name: importlib.metadata.version(name) for name in (
        "agentenv-framework", "agentenv-framework-protocol", "vercel-sandbox")})
    print(f"AgentEnv {version}. This demo uses a deterministic agent to test the integration.", flush=True)
    snapshot_sources(repo, output)

    resources, owned, clients = [], [], []
    phase = "build"
    cleanup = False
    completed = False
    create_vm = VercelSandboxProvider.create_vm
    terminate = VercelSandbox.terminate

    def record_resource(box, *, setup_failed=False):
        if not any(known.sandbox_id == box.sandbox_id for known in owned):
            owned.append(box)
            resources.append({"name": box.sandbox_id, "session_id": box._sandbox.current_session_id,
                              "phase": phase, "routes": box.tunnel_urls, "created_at": time.time(),
                              "setup_failed": setup_failed})
            save("resources.json", resources)

    async def tracked_create(provider, **kwargs):
        if provider not in clients:
            clients.append(provider)
        box = await create_vm(provider, **kwargs)
        record_resource(box)
        if phase == "task":
            await box.apply_network_policy(NetworkPolicy(
                mode=NetworkMode.ALLOWLIST, allow_hosts=("pypi.org", "*.vercel.run")))
        print("CREATED", phase, box.sandbox_id, flush=True)
        return box

    async def guarded_terminate(box):
        if cleanup:
            return await terminate(box)
        record_resource(box, setup_failed=True)
        print("RETAINING until assertions pass:", box.sandbox_id, flush=True)

    VercelSandboxProvider.create_vm = tracked_create
    VercelSandbox.terminate = guarded_terminate
    controller = VercelSandboxProvider.from_config()
    clients.append(controller)
    try:
        print("[1/3] Preparing six images in Vercel Sandbox. The first build can take several minutes.", flush=True)
        builder = await controller.create_vm(cpu=4, memory=8192, disk_size_gb=64, exposed_ports=[8080], timeout=2400)
        for source, target in [(output / "source.tar.gz", "/tmp/source.tar.gz"),
                               (HERE / "fixture_agent.py", "/tmp/fixture_agent.py"),
                               (HERE / "artifact_server.py", "/tmp/artifact_server.py")]:
            await builder.write_host_file(source.read_bytes(), target)
        with (output / "build.stdout").open("w") as stdout, (output / "build.stderr").open("w") as stderr:
            progress = asyncio.create_task(build_progress(time.monotonic()))
            try:
                result = await run_image_build(builder, stdout, stderr)
            finally:
                progress.cancel()
                await asyncio.gather(progress, return_exceptions=True)
        save("build-command.json", {"command_id": result.id, "session_id": result.session_id,
                                    "exit_code": result.returncode})
        assert result.returncode == 0, "Image build failed; see build logs and retained builder"
        manifest = json.loads(await builder.exec_script("cat /tmp/poc-artifacts/manifest.json"))
        assert len(manifest) == 6
        save("images.json", manifest)
        await builder.exec_script("nohup python3 /tmp/artifact_server.py >/tmp/artifact-server.log 2>&1 </dev/null &")
        base = builder.tunnel_urls[8080]
        def signed(name):
            expires = str(int(time.time()) + 2400)
            signature = hmac.new(KEY, (name + ":" + expires).encode(), hashlib.sha256).hexdigest()
            return base + "/" + name + "?" + urlencode({"expires": expires, "signature": signature})
        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.get(signed("manifest.json"))
            response.raise_for_status()
            assert response.json() == manifest
        print("PASS six image builds and signed fixture downloads", flush=True)

        class FixtureStore(LocalFilesystemObjectStore):
            def signed_get_url(self, url, expires_in=3600):
                key = self.get_object_key(url)
                return signed(key) if key in {name + ".tar.gz" for name in manifest} else None

        store = FixtureStore(root=str(output / "objects"), grants="off")
        configure(object_store=store, document_store=LocalSqliteDocumentStore(str(output / "state.sqlite")))
        def image(name):
            return DockerImageArtifact.put_tar(
                name, description="Disposable review fixture", image_name=name,
                tar_gz_object_url=store.object_url(name + ".tar.gz"))
        GatewayEnv.put(id="default", docker_image_artifact=image("poc-gateway"))
        ServiceDBEnv.put(id="default-db", db_docker_image_artifact=image("poc-db"),
                        db_web_docker_image_artifact=image("poc-db-web"),
                        db_mcp_docker_image_artifact=image("poc-db-mcp"))
        env = MCPServerEnv.put(id="review-items", environment_name="items",
                              docker_image_artifact=image("poc-items"), env_provider_type="gateway")
        agent = A2AAgent.put(id="review-agent", docker_image_artifact=image("poc-agent"),
                            default_env_vars={"LITELLM_API_KEY": "unused", "LITELLM_BASE_URL": "http://unused.invalid"})
        seed = EnvironmentArtifact.put(
            id="review-seed", environment_name="items",
            file_artifact=FileArtifact.put_bytes(id="review-items-json", description="Initial review state",
                                                filename="items.json", content=b'{"items":["starting-item"]}',
                                                content_type="application/json"))
        task = Task.put(id="vercel-review", steps=[
            DeployEnvTaskStep(id="environment", version=None, env_id=env.id, env_version=env.version,
                              sandbox_type="vercel", ttl_seconds=1800, cpu=4, memory_mb=8192, disk_size_gb=64),
            LoadArtifactTaskStep(id="seed", version=None, env_id=env.id, artifact_id=seed.id,
                                 artifact_version=seed.version, snapshot_after_load=False),
            DeployAgentTaskStep(id="agent", version=None, env_ids=[env.id], a2a_agent_id=agent.id,
                                a2a_agent_version=agent.version, sandbox_type="vercel",
                                ttl_seconds=1800, cpu=2, memory_mb=4096, disk_size_gb=64),
            PromptAgentTaskStep(id="ask", version=None, prompt="vercel-review-complete", prompt_id="ask",
                                timeout_seconds=120),
            AgentPromptResponseVerifierTaskStep(id="grade", version=None, prompt_id="ask", verifier_id="result",
                                                criteria=[{"type": "response_contains", "needles": [
                                                    "starting-item", "vercel-review-complete"]}]),
        ])
        save("task.json", task.to_dict())
        phase = "task"
        print("[2/3] Running the evaluation: deploy environment, load data, deploy agent, call tools, score.", flush=True)
        def step_done(index, total, step, context, elapsed):
            print(f"STEP {index + 1}/{total} {step.id} completed in {elapsed:.1f}s", flush=True)
            save("context.json", context.to_safe_dict())
        context = await task.run(on_step_complete=step_done)
        save("context.json", context.to_safe_dict())
        instance = Task.get_instance(context.instance_id)
        save("task-instance.json", asdict(instance))
        assert not context.metadata.get("failed_steps"), context.metadata.get("failed_steps")
        assert instance.status == "completed" and len(instance.completed_steps) == 5
        score = context.metadata["verifications"]["result"]["score"]
        assert score == 1, score
        assert len(context.deployed_envs) == len(context.deployed_agents) == 1
        deployed_env, deployed_agent = context.deployed_envs[0], context.deployed_agents[0]
        assert deployed_env.sandbox_type == deployed_agent.sandbox_type == "vercel"
        assert deployed_env.sandbox_id != deployed_agent.sandbox_id
        trajectory = context.prompt_responses[0].agent_trajectory_object_url
        assert trajectory and store.get(trajectory), "Agent trajectory was not collected"
        (output / "trajectory.json").write_bytes(store.get(trajectory))
        print("PASS actual Task.run: five steps, score 1, trajectory collected", flush=True)

        fresh = VercelSandboxProvider.from_config()
        clients.append(fresh)
        for box in owned[1:]:
            reconnected = await fresh.get_sandbox(box.sandbox_id)
            assert reconnected.tunnel_urls == box.tunnel_urls
            assert reconnected.network_policy == box.network_policy
        restored = await Env.from_instance_id(deployed_env.instance_id)
        api = await legacy_protocol.v1_base_url(deployed_env, deployed_env.gateway_url, "items")
        state = (await protocol_v1.get_data(api)).model_dump(mode="json")
        save("environment-state.json", state)
        assert state["parts"][0]["data"] == {"items": ["starting-item", "vercel-review-complete"]}, state
        print("PASS separate agent called environment MCP; independently verified mutation and reconnect", flush=True)
        if restored._sandbox:
            await restored._sandbox._client.aclose()

        transfer = await builder.exec("python3", "-c", "import sys; sys.stdout.write('x' * 2097152); sys.stderr.write('y' * 2097152)")
        assert await transfer.wait() == 0
        for reader, expected in ((transfer.stdout, b"x"), (transfer.stderr, b"y")):
            count = 0
            while chunk := await reader.read(65536):
                assert chunk == expected * len(chunk)
                count += len(chunk)
            assert count == 2097152
        print("PASS command output: both streams remain readable after completion", flush=True)

        cleanup = True
        phase = "cleanup"
        print("[3/3] Removing the three sandboxes and checking cleanup.", flush=True)
        report = await teardown_run(context)
        save("teardown.json", asdict(report))
        assert len(report.terminated) == 2 and not report.still_up
        again = await teardown_run(context)
        assert not again.still_up
        fresh_context = copy.deepcopy(context)
        fresh_context.metadata.pop(TORN_DOWN_KEY, None)
        fresh_report = await teardown_run(fresh_context)
        assert len(fresh_report.terminated) == 2 and not fresh_report.still_up
        save("fresh-context-teardown.json", asdict(fresh_report))
        running = await builder.exec("bash", "-c", "printf 'ready\\n'; sleep 60")
        assert await asyncio.wait_for(running.stdout.readline(), 30) == b"ready\n"
        await (await fresh.get_sandbox(builder.sandbox_id)).terminate()
        try:
            await asyncio.wait_for(running.wait(), 15)
        except asyncio.CancelledError:
            pass
        assert not builder._processes
        assert await asyncio.wait_for(running.stdout.read(), 15) == b""
        for box in owned:
            try:
                await fresh.get_sandbox(box.sandbox_id)
            except SandboxNotFoundError as error:
                assert getattr(error.__cause__, "status_code", None) == 404
            else:
                raise AssertionError("Resource still exists after teardown")
        save("success.json", {"status": "passed", "score": score, "steps": 5,
                              "instance_id": context.instance_id, "resources_removed": len(owned),
                              "checks": ["project-oidc", "six-real-images", "task-run", "agent-mcp-tool-call",
                                         "independent-state-read", "trajectory", "persisted-task", "reconnect",
                                         "repeat-teardown", "fresh-context-teardown", "lookup-404",
                                         "completed-output-read", "running-command-teardown"], "finished_at": time.time()})
        completed = True
        print("PASS cleanup: environment, agent and builder removed; all lookups return 404", flush=True)
        print("END-TO-END EVALUATION PASSED: score 1", flush=True)
        print(f"Run: {context.instance_id}\nResult: {output / 'success.json'}\nAgent actions: {output / 'trajectory.json'}\nCleanup: all three sandboxes removed.", flush=True)
    finally:
        if not completed:
            save("failure.json", {"status": "failed", "phase": phase, "resources": resources})
            for box in owned:
                if box is owned[0]:
                    try:
                        build_logs = await asyncio.wait_for(
                            box.exec_script("cat /tmp/poc-logs/* 2>/dev/null || true"), 15)
                        (output / "image-builds.log").write_text(build_logs)
                    except Exception as error:
                        print("Build log collection:", type(error).__name__, flush=True)
                try:
                    processes = await asyncio.wait_for(box._sandbox.query_processes(), 15)
                    save(box.sandbox_id + "-processes.json", [
                        {"id": p.id, "returncode": p.returncode} for p in processes])
                except Exception as error:
                    print("Process log collection:", box.sandbox_id, type(error).__name__, flush=True)
                try:
                    await asyncio.wait_for(box._sandbox.stop(), 30)
                    print("STOPPED AND RETAINED", box.sandbox_id, flush=True)
                except Exception as error:
                    print("Retention check:", box.sandbox_id, type(error).__name__, flush=True)
            print(f"Evaluation did not finish. Diagnostics: {output}\nCheck build.stdout/build.stderr for image errors and resources.json for retained sandboxes.\nRerun the same command after resolving the error; each attempt keeps its own results.", flush=True)
        VercelSandboxProvider.create_vm = create_vm
        VercelSandbox.terminate = terminate
        await close_clients(clients)


if __name__ == "__main__":
    asyncio.run(main())
