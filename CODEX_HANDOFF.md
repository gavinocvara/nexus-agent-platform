# Codex Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and the ADRs. Rules live in
`AGENTS.md`.

## Checkpoint

- Branch `main`; latest verified commit `2909c7139a0cac0364af1f7122fe8daf1e5eb3ce`
  (Milestone D release verification). Resolve the current head with
  `git rev-parse HEAD`.
- NEXUS `0.13.0`.
- Completed: PatchForge Milestones A (contracts), B (workspaces), C (sandbox), and
  D (ToolGateway). Release commit
  `bbb093e37421050bf2d31ea8a912c3e7a634ac9f`.
- Last validated gate: GitHub Actions run `36390696248`, where `validate` and
  `compose-integration` both passed. Locally: 351 unit tests passed (23 integration
  deselected), Ruff and strict mypy clean, 3 real Docker sandbox tests passed.

## Active Issues

- No failing tests.
- Open for the owner to decide: an independent read-only adversarial review of Milestone D
  (2026-09-28) reported reproducible gateway defects and recommends fixing them before
  Milestone E. The fixes are in `src/nexus/patchforge/gateway.py` and `workspace.py`:
  - a budget overrun after an execution raises an unhandled ValidationError;
  - read tools in FINALIZE can consume the finalization reserve before `submit_report`;
  - creating a `.gitignore` hides the agent's own files from diff and status;
  - filenames such as `a:b.py` crash tree, search, and diff;
  - file tools do not re-verify the workspace handle or lease on each call;
  - `submit_report` accepts unknown evidence IDs;
  - test-infrastructure files (`conftest.py`, `pytest.ini`, `sitecustomize.py`, `*.pth`)
    bypass the test-change policy.

## Exact Next Step

Wait for the owner's decision on the review findings above. Then either:

1. fix them as a Milestone D hardening checkpoint (0.13.x) with one deterministic
   regression test each, run the full gate, and wait for green CI; or
2. begin PatchForge Milestone E (Runtime): a closed phased workflow over the existing
   ToolGateway, with bounded implement ⇄ validate loops and a structurally reserved
   finalization capacity.

Do not start Milestone F or make live model calls.

## Critical Constraints

- Atlas remains the outer job, policy, review, approval, and audit boundary.
- Keep Git authority and `.git` outside the sandbox. Use only operator-profile commands.
  No shell, network, or secrets.
- Model output is narrative only; all evidence is runtime-attested.
- `patch_proposed` is not approval, merge, deployment, or a push to `main`.
- Phase 5/6/7 frozen behavior and evidence hashes are unchanged (see `AGENTS.md`).
- Keep one coherent unit per checkpoint, and update this file at each checkpoint.
