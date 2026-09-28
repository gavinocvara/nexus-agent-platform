# Codex Handoff

## Checkpoint

- Branch: `main`
- ToolGateway foundation commit: `03b9e5d8ecb545ab6207ed7a83d44e071743225d`
- Current hardening checkpoint: the commit containing the latest version of this file;
  resolve with `git rev-parse HEAD`
- Verified remote foundation: `origin/main` =
  `03b9e5d8ecb545ab6207ed7a83d44e071743225d`
- NEXUS version: `0.12.0`
- Active milestone: PatchForge Milestone D - ToolGateway, **in progress**

## Completed In This Run

- Closed Milestone C at commit `72c34c67843703b1da70339e80ecf2a71dde7cdb`.
- Verified GitHub Actions run `36368951357`: `validate` and `compose-integration` both
  passed on the first attempt.
- Added a typed ToolGateway foundation with strict argument/output contracts,
  runtime-owned call IDs and evidence, append-only records, canonical hashes,
  phase/capability/budget checks, finalization reserve protection, bounded read tools,
  compare-and-swap writes, runtime-owned Git inspection, fixed-profile sandbox execution,
  and typed control requests.
- Added sensitive-path, symlink, scope, protected-path, and test-change protections.
- Rejected shell executables in operator-owned repository commands.
- Added sandbox validation for operator-selected repository working directories.
- Confirmed the reported policy fixture regression is already fixed: the valid fixture's
  command limits fit its sandbox policy, and explicit timeout/output violation tests pass.
- Normalized expected workspace, sandbox, OS, and Unicode failures into typed failed
  call evidence while leaving programming defects visible to the runtime.
- Propagated bounded tree, search, file, diff, and sandbox-output truncation into the
  runtime-owned `ToolCallRecord`; oversized phase output is replaced by a bounded typed
  failure.

## Files Changed

- `src/nexus/patchforge/gateway.py` (new)
- `tests/unit/test_patchforge_gateway.py` (new)
- `src/nexus/patchforge/policy.py`
- `src/nexus/patchforge/sandbox.py`
- `src/nexus/patchforge/__init__.py`
- `tests/unit/test_patchforge_sandbox.py`
- `PROJECT_STATE.md`
- `CODEX_HANDOFF.md` (new)

## Validation

- Foundation focused gate: **45 passed** across gateway, policy, and sandbox unit tests.
- Latest gateway hardening gate: `pytest tests/unit/test_patchforge_gateway.py -q`:
  **26 passed**
- `ruff check` on PatchForge sources and the three focused test files: **passed**
- `ruff format --check` on the same files: **10 files already formatted**
- `mypy src/nexus/patchforge`: **passed, 7 source files**
- Milestone C full local gate before this checkpoint: **313 passed, 23 deselected**;
  three real Docker sandbox integration tests passed.
- Milestone C GitHub Actions: run `36368951357`, both jobs successful.

## Current Gaps

- No focused test is failing.
- The full repository suite, real Docker integration, secret scan, frozen-artifact diff,
  and GitHub Actions have not yet run for the Milestone D foundation.
- Formatter, linter, and typecheck command mappings need explicit focused coverage.
- Constructor identity, source, profile, task-hash, and verified-workspace bindings need
  explicit negative coverage and any resulting implementation hardening.
- Milestone D docs, changelog, version bump, and final CI are intentionally deferred.

## Uncommitted Work

- Expected after creating the checkpoint commit: none. Verify with `git status --short`.
- If this file is read before the checkpoint commit exists, stage only the files listed
  above after rerunning the focused gate.

## Exact Next Step

Continue Milestone D only. Add explicit tests for every fixed execution-tool mapping and
for constructor identity, source, profile, task-hash, and workspace binding. Harden only
the failures those tests expose. Commit that unit before running the full deterministic
suite, frozen-boundary checks, and real Docker tests. Only after those pass, update
version/docs, commit Milestone D complete, push, and wait for both CI jobs. Do not start
Milestone E in the same checkpoint.

## Recommended Commands

```powershell
Set-Location C:\Users\arman\Downloads\nexus-agent-platform\phase7-worktree
git status --short
git log -3 --oneline
Get-Content CODEX_HANDOFF.md
& 'C:\Users\arman\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/unit/test_patchforge_gateway.py tests/unit/test_patchforge_policy.py tests/unit/test_patchforge_sandbox.py -q
& 'C:\Users\arman\AppData\Local\Programs\Python\Python312\python.exe' -m mypy src/nexus/patchforge
& 'C:\Users\arman\AppData\Local\Programs\Python\Python312\python.exe' -m ruff format --check src/nexus/patchforge tests/unit/test_patchforge_gateway.py tests/unit/test_patchforge_policy.py tests/unit/test_patchforge_sandbox.py
& 'C:\Users\arman\AppData\Local\Programs\Python\Python312\python.exe' -m ruff check src/nexus/patchforge tests/unit/test_patchforge_gateway.py tests/unit/test_patchforge_policy.py tests/unit/test_patchforge_sandbox.py
```

## Constraints

- Atlas-min remains the outer job, policy, review, approval, persistence, and audit
  boundary.
- Keep Git authority and `.git` outside the sandbox; never expose unrestricted shell.
- Commands come only from operator-owned `RepositoryProfile`; no network or secrets.
- Model-authored content is narrative only; execution and validation evidence is runtime-
  attested.
- Preserve phase-scoped budgets, disabled parallel calls, and finalization reserve.
- `PATCH_PROPOSED` is not approval, merge, deployment, or a push to `main`.
- Preserve all Phase 5/6/7 frozen behavior and evidence hashes.
- Keep work in small committed, pushed, documented checkpoints.

## Deferred; Do Not Start Accidentally

- Milestone E runtime state machine
- Milestone F attestor and later E2E/benchmark work
- Live model calls or a live PatchForge task
- SentinelQA-lite and GitHub branch/draft-PR integration
- PatchForge memory, Brain reuse, daily adoption, Phase 8, Kubernetes, or Engram
