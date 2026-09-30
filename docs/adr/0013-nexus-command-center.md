# ADR 0013: NEXUS Command Center (read-only spatial dashboard)

Status: Accepted for the first release (owner-requested, branch `claude/nexus-command-center`)

## Context

NEXUS already records what its agents do: the resident engineer writes a runtime-attested
`CycleRecord` per cycle, owner decisions and publications are separate typed files, the
executor keeps the PatchForge result and SentinelQA verdict per candidate, the engineer and
the AegisOps investigator each keep a private SQLite memory, and the diagnostics layer reads
lab health through fixed, bounded adapters. Nothing presents this as one system. The owner
asked for a top-level visual experience that makes the architecture understandable and lets
a viewer watch NEXUS work, without becoming a second authority.

## Decision

Build **NEXUS Command Center**: a thin, read-only FastAPI view layer
(`src/nexus/command_center`) over existing records, and a React + TypeScript client
(`web/command-center`) whose centerpiece is an interactive 3D orb network.

### Authority

- GET-only API. No route accepts a body, writes a file, sends a notification, calls a
  model, or touches GitHub or Slack. Tests enforce the route set and that serving leaves the
  state tree byte-identical.
- Owner decisions stay where they are: `/nexus ship|revise|reject` over signed Slack, or the
  `decide` CLI. A pending approval is **displayed** with the exact governed command to use;
  the dashboard never records one. Enabling any write path needs a separate owner decision
  and must call the existing governed interfaces.
- The serving modules never import `approval.decide`, `publish_approved_change`, notifier
  transports, the Slack handler, or the executor (import-boundary test).
- Binds `127.0.0.1` by default, rejects foreign `Host` headers (DNS rebinding), sends a strict
  CSP. Exposing it beyond loopback needs an authentication layer first (not built).

### Sanitized view models

The browser receives purpose-built view models, never raw records or settings:
every string is length-bounded and replaced with `[withheld: credential-shaped]` when
`contains_credential` matches; configuration is an allow-list of booleans and numbers
mirroring `preflight` (never variable values, the webhook URL, tokens, or the process id in
the lease); memory exposes counts plus bounded metadata of recent entries; lab scenarios
expose id, title, and target only (their `expected_root_cause` and symptoms are evaluator
ground truth and never leave the server); candidate patches are never sent, only the
runtime `ChangeSummary` statistics and hashes.

### Data provenance

| Display | Source | Kind |
| --- | --- | --- |
| Cycle in flight | `<state_root>/run.lock` (cycle id, start, expiry) | live, 1 s poll |
| Interrupted cycles | `cycles/*.interrupted.json` | recorded |
| Cycle history, phases, gates, risk, self-review, decision, budgets, notifications | `cycles/<id>.json` validated as `CycleRecord` | recorded |
| Pending owner question | `CycleRecord.approval_request` with no `decisions/<request_id>.json` | recorded |
| Owner decision | `decisions/<request_id>.json` (`OwnerDecision`) or the record's `owner_decision` | recorded |
| Draft PR publication | `publications/<request_id>.json`, or `change.publication` | recorded |
| SentinelQA verdict and findings | `runs/<cycle>/<candidate>/sentinel_verdict.json` (`SentinelVerdict`) plus `sentinel_*` gates | recorded |
| PatchForge activity | `IMPLEMENT`/`TEST` transitions, PatchForge gates, `ChangeSummary` | recorded |
| Engineer memory | `memory.sqlite3` opened `mode=ro` | recorded |
| AegisOps brain | `.nexus/brain/aegisops-investigator.sqlite3` opened `mode=ro` | recorded |
| Lab service health | `DiagnosticServiceLayer.get_system_health()` | live, 15 s poll |
| Lab incident catalog | `lab/scenarios/v1` via `nexus.lab.catalog` | static |
| Engineer configuration | `SoftwareEngineerSettings` allow-list (as `preflight`) | static |
| Replay episodes | engineer evaluation catalog, executed in a temporary directory | replay |

A display with no source shows an intentional empty state; nothing is filled with
invented telemetry.

### Live and replay

- **Live** is the default. Animation is driven only by state changes: a new lease starts a
  dispatch pulse to the resident engineer that stays lit until the lease is gone; a new
  cycle record plays back that record's real transition sequence (labelled *recorded*); a
  pending approval materializes the owner-question overlay. Idle means ambient motion only,
  in muted amber, never the blue activity energy.
- **Replay** re-executes curated scenarios of `nexus.software_engineer.evaluation` in-process
  at server start (real cycle runtime, policy, risk, review, and memory; scripted executor,
  fixture repository; no model, network, GitHub, or Slack). Each episode carries its
  `record_sha256`, which the evaluation gate proves byte-identical. Replay tempo is
  synthetic (recorded timestamps are a fixed evaluation clock) and says so. Replay is framed
  in a distinct hatched blue treatment with a persistent `REPLAY · EVALUATION CATALOG`
  banner; replay state never enters the live store.

### Missing instrumentation (proposed, not built)

A cycle's phase is observable only after its record is written; while it runs, only the
lease exists. The smallest safe addition is for `EngineeringCycle._advance` to replace
`cycles/active.progress.json` (cycle id, sequence, phase, reason, time) atomically and delete
it when the record lands. It adds no authority, but `src/nexus/software_engineer` is a
governing path, so it waits for the owner. The client already consumes a `phase` field on
the active run; the server will fill it when the file exists.

### Experience

- **Scene** (`three` via React Three Fiber): NEXUS core at the origin (obsidian surface with
  molten fissures, fragmented particle rings, a blue plasma shell that charges on dispatch);
  an Atlas governance ring every conduit passes through; five systems on a tilted orbit:
  AegisOps, PatchForge, SentinelQA, Resident Engineer, Memory; Engram as an unlit wireframe
  marked *not built*. Systems rest in graphite gray and ignite in their identity accent only
  when a real event reaches them. Conduits are braided procedural tubes whose vertex shader
  sways them; a pulse is a uniform travelling from core to target.
- **Navigation**: wheel and swipe step through NEXUS → AegisOps → PatchForge → SentinelQA →
  Resident Engineer → Memory → Overview with eased camera transitions; left-drag orbits,
  right-drag pans, pinch or Ctrl+wheel zooms, click focuses, Esc returns to overview, arrow
  keys and 1-7 mirror the rail. Manual orbit never fights the rail: steps start from the
  current pose.
- **HUD** (DOM, accessible): boxy condensed display type with framed selection, a left
  system menu, a right system display, a stage rail of real `CyclePhase` values, a status
  block, a controls legend, an owner-question overlay that grows from the core, a Data view
  (full text of every panel, also the fallback without WebGL), and an Activity view
  (transitions, signals, gates with evidence hashes, notifications, memory writes).

### Real time

Server-Sent Events at `/api/v1/stream`: a `snapshot` on connect and on change (at most one
per second), semantic `signal` events (`run.started`, `run.ended`, `cycle.recorded`,
`decision.pending`, `decision.recorded`, `publication.recorded`, `health.changed`), and
heartbeats. Server to client only.

### Dependencies (client, recorded reasons)

`react`/`react-dom` (UI), `three` + `@react-three/fiber` (declarative WebGL scene),
`@react-three/drei` (camera controls, performance monitor), `@react-three/postprocessing` +
`postprocessing` (bloom), `zustand` (store read inside the render loop without re-renders),
`@fontsource/*` (self-hosted fonts, no third-party requests). Dev: `vite`, `typescript`,
`vitest`, `@playwright/test`. No new Python dependency.

### Performance

The HUD shell loads first; the 3D scene is a lazy chunk. Quality tiers adjust device pixel
ratio, bloom, and particle counts from a performance monitor; reduced motion switches to
on-demand rendering with no ambient drift. Scene code reads the store inside `useFrame`, so
stream updates do not re-render the canvas tree.

## Consequences

- The dashboard can be wrong only by omission: every value traces to a file or a bounded
  diagnostic read listed above.
- Not in this release: a Compose service or Docker image for the client (`compose.yaml`
  and `Dockerfile` are governing), a CI job for the client (`.github` is governing), write
  actions, and the progress file above. Each is an owner decision.
