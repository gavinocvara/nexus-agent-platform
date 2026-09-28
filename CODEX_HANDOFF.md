# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`.

## Checkpoint

- Branch: `main`
- NEXUS: `0.13.1`
- Baseline: Milestones A-D plus all seven Milestone D review fixes are complete.
- Milestone D release: `16a58f338267237c261233764e167433b3cb28c3`;
  GitHub Actions run `36399394926` passed both jobs.
- Active milestone: PatchForge Milestone E - Runtime, in progress.
- Current Runtime checkpoint: the commit containing this file; resolve with
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
- `CODEX_HANDOFF.md`

## Validation

- Runtime lifecycle and coordinator suites: **202 passed in 2.03s**.
- Real ToolGateway/WorkspaceManager/FakeSandbox Runtime path: **1 passed**.
- Focused workspace cleanup tests: **3 passed**.
- Focused PatchForge contracts/policy/sandbox/workspace/gateway/Runtime suite:
  **307 passed, 2 deselected in 45.22s**.
- Focused Ruff over PatchForge and Runtime tests: **passed**.
- Strict mypy over `src/nexus/patchforge`: **passed, 8 source files**.
- The two deselections are pre-existing Milestone D hardening fixtures that create
  `a:b.py`, which Windows cannot represent. The same tests pass in the validated Linux
  CI baseline. Do not weaken the production path rule to accommodate those fixtures.

## Exact Next Step

Perform the Milestone E closure audit against the explicit requirements. Add only
missing deterministic edge coverage or narrowly required Runtime behavior. If no
architectural gap remains, update the required release docs/version and run the full
release gate once. Do not implement Milestone F attestation.

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
