# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`.

## Checkpoint

- Branch: `main`
- NEXUS: `0.14.0`
- Baseline: Milestones A-E plus all seven Milestone D review fixes are complete.
- Milestone D release: `16a58f338267237c261233764e167433b3cb28c3`;
  GitHub Actions run `36399394926` passed both jobs.
- Completed milestone: PatchForge Milestone E - Runtime.
- Runtime release: the commit containing this file; resolve with
  `git rev-parse HEAD`.

## Completed In Milestone E

- Added the exact closed Runtime lifecycle graph from `created` through `closed`,
  including only the bounded `targeted_validate -> implement` retry edge.
- Added strict runtime-owned transition records and a replay-validatable snapshot bound
  to the PatchForge run ID. Every phase change is explicit and sequenced.
- Enforced `RunBudgets.max_implementation_loops` deterministically.
- Added deterministic mappings for partial, cancellation, policy, sandbox, validation,
  workspace, engine, attestation, and budget-exhaustion outcomes.
- Routed active failures to finalization and finalization failures to cleanup. Cleanup
  failure always closes the lifecycle while preserving an earlier primary failure.
- Added a single-action `RuntimeEngine` protocol and `PatchForgeRuntime` coordinator.
  There is no parallel-action shape or live engine implementation.
- Runtime accepts phase and report requests only after ToolGateway validation, preserves
  gateway records as authoritative evidence, and leaves successful patch attestation to
  Milestone F.
- Runtime actions carry the exact strict ToolGateway argument models. The real
  integration test caught and removed a JSON-string/typed-enum mismatch that the
  protocol fake had masked.
- Runtime renews leases only for workspace-bound tools and immediately calls
  `ToolGateway.refresh_workspace` after every successful renewal. Refresh failures are
  typed workspace failures.
- Runtime always attempts cleanup and closes success, cancellation, budget, policy,
  workspace, sandbox, engine, finalization, and cleanup-failure paths.
- Engine-adapter exceptions are typed as engine failures. Unexpected gateway or Runtime
  programming defects are not swallowed or relabeled; cleanup runs and the defect
  propagates.
- Runtime requires the ToolGateway's exact bound workspace manager and a fresh gateway
  evidence ledger, preventing cross-run or pre-used authority from being adopted.
- ToolGateway exposes runtime-attested per-phase call/duration/output usage, total usage,
  and finalization-reserve use. Runtime completion cross-checks it against tool evidence.
- Added a real deterministic integration proof over `WorkspaceManager`,
  `ToolGateway`, and `FakeSandbox`: failing reproduction, compare-and-swap edit,
  targeted/full validation, diff inspection, finalization-reserve use, report, lease
  refresh, evidence retention, and workspace removal.
- Hardened verified workspace cleanup for read-only Git object files on Windows. The
  retry callback is confined to the already marker-verified managed workspace.
- Kept the unit deterministic: no model adapter, direct Git authority, network, secrets,
  memory, GitHub mutation, or live model calls.

## Files Changed

- `src/nexus/patchforge/runtime.py` (lifecycle and coordinator)
- `src/nexus/patchforge/gateway.py`
- `src/nexus/patchforge/workspace.py`
- `src/nexus/patchforge/__init__.py`
- `tests/unit/test_patchforge_runtime.py` (new exhaustive lifecycle coverage)
- `tests/unit/test_patchforge_runtime_coordinator.py` (new)
- `tests/unit/test_patchforge_workspace.py`
- `tests/unit/test_patchforge_gateway.py`
- `CHANGELOG.md`, `README.md`, `ROADMAP.md`, and ADR 0010
- `pyproject.toml` and `src/nexus/_version.py`
- `CODEX_HANDOFF.md`

## Validation

- Runtime lifecycle and coordinator suites: **204 passed in 1.47s**.
- Real ToolGateway/WorkspaceManager/FakeSandbox Runtime path: **1 passed**.
- Focused workspace cleanup tests: **3 passed**.
- Focused PatchForge contracts/policy/sandbox/workspace/gateway/Runtime suite:
  **307 passed, 2 deselected in 45.22s**.
- Focused Ruff over PatchForge and Runtime tests: **passed**.
- Strict mypy over `src/nexus/patchforge`: **passed, 8 source files**.
- Full release gate:
  - `pip check`: **passed**.
  - Ruff format: **154 files already formatted**.
  - Ruff lint: **passed**.
  - mypy: **passed, 85 source files**.
  - pytest: **575 passed, 2 skipped, 23 deselected in 103.08s**.
  - scenario validation: **5 scenarios validated**.
  - Compose configuration: **passed**.
  - Compose integration: **23 passed in 183.34s**.
- The two full-suite skips are Milestone D hardening fixtures that create `a:b.py`,
  which Windows cannot represent. They remain active on Linux CI. The production path
  rule was not weakened.
- Local release-gate recovery removed only generated `.mypy_cache`, stale Visual Studio
  installer scratch data under `%TEMP%`, and unused Docker build cache. No project,
  evidence, image, container, or volume data was removed.

## Exact Next Step

Push the NEXUS 0.14.0 Milestone E release and require both GitHub Actions jobs green.
After release verification, the exact next task is Milestone F - Attestor. Do not begin
it in the Milestone E release session.

## Critical Constraints

- Atlas remains the outer job, policy, review, approval, persistence, and audit boundary.
- ToolGateway is the only engineering capability boundary.
- Keep Git authority and `.git` outside the sandbox; use only operator-profile commands.
- No unrestricted shell, network, secrets, memory/Brain, GitHub mutation, or live model
  calls.
- Model output is narrative only; runtime-attested evidence is authoritative.
- Preserve phase budgets, finalization reserve, disabled parallel calls, and bounded
  implementation loops.
- Cleanup must run on terminal and failure paths.
- Do not start Milestone F.
- Keep one coherent validated, committed, pushed unit per checkpoint.
