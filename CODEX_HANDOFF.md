# Handoff

Current state only. History lives in Git, `CHANGELOG.md`, and `docs/adr/`.

## State

- Version: NEXUS `0.13.0`; resolve HEAD with `git rev-parse HEAD`.
- Active track: accelerated PatchForge v1 (`ROADMAP.md`).
- Last completed milestone: D - ToolGateway, released at `bbb093e` on `main`;
  GitHub Actions run `36390696248` passed `validate` and `compose-integration`.
- Since then: repository context-slimming maintenance (docs/config only; no runtime
  behavior change).
- Last full gate: 351 passed, 23 deselected (non-integration); Ruff format/lint and
  strict mypy clean.
- Active failure: none known.

## Exact Next Step

Begin Milestone E - PatchForge Runtime in a new checkpoint. Implement the closed phase
state machine from ADR 0010 over the existing `ToolGateway`: bounded
`implement <-> targeted_validate` loops, explicit transition evidence, failure and
cleanup paths, and structurally reserved finalization capacity. Do not revise the
ToolGateway boundary unless a deterministic Runtime requirement demonstrates the need.

Focused start:

```bash
git status --short --branch
python -m pytest tests/unit/test_patchforge_gateway.py tests/unit/test_patchforge_policy.py tests/unit/test_patchforge_sandbox.py
```

## Critical Constraints

- Follow `AGENTS.md` invariants and PatchForge boundaries.
- `advance_phase` and `submit_report` are request-only today; Milestone E owns transitions.
- No live model calls, GitHub mutation, approval, merge, deployment, or memory in E.
- Do not start Milestone F (Attestor) or later work accidentally.
- Preserve Phase 5/6/7 frozen behavior and evidence hashes.
