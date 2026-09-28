# Codex Handoff

## Checkpoint

- Branch: `main`
- Latest validated durable commit before this checkpoint: `72c34c67843703b1da70339e80ecf2a71dde7cdb`
- Checkpoint commit: the commit containing this file; resolve with `git rev-parse HEAD`
- Remote at checkpoint start: `origin/main` = `72c34c67843703b1da70339e80ecf2a71dde7cdb`
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

- `pytest tests/unit/test_patchforge_gateway.py tests/unit/test_patchforge_policy.py tests/unit/test_patchforge_sandbox.py -q`: **45 passed**
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
- Expected `WorkspaceError`, `SandboxError`, and local I/O failures are not yet uniformly
  converted into typed failed call evidence.
- Truncation accounting should be checked for tree/search/file/diff outputs, not only
  sandbox execution output.
- Formatter, linter, and typecheck command mappings need explicit focused coverage.
- Milestone D docs, changelog, version bump, and final CI are intentionally deferred.

## Uncommitted Work

- Expected after creating the checkpoint commit: none. Verify with `git status --short`.
- If this file is read before the checkpoint commit exists, stage only the files listed
  above after rerunning the focused gate.

## Exact Next Step

Continue Milestone D only. Harden `ToolGateway.invoke` so expected workspace, sandbox,
Unicode, and OS failures produce typed failed evidence without swallowing programming
errors. Correctly attest truncation for every bounded output type. Add explicit tests for
all fixed execution-tool mappings and constructor identity/profile/source binding. Then
run the focused gate, full deterministic suite, frozen-boundary checks, and real Docker
tests. Only after those pass, update version/docs, commit Milestone D complete, push, and
wait for both CI jobs. Do not start Milestone E in the same checkpoint.

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
