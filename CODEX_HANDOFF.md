# Codex Handoff

## Checkpoint

- Branch: `main`
- Milestone D implementation checkpoint:
  `cbb65ce9eb9bf8d437d9d62ac57f2c4aeb6b3edd`
- Milestone D release commit: the commit containing this file; resolve with
  `git rev-parse HEAD`
- NEXUS version: `0.13.0`
- Completed milestone: PatchForge Milestone D - ToolGateway
- Exact next milestone: PatchForge Milestone E - Runtime

## Completed

- Preserved the completed Milestone A contracts, Milestone B workspace authority, and
  Milestone C Docker/Fake sandbox boundary.
- Completed a strict typed ToolGateway for bounded reads, compare-and-swap writes,
  runtime-owned Git status/diff, fixed operator-profile execution, and explicit control
  requests.
- Bound gateway construction and every call to the Atlas job, agent identity,
  engineering task/hash, source revision, canonical repository profile, and verified
  workspace.
- Enforced capability, phase, path/symlink, protected-file, test-change, call, duration,
  output, and finalization-reserve policy. Shell executables and arbitrary commands are
  rejected.
- Added append-only runtime evidence with contiguous call IDs, canonical hashes, bounded
  output/truncation, and typed operational failures.
- Kept `advance_phase` and `submit_report` request-only. Milestone D introduces no
  lifecycle transition, model adapter, live call, GitHub mutation, approval, merge,
  deployment, or memory.
- Advanced NEXUS to `0.13.0` and aligned CHANGELOG, README, ADR 0010, ROADMAP, project
  state, and this handoff. ToolGateway is no longer documented as future work.

## Files Changed

- `src/nexus/patchforge/gateway.py`
- `src/nexus/patchforge/policy.py`
- `src/nexus/patchforge/sandbox.py`
- `src/nexus/patchforge/__init__.py`
- `tests/unit/test_patchforge_gateway.py`
- `tests/unit/test_patchforge_policy.py`
- `tests/unit/test_patchforge_sandbox.py`
- `pyproject.toml`
- `src/nexus/_version.py`
- `CHANGELOG.md`
- `README.md`
- `docs/adr/0010-patchforge-v1-trustworthy-engineering-agent.md`
- `ROADMAP.md`
- `PROJECT_STATE.md`
- `CODEX_HANDOFF.md`

## Validation

- Policy regression verification: **12 passed**. The valid profile remains within its
  sandbox limits, and explicit timeout/output-limit violations remain rejected.
- Release focused gateway/policy/sandbox gate: **60 passed**.
- Release full non-integration suite: **351 passed, 23 deselected**.
- Release formatting: **152 files already formatted**; Ruff lint: **passed**.
- Release strict mypy: **84 source files passed**; `pip check`: **passed**.
- Editable install and imported package both report NEXUS **0.13.0**.
- Five scenarios validate; Compose configuration passes with only expected warnings for
  absent local PostgreSQL variables.
- Real Docker sandbox integration: **3 passed**.
- Secret-pattern scan: **no matches**. Governing specifications and frozen Phase 7
  protocol/report files remain unchanged.
- The final release gate and GitHub Actions result are recorded in `PROJECT_STATE.md`
  and should be verified against the release commit before Milestone E begins.

## Current State

- No known Milestone D test failure remains.
- Expected uncommitted work after the release commit: none. Verify with
  `git status --short`.
- The Milestone D release is complete only when the release commit is on `origin/main`
  and both `validate` and `compose-integration` GitHub Actions jobs pass.
- No live model calls were performed.

## Exact Next Step

Begin Milestone E - PatchForge Runtime in a new resumable checkpoint. Implement the
closed phase state machine over the existing ToolGateway, with bounded
`implement <-> targeted_validate` loops, explicit transition evidence, failure and
cleanup paths, and structurally reserved finalization capacity. Do not revise the
ToolGateway boundary unless a deterministic Runtime requirement demonstrates the need.

## Recommended Commands

```powershell
Set-Location C:\Users\arman\Downloads\nexus-agent-platform\phase7-worktree
Get-Content CODEX_HANDOFF.md
Get-Content PROJECT_STATE.md -TotalCount 40
git status --short --branch
git log -5 --oneline
git rev-parse HEAD
git rev-parse origin/main
& 'C:\Users\arman\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/unit/test_patchforge_gateway.py tests/unit/test_patchforge_policy.py tests/unit/test_patchforge_sandbox.py -q
```

## Constraints

- Atlas-min remains the outer job, policy, review, approval, persistence, and audit
  boundary.
- Keep Git authority and `.git` outside the sandbox. Never expose unrestricted shell.
- Commands come only from the operator-owned `RepositoryProfile`; no network or
  secrets.
- Model-authored content is narrative only. Runtime-owned records are the exclusive
  source of execution, reproduction, validation, diff, and budget evidence.
- Preserve phase-scoped budgets, disabled parallel calls, and finalization reserve.
- `PATCH_PROPOSED` is not approval, merge, deployment, or a push to `main`.
- Preserve all Phase 5/6/7 frozen behavior and evidence hashes.
- Work in small validated, committed, pushed, documented checkpoints.

## Deferred; Do Not Start Accidentally

- Milestone F Attestor and later deterministic E2E/benchmark work
- Live model calls or a live PatchForge task
- SentinelQA-lite and GitHub issue/branch/draft-PR integration
- PatchForge memory, Brain reuse, automatic adoption, Kubernetes, or Engram
