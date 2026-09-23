# ADR 0006: Single Bounded AegisOps Investigator

## Status

Accepted

## Context

Phase 5 needs a real model-driven investigator without introducing Atlas, memory,
handoffs, remediation, or ambient machine access. The model must reason from Phase 4
evidence while deterministic software retains control of capabilities, budgets,
provenance, failure handling, and evaluation ground truth.

## Decision

Implement exactly one `aegisops.investigator` with the OpenAI Agents SDK behind a
small `InvestigatorEngine` adapter. The SDK agent has a Pydantic output type, no
handoffs or sessions, and exactly the eleven Phase 4 function tools. Tool construction
fails closed unless the SDK implementations, immutable registry, and investigator
policy have identical names and every registry item remains read-only and low risk.

The SDK's local context contains only the `DiagnosticServiceLayer`, its
`DiagnosticSession`, and a run ID. It contains no scenario, evaluator, expected answer,
environment mapping, model credential, or write capability. Every tool dispatches to
the existing service layer. Tool exceptions are not converted into model-readable
error text, allowing a hard call-budget failure to terminate with a typed run status.

Add a UUID `tool_call_id` to each diagnostic result and its evidence-free audit event.
The session retains a defensive copy of each result in a separate in-memory evidence
ledger. A diagnosis cites an exact call ID, tool name, result path, observed value, and
short observation. The evaluator resolves that path against recorded evidence and
compares the claimed value; a matching ID alone is not sufficient.

Require a structured diagnosis with an explicit status, bounded hypotheses,
supporting and conflicting evidence, confidence, and a read-only next diagnostic
action. Instructions classify telemetry as untrusted data, require contradictory
evidence, forbid hidden reasoning disclosure and remediation, and require abstention
when evidence or diagnostic backends are insufficient.

Keep model selection and execution bounds in typed environment configuration. Live
execution is disabled by default and reads `OPENAI_API_KEY` only from the process
environment. Deterministic tests inject an `InvestigatorEngine`; they do not need a
key or network model call.

Place scenario activation and scoring under `nexus.evaluation`, which may import the
lab catalog. It gives the agent only one generic incident prompt, primes observable
symptoms through ordinary APIs, and always resets and verifies recovery in `finally`.
Reports contain typed run records, scores, and reproducibility hashes, but no prompts,
transcripts, chain of thought, or duplicated tool payloads. Local reports live below
ignored `.nexus/evaluations/aegisops`.

Disable Agents SDK tracing by default, independently of NEXUS service telemetry. This
avoids sending evaluator runs and tool contents to an external trace backend. An
operator may explicitly enable SDK tracing, while sensitive trace inclusion remains
disabled.

## Consequences

- The first probabilistic component sits behind deterministic capability and result
  contracts.
- Scenario truth cannot enter agent context through the production package boundary.
- Exact evidence claims can be machine-checked without copying evidence into audit
  records.
- Time, turns, and diagnostic calls have explicit upper bounds.
- The included tests validate wiring and scoring, not live-model diagnostic quality.
- Run state, evidence, and audit records remain process-local until Atlas provides a
  durable execution and policy plane.
