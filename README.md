# AgentEnv + Vercel Sandbox

This proof of concept adds the `vercel` sandbox provider to AgentEnv. AgentEnv runs the task, connects the agent to its environment, collects the trajectory and scores the result. Vercel provides the separate sandboxes running the environment and agent.

The included demo exercises one complete evaluation: deploy an items environment behind a gateway with its database services, load `starting-item`, deploy a deterministic agent, ask it to add `vercel-review-complete` through MCP, and grade its response. The controller independently reads the final state, verifies the stored task and trajectory, reconnects, and tears down the successful run. The agent performs real MCP calls; it does not call a language model. This validates the integration, not model quality.

## Setup

Prerequisites: Git, Python 3.11.4 or newer, uv, Node.js and Vercel CLI 62.5.0 or newer (the demo was tested with 62.5.0). Use a Vercel project where you can create 4-vCPU/8-GB sandboxes for the build and environment, plus a 2-vCPU/4-GB agent. The demo builds Linux images in a fresh Vercel sandbox, so local Docker is not needed. Network access to the package and image registries referenced by the repository Dockerfiles is required.

AgentEnv 0.9.1334 includes the Vercel provider. Clone this branch for the standalone example environment and deterministic agent.

```bash
git clone --branch vercel-demo https://github.com/vercel-labs/agentenv-framework.git
cd agentenv-framework
uv sync --frozen --extra vercel
```

The launcher selects the dedicated Vercel configuration for environments and agents. The demo uses local stores in its output directory.

## Run with an existing Vercel login

If needed, run `vercel login`. Choose an existing project and team that you have access to. The wrapper uses the public `vercel project token` command and passes its short-lived OIDC token only to the child process. It does not print the token or write it to disk.

From the cloned repository, run:

```bash
./vercel-eval --scope YOUR_TEAM --project YOUR_PROJECT
```

Replace `YOUR_TEAM` with your team slug and `YOUR_PROJECT` with an existing project name. Each run prints a new temporary output directory for its records and artifacts. The launcher sets `AGENT_ENV_CONFIG` automatically and uses the repository's `.venv` Python. Do not add explicit `token`, `team_id` or `project_id` fields to the dedicated configuration for this OIDC route.

Run `./vercel-eval --help` for usage.

The demo creates three temporary sandboxes: an image builder, an environment and an agent. Successful runs remove all three, verify subsequent lookups report them missing, and verify cleanup from a fresh context. Failed runs stop and retain tracked sandboxes and record their identifiers for diagnosis. The runner also records handles reaching its termination guard during setup rollback. Failures before a handle reaches the runner can still follow the provider’s own creation-cleanup behavior. Retained resources are intentionally left for diagnosis; this demo does not automatically reap them.

The image-download fixture serves only disposable test images. Its fixed signing key is public test data; this fixture is not a production artifact store. The demo uses local SQLite and filesystem stores for run records and artifacts, and applies a restricted policy allowing the test package host and Vercel routes to the workload sandboxes. Scale's actual stores and network requirements need their own acceptance run.

## What passing means

Run with normal Python execution; the runner refuses `python -O` because its assertions are acceptance checks.

Expected final output:

```text
END-TO-END EVALUATION PASSED: score 1
```

The output folder retains:

- `success.json`: final pass, score, run identifier and cleanup assertions.
- `task.json`, `task-instance.json` and `context.json`: the five task steps and recorded result.
- `environment-state.json`: exactly `starting-item` and `vercel-review-complete`.
- `objects/`: collected task artifacts, including the agent trajectory.
- `resources.json`, `images.json` and `source-hashes.json`: resource, image and source provenance.
- `build.stdout`, `build.stderr` and `build-command.json`: image-build evidence.

The stdout/stderr stream can also be redirected to a local log. Output folders contain deployment coordinates and private local paths; review them before sharing. Keep these generated outputs outside the checkout.

## Review scope

The integration remains in-tree as requested, with optional Python SDK dependencies. CPU and memory are minimum requests rounded to supported shapes; fixed disk sizing is logged. Attribution keeps up to five tags, prioritizing `pipeline_step` and `run_id`, then other keys alphabetically, with a warning naming omitted keys. OIDC and explicit configuration credentials are supported. This POC covers ephemeral runs and active reconnect. Persistent resume, credential brokering and model-quality benchmarking are separate work.

Recorded follow-ups include cleanup of commands still running during teardown, upload retry diagnostics, resource log severity and readiness behavior. Production workload acceptance, actual stores, CPU/memory semantics, optional OIDC and maintenance ownership remain for Scale to confirm.

See [validation results](examples/vercel-sandbox/RESULTS.md) for the validation scope and remaining acceptance work.

The provider contribution is on [`feat/vercel-sandbox`](https://github.com/vercel-labs/agentenv-framework/tree/feat/vercel-sandbox). See the [upstream project README](README.upstream.md) for the broader AgentEnv SDK.
