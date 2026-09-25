# ADR 0008: Agent-Private Brain v1

## Status

Accepted

## Context

The locked `aegisops-memoryless-v1` baseline establishes measurable behavior for one
bounded investigator. Phase 7 adds durable private experience without changing the
model, eleven tools, instructions, scenarios, scoring, Diagnosis schema, permissions,
12-call diagnostic budget, 10-turn limit, or 120-second agent loop. Memory is untrusted
input and evaluator ground truth must remain structurally inaccessible.

## Decision

Give only `aegisops.investigator` a private SQLite store outside the lab Compose project,
PostgreSQL volume, and observability stack. Brain has three explicit modes:
`disabled`, `learn`, and `frozen_eval`. Disabled is the exact memoryless path. Learn may
write after a run. Frozen evaluation opens a hash-verified read-only snapshot, performs
no writes, and verifies equal pre/post logical hashes.

The writer accepts one exact `AgentObservableRun` model, constructed field-by-field in
the investigator runtime. It contains agent-run times and identity, runtime completion,
an optional self-reported diagnosis, payload-free tool-call metadata, turns, and token
usage. It contains no scenario, evaluator, score, correctness, benchmark session,
repetition, ordering, recovery, reset, or harness data. Brain imports neither
`nexus.evaluation` nor `nexus.lab`.

All diagnoses are stored as `self_reported/unverified`; confidence never verifies a
claim and is not used for retrieval ranking. Episodic records retain the historical
claim, safe evidence patterns, path, and runtime outcome. Procedural records are only
`observed_trajectory/unverified/candidate` and cannot be evaluator-promoted. Conflicting
self-reports over the same path are marked disputed with both provenance chains kept.

Every record carries a stable UUID, strict agent/namespace, schema version, validity
interval, private agent-run and tool-call provenance, lifecycle state, supersession,
retention policy, and version. Unknown fields, secret-shaped values, evaluator harness
markers, invalid namespaces, malformed rows, and identity/policy memory types fail
validation.

Retrieval uses only the unchanged incident prompt and run start as a pinned `as_of`.
It performs one separately timed query, filters active and age-valid records, ranks by
deterministic lexical overlap, prefers candidate procedures, and ties by timestamp and
record ID. Count, rendered-character, estimated-token, and two-second time bounds are
independent of the 12 diagnostic calls and 10 turns. No live-system observation enters
the retrieval key.

The generic prompt contains no incident-specific signal. In Brain v1, procedural
memories therefore behave mainly as a learned reusable context prefix, lexical ties tend
toward recency, and incident-specific episodic records may not match. Learning order is
experimentally material and must follow a fixed pre-registered seed. Unverified
self-reports can anchor later runs; the targeted calibration result is not a basis for
tuning this policy.

Selected records are canonical JSON with delimiter characters escaped inside a block labeled
untrusted historical data. The block is appended to the user input, never to
`INVESTIGATOR_INSTRUCTIONS`. It cannot alter settings, tool registry, policy, identity,
or budgets, and its tool-call IDs cannot satisfy current-run evidence validation.

Brain identity records schema and record-schema hash, writer version/allowlist hash,
retrieval algorithm and bounds, pinned-as-of policy, tokenizer identity, renderer hash,
mode, protocol hash, snapshot hash, and memory-type counts. Per-run audit records
retrieval status/latency, ranked record IDs/content hashes, rendered hash and size,
truncation, Brain-attributable input tokens, write count/latency, and pre/post hashes.

Canonical snapshot identity is SHA-256 over NFC-normalized, sorted-key, compact UTF-8
JSONL ordered by record ID. Snapshot export also produces a SQLite file for read-only
execution. Hash and retrieval replay are tested across processes and `PYTHONHASHSEED`
values.

## Experiment Boundary

The generic prompt makes pre-run Brain v1 primarily a reusable procedural prefix, not
incident-specific episodic recall. The committed protocol pre-registers the authorized
targeted-learn and frozen-smoke calibration and a deferred leave-one-scenario-out study
with contemporaneous memoryless and placebo arms. Calibration cannot support a causal
learning claim, and Brain sessions cannot be locked as official baselines.

The historical schema-3 baseline lock remains byte-for-byte unchanged. Future locks use
a separate portable lock schema with POSIX paths relative to benchmark storage and
digests for manifest, summary, and every run. Legacy locks resolve by session ID under
the current storage root and treat their absolute path as informational only.

## Failure Semantics

Brain-enabled storage failure, timeout, hash mismatch, write failure, or frozen mutation
produces explicit `brain_failure`. The model is not called after retrieval failure, and
the result is never mixed into a memoryless arm. Brain writes finish synchronously
before a learn run returns.

## Deferred Work

The 100+ run held-out/placebo study is intentionally not authorized. Placebo snapshot
generation and interleaved fold orchestration will be implemented only when that study
is approved. Semantic and reflective memory, supervised confirmed outcomes, Engram,
cross-agent retrieval, vectors, and shared infrastructure are also deferred.

## Consequences

- Memoryless Phase 6 behavior remains reproducible with `NEXUS_BRAIN_MODE=disabled`.
- SQLite is adequate for one private local agent and survives lab volume resets and
  database faults without becoming diagnostic evidence.
- Wrong high-confidence diagnoses remain visible unverified history and may be disputed;
  they never become ground truth.
- Every Brain-enabled result is attributable to a fixed protocol, configuration, and
  frozen snapshot identity.
