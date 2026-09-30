# Runbook: NEXUS Command Center

The Command Center (ADR 0013) is a read-only spatial dashboard over existing NEXUS records.
It never records a decision, sends a notification, calls a model, or touches GitHub or
Slack. Owner decisions stay on their governed channels (signed Slack `/nexus`, or the
`decide` CLI); the dashboard only shows the exact command.

## Run it

```bash
# once: Python environment (see README) and the client build
cd web/command-center && npm ci && npm run build && cd ../..

# serve API, event stream, and the built client on http://127.0.0.1:8765
python -m nexus.command_center serve
```

Development with hot reload: run `serve` as above, then `npm run dev` in
`web/command-center` (Vite on `127.0.0.1:5173`, proxying `/api` to `:8765`).

### With Docker Compose

The service is opt-in (profile `command-center`) because it bind-mounts the host's engineer
state read-only and must never let Docker create that directory (on Linux it would be
root-owned and the resident engineer could no longer write its own state):

```bash
mkdir -p .nexus/software_engineer                  # as your user, once
docker compose --profile command-center up -d      # add the lab too: it is the same stack
# open http://127.0.0.1:8765  (loopback only; there is no authentication)
docker compose --profile command-center ps command-center   # healthy after a few seconds
```

In the container the dashboard reads exactly that directory (read-only), reads lab health
over the Compose network, runs as a non-root user on a read-only root filesystem with a
`/tmp` tmpfs, drops all capabilities, and has no Docker socket and no secret. The engineer's
environment is not shared with it, so the snapshot reports the engineer's configuration as
not visible (the ship-policy gates, which are code, still show). The AegisOps brain stays
outside Compose (ADR 0008), so the container reports it as not visible; run the Command
Center on the host to see it. Where the Debian mirrors
are unreachable, build from a base that already ships git:
`NEXUS_COMMAND_CENTER_PYTHON_IMAGE=python:3.12 docker compose --profile command-center build`.
Grafana, Prometheus, Loki, and Tempo stay the deep-observability tools on their own ports.

Read-only one-shots:

```bash
python -m nexus.command_center snapshot   # the sanitized live snapshot as JSON
python -m nexus.command_center replay     # capture replay episodes, print record hashes
```

## What it reads

| Source | Default location | Setting |
| --- | --- | --- |
| Resident engineer state (cycles, lease, decisions, publications, run artifacts) | `.nexus/software_engineer` | engineer `NEXUS_SOFTWARE_ENGINEER_STATE_ROOT` or `NEXUS_COMMAND_CENTER_ENGINEER_STATE_ROOT` |
| Engineer private memory (opened `mode=ro`) | `.nexus/software_engineer/memory.sqlite3` | `NEXUS_COMMAND_CENTER_ENGINEER_MEMORY_PATH` |
| AegisOps brain (opened `mode=ro`) | `.nexus/brain/aegisops-investigator.sqlite3` | `NEXUS_COMMAND_CENTER_BRAIN_PATH`, `..._BRAIN_ENABLED` (false in Compose) |
| Lab health (diagnostics layer, fixed endpoints) | `localhost:8000-8002` | `NEXUS_COMMAND_CENTER_HEALTH_ENABLED`, `..._HEALTH_INTERVAL_SECONDS` |
| Lab incident catalog (id, title, target only) | `lab/scenarios/v1` | `NEXUS_COMMAND_CENTER_SCENARIO_DIRECTORY` |
| Replay episodes | engineer evaluation catalog, run in a temp dir at start | `NEXUS_COMMAND_CENTER_REPLAY_ENABLED` |

A missing source is shown as `ABSENT` with an explanation, never filled in. On a fresh
clone everything but the lab catalog and replay is absent, and the lab is `UNAVAILABLE`
until `docker compose up -d`.

## Live, recorded, replay

- **LIVE** (amber): driven by state changes over Server-Sent Events. A lease file
  (`run.lock`) lights the resident engineer with a slow heartbeat. While the cycle runs, its
  runtime publishes `cycles/active.progress.json` at every phase transition (ADR 0013), and
  the dashboard shows that phase only when the file names the lease holder: the rail marks
  it, the core dispatches a pulse to the systems that phase involves, and UNDERSTAND lights
  Memory. In IMPLEMENT with the PatchForge executor, PatchForge and SentinelQA are shown as a
  half-lit **pipeline**: they run one after the other inside one call, and which one is
  working is not observable. The file disappears when the cycle ends, however it ends.
- **RECORDED playback**: when a new cycle record appears, its real transition order plays
  once, labelled `RECORDED PLAYBACK`.
- **REPLAY** (blue, hatched, `NOT LIVE` banner): curated evaluation scenarios re-executed
  in-process. The cycle runtime, policy, risk, review, and memory are real; a scripted
  executor stands in for PatchForge and SentinelQA, and the orbs it plays are marked
  `SCRIPTED`. Replay tempo is synthetic. Each episode shows its `record_sha256`, which the
  engineer evaluation gate proves byte-identical.

## Controls

Scroll or arrow keys step through Overview, NEXUS, AegisOps, Resident Engineer,
PatchForge, SentinelQA, Memory/Engram; `1`-`7` jump; `Esc` returns to the overview (or
closes Data/Activity). Drag orbits, right-drag pans, `Ctrl`+scroll or pinch zooms, click an
orb to focus it. In replay, `Space` plays or pauses. The **Data** view is the complete
text equivalent (and the automatic fallback without WebGL); **Activity** is the technical
record: transitions, gates with evidence hashes, signals, candidates, self-review,
notifications, and the cycle report.

URL parameters for demos and tests: `?stop=N`, `?view=data|activity`, `?quality=high|medium|low`,
`?mode=replay&episode=<name>&t=<seconds>&paused=1`, `?webgl=0`, `?still` (no animation).

## Security

- GET-only API; the route set and an import boundary are enforced by
  `tests/unit/test_command_center.py`, which also proves serving leaves the state tree
  byte-identical.
- Binds `127.0.0.1`; `Host` must be in `NEXUS_COMMAND_CENTER_ALLOWED_HOSTS`
  (DNS-rebinding guard). There is no authentication layer: do not expose it beyond
  loopback without adding one.
- Strict CSP (`script-src 'self'`, no inline scripts, same-origin fonts, no third-party
  requests); `no-store` on API responses.
- Payloads are view models: bounded text, credential-shaped strings withheld, settings as
  booleans (as `preflight` prints them), no lease pid, no candidate patch, no evaluator
  ground truth.

## Validation

```bash
python -m pytest -q tests/unit/test_command_center*.py tests/unit/test_software_engineer_progress.py
cd web/command-center
npm run typecheck && npm test && npm run build
npm run visual            # starts `serve` if nothing listens on :8765; SwiftShader WebGL
node visual/capture.mjs <out-dir>   # named screenshots for human review

# Compose: the service plus containment (with the stack up, as CI does)
RUN_INTEGRATION=1 NEXUS_INTEGRATION_REQUIRE_COMMAND_CENTER=1 \
  python -m pytest -q -m integration tests/integration/test_command_center_stack.py
```

CI runs the client job `command-center` (npm ci, typecheck, vitest, build, Playwright
against a real server, screenshots uploaded as the `command-center-browser` artifact) and,
in `compose-integration`, the Compose service with its integration tests and containment
checks (exactly one read-only mount, read-only root filesystem, non-root, no secret-like
variables, loopback port, nothing written to the state tree).

The visual suite asserts layout invariants (no horizontal overflow, panels in bounds), a
non-blank canvas, keyboard navigation, unmistakable replay labelling, GET-only network
traffic, the no-WebGL fallback, and the small-screen layout. It does not pixel-compare the
WebGL scene: GPU output differs across machines. `capture.mjs` produces the reviewed set
(overview, each system, replay dispatch and owner step, data, activity, 1920 and 390 wide).

## Performance

Measured with `?capture=1` (renderer counters exposed on `window.__nexusRenderer`), full
frame including post-processing, 1440x900 overview: high tier 77 draw calls, ~122k
triangles, 8k points; low tier (no post-processing) 53 calls, ~42k triangles. JS heap about
13 MB. The HUD shell is ~86 KB gzipped; the scene chunk (~289 KB gzipped) loads lazily. A
performance monitor steps quality down (DPR, bloom, particle and debris counts) on sustained
frame drops. Headless SwiftShader frame times are CPU rasterization and are not a GPU
benchmark. The most expensive shader is the orb surface (Worley fractures); the core at
NEXUS focus covers about half the viewport.

## Troubleshooting

- Blank page with "client has not been built": run the client build above.
- Fonts missing or console CSP errors: the build must not inline assets
  (`assetsInlineLimit: 0` in `vite.config.ts`).
- `400 Invalid host header`: add the host to `NEXUS_COMMAND_CENTER_ALLOWED_HOSTS`.
- Replay `PREPARING` for more than a few seconds: check the server log; capture needs `git`.
- `bind source path does not exist` from Compose: create `.nexus/software_engineer` as
  your user; Compose is configured never to create it.
