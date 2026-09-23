# AegisOps Investigator Runbook

## Boundary

`aegisops.investigator` is one read-only agent. It can invoke only the eleven tools in
the Phase 4 diagnostic registry. It has no shell, filesystem, browser, web search,
MCP, SQL, Docker, GitHub, lab control, memory, handoff, code execution, or write tool.
It reports diagnoses and read-only next diagnostic steps; it cannot remediate.

## Configuration

Install the project, create an untracked `.env`, and set:

```text
NEXUS_AGENT_ENABLED=true
NEXUS_AGENT_MODEL=gpt-5-mini
NEXUS_AGENT_MAX_TURNS=10
NEXUS_AGENT_TIMEOUT_SECONDS=120
NEXUS_AGENT_MAX_TOOL_CALLS=12
NEXUS_AGENT_SDK_TRACING_ENABLED=false
OPENAI_API_KEY=<local secret>
```

Never commit `.env` or the API key. `NEXUS_AGENT_ENABLED` defaults to false. Model,
turn, elapsed-time, and diagnostic-call bounds are operator configuration rather than
agent inputs.

SDK tracing is distinct from the lab's OpenTelemetry signals and defaults to off.
When explicitly enabled, sensitive trace inclusion remains disabled. Evaluation
reports do not contain prompts, transcripts, hidden reasoning, or full evidence.

## Investigate

Start the full Compose environment, generate or activate an incident through the
evaluator controls in a separate operator workflow, then run:

```powershell
py -m nexus.aegisops investigate
```

The CLI always supplies the same generic incident prompt. It prints one typed run
record and exits `0` only when model execution completed. Disabled execution, missing
credentials, tool-budget exhaustion, maximum turns, timeout, invalid output, and
model errors produce explicit statuses and exit `2`.

## Live Evaluation

With Compose healthy and the live-agent settings enabled:

```powershell
py -m nexus.evaluation.aegisops --runs 1
```

The harness evaluates all five version 1 scenarios. For each repetition it verifies
baseline health, activates the scenario, produces its expected symptom through an
ordinary API, sends only the generic prompt to the investigator, scores the result,
then resets and verifies recovery in `finally`.

Reports are written under `.nexus/evaluations/aegisops/`, which Git ignores. They
record timestamp, Git SHA, NEXUS version, model, instruction and registry hashes,
scenario schema, limits, scores, latency, tool and turn counts, and token usage when
the SDK supplies it. They do not establish a benchmark until a real live run has been
completed and reviewed.

## Scoring

Per-run scoring records component and failure-class correctness, exact diagnosis,
confidence, abstention, evidence-reference validity, unsupported claims, unsafe
verbal action requests, tool calls, turns, latency, and optional token counts.
Evidence is valid only when its call ID and tool match a recorded result and the
referenced result path contains the claimed value.

## Deterministic Validation

```powershell
py -m pytest tests/unit/test_aegisops_contracts.py
py -m pytest tests/unit/test_aegisops_runtime.py
py -m pytest tests/unit/test_aegisops_evaluation.py
```

These tests use scripted engines and do not call a model. They verify the tool
boundary, schema, provenance, budgets, timeout and turn failures, evaluator isolation,
prompt-injection handling, scoring, all-scenario iteration, and mandatory reset. They
are wiring and safety tests, not fabricated model-accuracy results.
