# Codex Handoff

Current state only. History: Git and `CHANGELOG.md`. Rules and invariants: `AGENTS.md`.
Subsystem status on `main`: `PROJECT_STATE.md`.

## Checkpoint

| Item | Value |
| --- | --- |
| Version | `0.27.0`; no bump since |
| `main` | `95c1c96`: core label fix (direct, owner request) on PR #5's merge `fec1cbb0b78735ccbe8fcb1a59b81c7b45e322e1`. CI run `36834353149`: `validate` and client browser checks green; `compose-integration` hit a PyPI read timeout building the lab `orders` image, before any test |
| PR #5 | Merged. Branch head `836722d` green on `validate`, `compose-integration`, `command-center` (run `36677641470`) |
| Active branch | none |

## Command Center (landed; ADR 0013, `docs/runbooks/command-center.md`)

- `src/nexus/command_center/`: GET-only view models, SSE, replay of 8 evaluation scenarios.
  Client in `web/command-center/` (React + R3F).
- Live phase (owner-approved governing path): `src/nexus/software_engineer/progress.py`
  writes `cycles/active.progress.json` atomically at each `_advance`; removed in `run()`'s
  `finally` before the lease is released; failures stop publishing, never fail the cycle.
- CI: job `command-center`; `compose-integration` runs the profile and containment checks.
- Compose profile `command-center`: `127.0.0.1:8765`, one read-only mount
  (`.nexus/software_engineer`), read-only rootfs, non-root, no caps, secrets or socket.
  The AegisOps brain is not mounted (ADR 0008; `tests/unit/test_brain.py`) and shows as
  not visible there.
- Core label (`95c1c96`): mounts once R3F connects events; the visual suite checks all 7.
- Not built (owner decisions): write actions; authentication or non-loopback exposure.

## Validation (PR #5 head `836722d`, local Linux, Python 3.12.3, Node 22)

- Ruff, strict mypy (139 files) clean; pytest **971 passed, 28 skipped** (integration).
- Engineer evaluation and SentinelQA gates passed; scenarios (5) and Compose config valid.
- Client: typecheck, vitest 8/8, build, Playwright 6/6 (SwiftShader).
- Compose integration with the profile: 28 passed; CI containment step passed locally.
  Images were built from sandbox-only CA-trusting local bases (Debian mirror denied).
- GitHub Actions on `836722d`: `validate`, `compose-integration`, `command-center` all green.

## Known risks

- No authentication: loopback only (`allowed_hosts`, port binding).
- `test_real_docker_sandbox_terminates_on_output_limit` failed once in several local
  integration runs (untouched code; passes alone).
- The default image installs git from Debian; `NEXUS_COMMAND_CENTER_PYTHON_IMAGE` overrides.
- No real-GPU frame timing exists (SwiftShader only).
- A real `propose` cycle may exhaust the default 1800 s budget.

## Owner decisions required (nothing below has been done)

1. `nexus.__version__` reports `0.18.0` against `0.27.0`: deliberately untouched.
2. Finish the Slack exercise (`/nexus bogus` and non-owner re-check pending; a live owner
   decision already works) and authorize the first real `decide` + `publish`.
3. Remote branch cleanup: `maintenance/repo-hygiene-claude` unmerged;
   `claude/nexus-command-center` and `claude/fix-core-label` fully merged, deletable.

## Frozen evidence (unchanged, never modify)

Phase 5 investigator hashes, Phase 6 `aegisops-memoryless-v1` baseline and lock, Phase 7
protocol, calibration evidence, frozen snapshot and self-report (`docs/experiments/`;
pinned by tests).

## Exact next task

None is authorized; the next checkpoint is an owner decision. Per `ROADMAP.md` (After
PatchForge v1, step 2) it is the resident engineer's Slack owner-command exercise
(`/nexus bogus`, non-owner re-check) and then the first owner-authorized real `decide` +
`publish` or model run. No further Command Center work is authorized.

## Resume

```bash
uv venv --python 3.12 <outside repo> && uv pip install -e ".[dev]"
python -m ruff check . && python -m mypy && python -m pytest -q
cd web/command-center && npm ci && npm run typecheck && npm test && npm run build
mkdir -p .nexus/software_engineer && docker compose --profile command-center up -d
```
