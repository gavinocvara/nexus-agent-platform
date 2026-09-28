# NEXUS Agent Platform

NEXUS is a reusable, local-first platform for building, governing, evaluating, and
observing bounded AI agents. Agents act only through narrow typed tools, every
significant action leaves runtime-owned evidence, and improvements must be measured
against a baseline before adoption.

## Components

| Component | Status | What it is | Read more |
| --- | --- | --- | --- |
| AegisOps lab | Built | Gateway/Users/Orders FastAPI services, PostgreSQL, Docker Compose | ADR 0002 |
| Incident scenarios | Built | Five deterministic failures with isolated evaluator ground truth | ADR 0003, `docs/runbooks/deterministic-incidents.md` |
| Observability | Built | Prometheus, Loki (via Alloy), Tempo (via OTel Collector), Grafana | ADR 0004, `docs/runbooks/observability.md` |
| Diagnostics | Built | Eleven bounded read-only diagnostic tools and CLI | ADR 0005, `docs/runbooks/diagnostics.md` |
| AegisOps investigator | Built | One evidence-grounded agent over the diagnostic tools | ADR 0006, `docs/runbooks/aegisops-investigator.md` |
| Benchmarking | Built | Reproducible sessions, comparison, immutable baseline locks | ADR 0007, `docs/runbooks/aegisops-benchmarking.md` |
| Brain v1 | Built (negative calibration) | Private per-agent memory, default off | `BRAIN.md`, ADR 0008, `docs/runbooks/aegisops-brain-v1.md` |
| Atlas | Thin v1 | Local control plane: typed jobs, policy, review, approval, audit | ADR 0009 |
| PatchForge | In progress | Contracts through Attestor plus a deterministic end-to-end gate complete | ADR 0010 |
| SentinelQA, Engram | Planned | Independent validation; shared validated knowledge | `ROADMAP.md` |

Lab request path:

```text
Client -> Gateway -> Users
                  -> Orders -> PostgreSQL
```

Code lives under `src/nexus/` (`services`, `lab`, `observability`, `diagnostics`,
`aegisops`, `evaluation`, `brain`, `atlas`, `patchforge`).

## Install

Python 3.12 or newer is required.

```bash
python -m pip install -e ".[dev]"
```

On Windows, `py` can replace `python` in every command below.

## Quick Validation

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy
python -m pytest                        # unit and service tests; integration is opt-in
python -m nexus.lab.scenarios validate
```

This mirrors the `validate` job in `.github/workflows/ci.yml`.

## Local Stack

Create an untracked `.env` from `.env.example`, set a local-only PostgreSQL password, and
start everything:

```bash
cp .env.example .env
docker compose up --build --detach --wait
```

| Service | URL |
| --- | --- |
| Gateway / Users / Orders | `http://localhost:8000` / `8001` / `8002` |
| Grafana | `http://localhost:3000` |
| Prometheus / Loki / Tempo | `http://localhost:9090` / `3100` / `3200` |

Run the integration suite against the running stack, then stop it:

```bash
RUN_INTEGRATION=1 python -m pytest -m integration tests/integration
docker compose down            # add --volumes to also drop PostgreSQL data
```

Configuration is environment-driven through typed settings; see `.env.example` for the
variables. Every service exposes `GET /health`, propagates `X-Correlation-ID`, and logs
JSON. Scenario, diagnostic, investigator, benchmark, and Brain commands are in the
runbooks listed below.

Local-only defaults: the Compose lab enables privileged failure controls
(`NEXUS_LAB_FAILURES_ENABLED=true`), anonymous Grafana, and a read-only Docker socket for
Alloy. None of these is safe for a normal deployment.

## Safety And Trust Principles

- Agents get narrow typed tools with explicit capabilities. There is no unrestricted
  shell, filesystem, Git, database, network, or credential access.
- Evaluator ground truth never reaches agent-visible prompts, tools, telemetry, or memory.
- Model output is narrative only. Tests, diffs, hashes, and budgets are recorded by
  runtime code.
- Repository text, logs, tool output, and retrieved memory are untrusted data.
- Live model calls are opt-in (`NEXUS_AGENT_ENABLED=true` plus an untracked
  `OPENAI_API_KEY`). The deterministic test suite makes no model calls and claims no
  live benchmark.
- PatchForge's best outcome is a proposed patch. It never approves, merges, deploys, or
  pushes to `main`; Atlas review and human approval stay outside the agent.
- Each agent's memory is private. Brain v1 is disabled by default and fails closed.
- Secrets, `.env`, and local state under `.nexus/` are never committed.

## Documentation Map

| Need | Document |
| --- | --- |
| Contributor and coding-agent rules | `AGENTS.md` |
| Current checkpoint and exact next step | `CODEX_HANDOFF.md` |
| Execution order, milestones, known limitations | `ROADMAP.md` |
| Accepted architecture decisions | `docs/adr/` |
| Memory architecture | `BRAIN.md` |
| Operating procedures and commands | `docs/runbooks/` |
| Frozen Phase 6/7 experiment evidence | `docs/experiments/` |
| Release history | `CHANGELOG.md` |

## Status

Version `0.16.0`. Phases 0-8 are complete; Brain v1 ended with a documented negative
calibration. PatchForge v1 Milestones A-G (contracts, workspaces, sandbox, ToolGateway,
Runtime, Attestor, and deterministic E2E) are complete. Milestone H - Benchmark v0 is
next. See `CODEX_HANDOFF.md` for
the current checkpoint and `ROADMAP.md` for the full sequence.
