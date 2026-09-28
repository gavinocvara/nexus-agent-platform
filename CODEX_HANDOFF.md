# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`.

## Checkpoint

- Branch `main`; local `HEAD` must equal `origin/main` at every checkpoint.
- Released: NEXUS `0.14.0` (Milestones A-E). Runtime release `7222297`; GitHub Actions
  run `36459625231` passed both jobs.
- Active: PatchForge Milestone F - Attestor (in progress, unreleased).

## Milestone F Progress

Done (committed and pushed):

1. `WorkspaceManager.propose_commit`: runtime-owned commit in the control repository with
   a fixed identity, caller timestamp, and private index; its diff must equal the
   worktree diff byte for byte. `GitRunner` accepts only allowlisted index/identity env.
2. Runtime workspace fingerprints: `WorkspaceStateRecord` at start and before/after every
   write or sandbox-execution call, plus `FinalWorkspaceCapture` (proposed commit and
   patch bytes) at `reported`, before cleanup. Capture failures are typed
   (`WORKSPACE_ERROR` during the run; `FinalCaptureError` at the end).

Remaining:

3. Attestor (`src/nexus/patchforge/attestor.py`): pure function from `RuntimeCompletion`
   plus task, profile, and policy to `PatchResult`. Map sandbox executions to
   `TestExecution` by command purpose; reproduction = the `reproduction` command failing
   on the pristine tree, then targeted tests passing on the final tree; checks count only
   executions whose before/after fingerprints equal the final diff; `DiffSummary` with
   scope/protected/test findings; tamper checks (command hashes, execution/tool-call
   linkage, fingerprint chain with no out-of-band changes, patch hash). Store the patch in
   a content-addressed local artifact store. Only a clean attestation is
   `patch_proposed`.
4. Real integration proof (WorkspaceManager + ToolGateway + FakeSandbox + Runtime +
   Attestor): happy path plus tamper, scope, stale-validation, and missing-reproduction
   paths.
5. Release 0.15.0: ADR 0010 section, ROADMAP, CHANGELOG, README status, full gate,
   green GitHub Actions, then hand off Milestone G.

## Active Issues

- No known failing tests. Focused PatchForge suite: 320 passed.
- Remote branch `maintenance/repo-hygiene-claude` is unmerged and untouched; owner decides.

## Critical Constraints

- Atlas remains the outer policy, review, approval, persistence, and audit boundary.
- ToolGateway is the only engineering capability boundary; the model never gets Git.
- Runtime-attested evidence is authoritative; model output is narrative only.
- No unrestricted shell, network, secrets, memory/Brain, GitHub mutation, or live model
  calls. The owner's API key stays in ignored local state only.
- Preserve phase budgets, finalization reserve, disabled parallel calls, bounded loops,
  cleanup on every path, and Phase 5/6/7 frozen evidence.
