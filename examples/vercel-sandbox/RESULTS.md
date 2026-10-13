# End-to-end results

## October 12, 2026: review fixes

The updated demo completed all five task steps with score **1.0** using an existing Vercel CLI login. It independently verified the environment mutation, stored trajectory and task result, and fresh-provider reconnect. The environment, agent and image builder were removed. Cleanup also succeeded from a fresh saved context after deletion, and subsequent lookups reported all three resources missing.

A separate live probe verified the five-tag cap, preserving `pipeline_step` and `run_id`; refresh of a cached unrestricted policy after another handle changed it; lookup of a stopped sandbox; typed not-found errors after deletion; repeated handle termination; and both cleanup entry points using saved sandbox IDs.

On native Linux, the unit/protocol suite passed **7,250 tests with 9 skips** for root permissions, platform/filesystem differences and the missing optional WebSocket dependency. Package build, frozen-lock validation and clean installation passed; clean installation ran `hello` twice with score 1 and no leftover sandbox work folder. The plugin API check found no break. A focused suite passed **151 tests** with `VERCEL_OIDC_TOKEN` deliberately set to a placeholder, covering credential-test isolation as well as the provider, policy and cleanup changes.

These checks ran against the review-fix candidate based on upstream commit `c70011524ae65fbd35eb2b8292c9560eb81967cc`. The provider code and tests are shared with the upstream contribution; the standalone demo stays on `vercel-demo`.

## October 9, 2026: original evaluation

The reviewed provider completed a real AgentEnv evaluation using project OIDC obtained from an existing Vercel CLI login. No manually created API token or model key was needed.

| Step | Observed result |
| --- | --- |
| Deploy environment | Gateway, items MCP server and database services running in Vercel Sandbox |
| Load data | Initial state contains `starting-item` |
| Deploy agent | A separate Vercel Sandbox runs the deterministic review agent |
| Prompt agent | Agent calls `items_add_item` and `list_items` through the environment's public MCP route |
| Grade response | Task status `completed`, all five steps successful, score **1.0** |

The controller independently read the final environment state as `["starting-item", "vercel-review-complete"]`. AgentEnv collected the native trajectory into its local object store and retained the task-instance result. Fresh-provider reconnect restored routes and policy. Teardown removed the agent, environment and image builder; all three subsequent lookups returned 404. Repeated teardown succeeded.

The initial attempt exposed a missing MCP client dependency in the new test-agent image. The dependency was added and checked at build time; two subsequent full runs passed. The final runner also passed an injected setup-failure test that verifies recording and retention before create returns. The initial diagnostic resources were stopped and retained. No provider implementation changes were needed for this evaluation.

The publication candidate is based on upstream commit `c70011524ae65fbd35eb2b8292c9560eb81967cc`. Upstream changes and dependency metadata were incorporated before publication. The provider now inherits the current VM image-loading contract, preserving registry pulls, builds from stored contexts and early refusal of unavailable images. The five demo scripts are unchanged. The complete evaluation above passed again on this base using a fresh installation with only the `vercel` extra. After the compatibility correction, 171 focused provider, policy, capability and VM tests passed. A separate live Vercel probe pulled and ran a registry image, built a context image and checked its contents, rejected an unavailable image, and verified cleanup with a 404 lookup. The plugin API check reported no break.

Earlier broad verification on base `6e20760bebacd73ebb74e8add0bd69407b24baa4`: 6,810 unit/protocol passes with 9 declared skips, 83 provider-specific passes, all 60 installer cases passed across two runs, build and clean installation passed. These historical results do not establish the outcome of the broader suite on the newer base. See the upstream PR for current validation and CI status.

This is a deterministic integration evaluation. It does not benchmark a language model or establish acceptance of Scale's production workloads, image/object stores, mixed-provider setup, persistence or credential brokering. Maintainers should review the documented CPU/memory minimum semantics, OIDC option, operational ownership and recorded provider follow-ups, particularly commands still running during teardown and upload retry diagnostics.

Private run logs, deployment coordinates, local state and diagnostic resource identifiers are excluded from the repository. The demo scripts produce those records for each reviewer's own run.
