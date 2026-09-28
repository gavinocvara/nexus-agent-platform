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
- Kept the unit pure: no workspace provisioning, tool invocation, model adapter, Git
  authority, network, secrets, memory, GitHub mutation, or live model calls.

## Files Changed

- `src/nexus/patchforge/runtime.py` (new lifecycle core)
- `src/nexus/patchforge/__init__.py`
- `tests/unit/test_patchforge_runtime.py` (new exhaustive lifecycle coverage)
- `CODEX_HANDOFF.md`

## Validation

- Runtime lifecycle suite: **188 passed**.
- PatchForge contracts plus Runtime lifecycle: **202 passed in 0.38s**.
- Focused Ruff over PatchForge and Runtime tests: **passed**.
- Strict mypy over `src/nexus/patchforge`: **passed, 8 source files**.
- A broader local gateway selection reached one pre-existing Windows-only fixture
  failure: the Milestone D hardening test attempts to create `a:b.py`, which Windows
  cannot represent. Runtime tests are unaffected; the validated Linux CI baseline is
  green. Do not weaken the production path rule to accommodate that fixture.

## Exact Next Step

Implement the Runtime coordinator over the existing lifecycle and `ToolGateway`.
Introduce a deterministic engine protocol with no live implementation, accept only
typed ToolGateway phase/report requests, renew workspace leases at explicit boundaries,
call `ToolGateway.refresh_workspace` immediately after every successful renewal, and
guarantee cleanup on success and failure paths. Add scripted tests before expanding
result assembly.

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
