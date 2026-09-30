# Codex Handoff

Canonical transfer document for the next coding agent. Current state only; history is in
Git and `CHANGELOG.md`, rules in `AGENTS.md`, the long-form resume state in
`PROJECT_STATE.md`. Read this file, then only the source and test files named below.

## Checkpoint

| Item | Value |
| --- | --- |
| Version | `0.27.0` (`pyproject.toml`) on `main`; no version bump on the branch below |
| `main` | 0.27.0 via PR #1 and PR #2; CI green at `6ea5a92` (run `36641561380`) |
| Active branch | `claude/nexus-command-center`: NEXUS Command Center, owner-requested, **not merged**, no PR opened |
| Working tree | clean at each branch checkpoint |

## Command Center (branch `claude/nexus-command-center`)

Read-only spatial dashboard (ADR 0013, `docs/runbooks/command-center.md`).

- Backend `src/nexus/command_center/`: GET-only FastAPI view models over cycle records,
  lease, decisions, publications, SentinelQA verdicts, private memory (SQLite `mode=ro`),
  lab health (diagnostics layer), lab catalog without ground truth; SSE hub; replay of 8
  curated engineer evaluation scenarios in a temp dir (scripted stand-ins named).
- Client `web/command-center/`: Vite + React 19 + TypeScript + React Three Fiber.
  Director/timeline in `src/director/` (pure, unit-tested); scene in `src/scene/`; HUD in
  `src/hud/`. Owner overlay shows governed Slack/CLI commands only.
- Validation at the checkpoint: see "Validation" below.

Not built (each is an owner decision): live phase visibility via
`cycles/active.progress.json` written by `EngineeringCycle._advance` (governing path);
a CI job for the client (`.github`); a Compose service and image (`compose.yaml`,
`Dockerfile`); any write action; authentication for non-loopback exposure. AegisOps
investigator runs are not persisted where the dashboard reads, so AegisOps shows lab
health only.

## Validation (branch checkpoint, Linux, Python 3.12.3, Node 22)

- `uv pip check` compatible; Ruff format and lint clean; strict mypy clean (138 files).
- pytest: **953 passed, 23 deselected**, no failures (includes 17 Command Center tests).
  Engineer evaluation gate passed; its `record_sha256` values equal the replay episodes'.
- `nexus.lab.scenarios validate`: 5 scenarios. `docker compose config --quiet`: valid.
- Client: `tsc -b` clean; vitest 5/5; `vite build` ok (HUD 86 KB gz, lazy scene 289 KB
  gz); Playwright visual suite 6/6 (SwiftShader). Screenshots reviewed at 1440x900,
  1920x1080, 390x844 for live, empty, replay, data, and activity states.
- Not run: Compose integration, CI (no workflow change), real-GPU frame timing.

## Subsystem status (unchanged on `main`)

| Subsystem | Status |
| --- | --- |
| SentinelQA-lite | Complete + runner-integrity canary; residual risk in ADR 0011. |
| Resident engineer | Hardened; effective autonomous class empty in production; draft-PR-only; off by default. |
| Slack | Live owner decision works over signed HTTPS; `/nexus bogus` and non-owner re-check pending. |
| GitHub publisher | Live `exercise-github` passed 8/8 and cleaned up; no real `decide` + `publish` yet. |
| Scheduler, model spend | Disabled; no live model call ever made. |

## Known operational risks

- A real `propose` cycle runs the full suite about seven times; with the default
  1800 s budget it may end `budget_exhausted`. Raise the budget or narrow targeted paths first.
- SentinelQA residual risk: canary-aware or test-detecting candidate code (ADR 0011).
- Command Center has no authentication: loopback only (enforced by `allowed_hosts`).

## Owner decisions required (nothing below has been done)

1. Review the Command Center branch; decide whether to open a PR and merge.
2. Decide the governed progress-file instrumentation (ADR 0013), a client CI job, and a
   Compose service.
3. Finish the Slack exercise (`/nexus bogus`, non-owner) and authorize the first real
   `decide` + `publish`.
4. Remote branch cleanup: `maintenance/repo-hygiene-claude` unmerged;
   `maintenance/context-slimming`, `claude/confident-faraday-1drq98` fully merged.

## Exact next task

Owner review of `claude/nexus-command-center`. If the progress file is approved: add the
atomic write/delete in `src/nexus/software_engineer/cycle.py` (`_advance`, `_persist`)
with tests in `tests/unit/test_software_engineer_*.py`; the reader
(`command_center/sources.py`, `PROGRESS_FILENAME`) and client already consume it.

## Resume and validation commands

```bash
uv venv --python 3.12 <outside repo> && uv pip install -e ".[dev]"
python -m ruff format --check . && python -m ruff check . && python -m mypy
python -m pytest -q && python -m nexus.lab.scenarios validate && docker compose config --quiet
python -m nexus.software_engineer.evaluation && python -m nexus.sentinelqa
cd web/command-center && npm ci && npm run typecheck && npm test && npm run build && npm run visual
python -m nexus.command_center serve   # http://127.0.0.1:8765
```

## Invariants that must not be violated

- Agents act only through narrow typed tools; evidence comes only from runtime code.
- PatchForge proposes; SentinelQA verifies; a human approves and merges. Draft PRs only;
  silence is never approval; autonomy requires `sentinel_review`.
- The Command Center is a view: GET-only, never records a decision, never authoritative.
- Memory carries knowledge, never authority. Governing paths change only through an ADR
  and owner review. Frozen Phase 5/6/7 evidence untouched. Never commit `.env`, `.nexus/`,
  model outputs, patches, bundles.
