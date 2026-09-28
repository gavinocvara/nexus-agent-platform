# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`.

## Checkpoint

- Branch `main`. Resolve the current head with `git rev-parse HEAD`.
- NEXUS `0.13.1`: PatchForge Milestones A (contracts), B (workspaces), C (sandbox), and
  D (ToolGateway) are complete, plus the Milestone D hardening release.
- Hardening fixed all seven findings from the independent adversarial review of
  Milestone D, with one deterministic regression test each (ADR 0010, CHANGELOG 0.13.1).
- Release commit `16a58f338267237c261233764e167433b3cb28c3`. GitHub Actions run
  `36399394926` passed `validate` and `compose-integration` on the first attempt.
  Locally: 372 passed (23 integration deselected), Ruff and strict mypy clean.

## Active Issues

- No known failing tests.

## Exact Next Step

Begin PatchForge Milestone E (Runtime): a closed phased
workflow over the existing ToolGateway, with bounded implement ⇄ validate loops and a
structurally reserved finalization capacity. The runtime must call
`ToolGateway.refresh_workspace` after every lease renewal.

Do not start Milestone F or make live model calls.

## Critical Constraints

- Atlas remains the outer job, policy, review, approval, and audit boundary.
- Keep Git authority and `.git` outside the sandbox. Use only operator-profile commands.
  No shell, network, or secrets.
- Model output is narrative only; all evidence is runtime-attested.
- `patch_proposed` is not approval, merge, deployment, or a push to `main`.
- Git ignore/attribute rules are pinned to the source commit; do not relax that check.
- Phase 5/6/7 frozen behavior and evidence hashes are unchanged (see `AGENTS.md`).
- Keep one coherent unit per checkpoint, and update this file at each checkpoint.
