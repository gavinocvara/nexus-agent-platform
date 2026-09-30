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
| PatchForge | Built (A-H, J); live run not executed | Contracts through Benchmark v0 plus an opt-in model engine boundary | ADR 0010 |
| SentinelQA-lite | Built | Independent verification against the pristine specification (content-locked tests, harness boundary, runner-integrity canary); fail-closed verdicts gate Atlas approval | ADR 0011, `docs/runbooks/sentinelqa.md` |
| Resident Software Engineer | Built, disabled by default | Bounded daily cycle: inspect, rank, run PatchForge + SentinelQA for mechanical and model-backed recipes, risk-classify, self-review, ask the owner; private provenance-backed memory; owner decisions by CLI or signed Slack command; verified draft pull requests only (never a merge) | ADR 0012, `docs/runbooks/software-engineer.md` |
| Command Center | Built, read-only (branch) | Spatial dashboard over the real records: orb network, live stream, replay of evaluation episodes, accessible data and activity views | ADR 0013, `docs/runbooks/command-center.md` |
| Engram | Planned | Shared validated knowledge | `ROADMAP.md` |

Lab request path:

```text
Client -> Gateway -> Users
                  -> Orders -> PostgreSQL
```

Code lives under `src/nexus/` (`services`, `lab`, `observability`, `diagnostics`,
`aegisops`, `evaluation`, `brain`, `atlas`, `patchforge`, `sentinelqa`,
`software_engineer`, `command_center`); the Command Center client lives in
`web/command-center`.

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
python -m nexus.patchforge.e2e_catalog      # deterministic gates, no model, no network
python -m nexus.patchforge.benchmark_corpus
python -m nexus.sentinelqa
python -m nexus.software_engineer.evaluation
```

`make validate` runs the first five (`make PYTHON=py validate` on Windows). Together they
mirror the `validate` job in `.github/workflows/ci.yml`; `compose-integration` runs the
integration suite against the Compose stack.

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
- SentinelQA verifies every proposal against the original tests, locked by content hash
  at the source commit. A candidate that changes, deletes, skips, or reconfigures the
  specification is rejected, and before any pass the runner must report a planted
  failing test, so code that silences the runner is caught. Evidence that cannot be
  trusted is inconclusive. Only a passed verdict unlocks human approval. Code written to
  recognise and spare that planted test remains a documented residual risk.
- Each agent's memory is private. Brain v1 is disabled by default and fails closed.
- The resident Software Engineer is disabled by default (`NEXUS_SOFTWARE_ENGINEER_ENABLED`),
  runs in `dry_run` mode unless the owner changes it, classifies every change by risk,
  never ships anything that touches its own governing rules or the documents agents take
  direction from, and treats silence as no decision. Autonomous shipping additionally
  requires a passed SentinelQA review and is off by default. Its memory is a private
  namespace that carries knowledge, never authority; owner rejections and failed attempts
  are always recalled. Slack commands are HMAC-verified, replay-protected, and accepted
  only from the configured owner; they record decisions and never publish. Credentials
  live only in the environment. The most it can ever do is open a draft pull request
  whose blobs, tree, commit, branch, and head were verified; a human merges.
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

Version `0.27.0`. Phases 0-8 are complete; Brain v1 ended with a documented negative
calibration. PatchForge Milestones A-H and J (SentinelQA-lite) are complete, and the
resident Software Engineer is built through draft-pull-request publication and Slack
owner decisions. The local gate for 0.27.0: 934 tests passed (23 integration tests
deselected), Ruff and strict mypy clean, E2E, Benchmark v0, SentinelQA (35 scenarios), and
engineer evaluation (30 scenarios) gates passed; CI (`validate`, `compose-integration`) is
green.

No real GitHub publication, Slack owner decision, or live model call has been made. The
first controlled GitHub exercise (`python -m nexus.software_engineer exercise-github`,
a dry run unless `--confirm-live`), the Slack exercise, and the first live PatchForge or
model-backed run each await explicit owner authorization. See `CODEX_HANDOFF.md` for the
current checkpoint and `ROADMAP.md` for the full sequence.
