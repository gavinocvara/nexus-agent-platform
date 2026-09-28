# Brain v1 Calibration and Frozen-Smoke Report

## Scope

This report closes the authorized Phase 7 calibration sequence without changing Brain
behavior. It records the single targeted learn run and the five-scenario frozen smoke,
then separates observed facts from deterministic mechanism inferences and hypotheses
that require a controlled experiment.

- Targeted learn session: `c6832bae-feb7-4216-933e-139c734c2173`
- Frozen smoke session: `4776dc1c-6f7a-41d1-b75b-e6c70856923c`
- Repository: `f77313e7e624b68750578b2b25b90ba8123fab62`
- NEXUS: `0.8.1`
- Model: `gpt-5.6-sol`
- Retrieval: `bounded-lexical-v1`
- Protocol SHA-256:
  `c6c3cff80da44c6fbc486a57b4eaf1ea6215c1ba923c1afed75d6717f94368ed`

The ignored local artifacts were inspected read-only. No live model call was made for
this postmortem.

## Frozen Snapshot

- Logical SHA-256:
  `9e7d36376c6b0c8c77d5a3f94cafcea22cb2a53a313986a2a3fe6aed4f0fd3b6`
- SQLite file SHA-256:
  `eeb6e6604f032ac925a981ab249f189418bd92a0005801f513faeb3fd5636bb5`
- Memory counts: one episodic and one procedural, both active
- Every smoke run recorded the expected logical hash before and after execution
- Every smoke run recorded zero memory writes

Smoke artifact SHA-256 values:

- Manifest: `2b4b01587e09b09b03e280b7523eb62198f197a1921487599b7c25e827c13211`
- Summary: `869ca3690ff7c3bf3d68ddcaed7cd3e680e7b130ccc0ef58a82e59eba41b4c8b`
- Runs 1 through 5:
  `3c49799c07247882d8e5bf8315c9b72a9bae3d44fea2f73babc48b2789629dad`,
  `fbb0e0393f127e5f42d337940f507099e376b516b6adaa42186dbda3796c8c48`,
  `f6bc2f1110e71c64117b8ec3163b74ba7c024bf406a856a41b9ade9b3d9c403f`,
  `3cd9773a24ab6c68db74c8940ff8f648bd2883a72edd5e7d1311b4a7224faf44`,
  and `62bc00736a5ddce2bc1fe0a0e2cef4f965491923ad2230b2dc2ed93298a2c265`

## Retrieved Procedural Memory

The complete safe structured payload and lifecycle envelope for
`8eef2c33-9acb-52b3-8329-11345784890c` are:

```json
{
  "id": "8eef2c33-9acb-52b3-8329-11345784890c",
  "schema_version": 1,
  "agent_id": "aegisops.investigator",
  "namespace": "aegisops.investigator",
  "memory_type": "procedural",
  "trigger_terms": [
    "investigate",
    "diagnostic",
    "postgres",
    "service_unavailable"
  ],
  "candidate_next_steps": [
    "get_database_health",
    "get_system_health",
    "get_request_summary",
    "get_service_health",
    "get_dependency_summary",
    "get_recent_errors",
    "search_logs"
  ],
  "redundant_patterns": [
    "get_dependency_summary:repeated:2",
    "get_request_summary:repeated:3",
    "get_service_health:repeated:2"
  ],
  "budget_risk_sequences": [],
  "evidence_combinations": [
    "get_database_health:prometheus:success:results-1:error-none:status-none",
    "get_system_health:service_health:success:results-4:error-none:status-none",
    "get_request_summary:prometheus:success:results-1:error-none:status-none",
    "get_request_summary:prometheus:success:results-1:error-none:status-none",
    "get_request_summary:prometheus:success:results-1:error-none:status-none"
  ],
  "provenance": {
    "source": "agent_observable_run",
    "agent_run_id": "76c66ad1-f1d7-40de-9a24-23e43f027d15",
    "tool_call_ids": [
      "413aee54-7db0-4553-9374-e49cd9f1afd3",
      "317c666e-abde-4d69-a788-d69cd0f3e08f",
      "796f217f-bd8e-461b-8e09-efad32651927",
      "7045ac00-0646-4368-8893-5f7252e5db66",
      "3e929a31-151c-466e-83a6-9f3c7bd338cf",
      "ed9ece5d-c393-4789-93c8-70794efb20ce",
      "3bd0b312-ba1d-458f-883d-47304777e51d",
      "f1efe920-5199-48e2-922e-f57fd77be92c",
      "32d09306-8c1b-4b2d-9ce0-f24dcaf389ab",
      "e619d607-3d84-4235-a075-e615280b13d5",
      "b537e319-05b9-4d4a-8fc5-5cd9c01f2e22"
    ]
  },
  "source_episode_ids": [
    "900740a4-8a60-5d41-8bf1-5f9d8e8ffa1a"
  ],
  "lifecycle": {
    "state": "active",
    "verification_status": "unverified",
    "derivation": "observed_trajectory",
    "procedure_status": "candidate",
    "retention_policy": "private_brain_v1",
    "version": 1,
    "supersedes": [],
    "created_at": "2026-09-25T06:03:25.594443Z",
    "valid_from": "2026-09-25T06:03:13.404128Z",
    "valid_to": "2026-09-25T06:03:25.594443Z"
  }
}
```

The source episodic record remains active and unverified. Its historical 0.97-confidence
self-report was not corrected, removed, promoted, or exposed as benchmark truth.

## Frozen-Smoke Result

Frozen calibration observed 0/5 completion with the same unverified procedural memory
retrieved in all five scenarios and 100% diagnostic tool-budget exhaustion. This is
consistent with procedural-memory anchoring but is not a causal comparison because no
contemporaneous memoryless or placebo arm was run.

All five runs ended after two turns and 12 successful diagnostic calls with
`tool_budget_exceeded`. There were no backend failures and no Brain failures. Retrieval
succeeded in 5/5 runs, the memory hit rate was 100%, no memory was written, and each run
added 420 estimated Brain-attributable input tokens. The episodic record was not
retrieved. Each run's `accounting_complete` flag was false at budget termination.

## Tool-Sequence Comparison

The procedural record stores a seven-item ordered `candidate_next_steps` list. Its source
episode stores an 11-call diagnostic path. Longest common subsequence (LCS) measures
ordered overlap while permitting intervening calls; multiset overlap measures repeated
tool overlap while ignoring order.

| Scenario | Exact prefix | Candidate LCS | Candidate multiset | Source-path LCS | Source multiset |
| --- | ---: | ---: | ---: | ---: | ---: |
| `orders_database_unavailable` | 2 | 4/7 | 6/7 | 7/11 | 10/11 |
| `orders_latency` | 1 | 4/7 | 5/7 | 7/11 | 8/11 |
| `orders_unavailable` | 1 | 5/7 | 7/7 | 8/11 | 10/11 |
| `users_latency` | 1 | 5/7 | 6/7 | 8/11 | 10/11 |
| `users_unavailable` | 1 | 5/7 | 6/7 | 8/11 | 10/11 |

Across the five runs, candidate-list LCS was 23/35 (65.7%) and candidate multiset
overlap was 30/35 (85.7%). Source-path LCS was 38/55 (69.1%) and source-path multiset
overlap was 48/55 (87.3%). All five runs began with `get_database_health`; only
`orders_database_unavailable` also matched the stored second call,
`get_system_health`.

For the first seven observed calls, exact-position agreement with the candidate list was
6/35 (17.1%) and LCS overlap was 15/35 (42.9%). For the first 11 observed calls,
exact-position agreement with the source path was 8/55 (14.5%) and LCS overlap was
36/55 (65.5%). The starts therefore share tool vocabulary and partial order, but are not
close positional replays of the stored path.

The stored redundant counts were request summary 3, dependency summary 2, and service
health 2. Smoke counts for those tools were respectively `3/2/2`, `5/2/0`, `3/2/1`,
`3/2/2`, and `3/2/2`. Twelve of 15 stored tool/count patterns matched exactly; 13 of 15
stored tools were repeated at least twice. Every run also called `get_recent_errors`
three times, a repeated pattern not present in the procedural record. No observed
sequence can match a recorded budget-risk pattern because `budget_risk_sequences` is
empty.

## Deterministic Retrieval Analysis

The generic prompt tokenizes to:

`abstain`, `aegisops`, `affected`, `approved`, `available`, `class`, `component`,
`diagnostic`, `evidence`, `failure`, `identify`, `insufficient`, `investigate`, `likely`,
`most`, `when`.

Procedural retrieval tokens come only from `trigger_terms` and
`candidate_next_steps`. The record overlaps on `diagnostic` and `investigate`, producing
`2 * 10 + 2 = 22`: ten points per distinct lexical overlap plus the fixed two-point
procedural bonus.

Episodic retrieval tokens come only from `tools_used`, `diagnostic_path`, and the
self-reported diagnosis component and failure class. The episodic record has no token in
common with the generic prompt, receives no score, and is excluded before ranking. There
was therefore no score tie in this smoke. For an actual tie, the implementation orders
by higher score, then newer `created_at`, then lexicographically ascending record ID.
Both frozen records have the same creation timestamp, but that final tie behavior was
not exercised because only the procedural record qualified.

## Evidentiary Classification

### Directly observed

- The smoke completed all five planned runs, with 0/5 investigation completions and
  5/5 `tool_budget_exceeded` outcomes.
- Every run made 12 successful tool calls in two turns, retrieved the same procedural
  memory, added 420 estimated Brain-attributable input tokens, and wrote no memory.
- Every run recorded `accounting_complete=false` at tool-budget termination.
- Snapshot hashes were identical before and after every run; backend and Brain failure
  counts were zero.
- The tool sequences and overlap measurements above come directly from the five durable
  run records and the frozen canonical snapshot.

### Deterministic mechanism inference

- The procedural record is selected because the generic prompt shares exactly two
  retrieval tokens with it and none with the episodic record.
- The selected record plausibly functioned as a reusable diagnostic-path prior because
  its full payload was rendered into every run despite the prompt containing no
  incident-specific signal. This describes the deterministic exposure mechanism, not a
  causal behavioral effect.
- The recorded candidate tools, partial order, and redundant counts materially overlap
  the observed paths. The stored procedure does not encode the observed repeated
  `get_recent_errors` pattern or any budget-risk sequence.

### Hypothesis requiring controlled evaluation

- The retrieved procedural memory anchored the model on a repetitive path.
- The 420-token context increased repetition or prevented a timely Diagnosis.
- The same runs would have completed under a contemporaneous memoryless or
  length-matched placebo arm.
- Changing retrieval, procedure extraction, prompt wording, or budget-risk recording
  would improve outcomes.

None of those hypotheses is established by this smoke. Testing them requires a
pre-registered controlled comparison, not post-result tuning.

## Disposition

Recommend **Phase 7 Brain v1 complete with a documented negative behavioral calibration
and known retrieval limitation**. The phase delivered private typed memory,
provenance/lifecycle controls, deterministic bounded retrieval, frozen read-only replay,
auditability, and memory evaluation. The smoke successfully tested those mechanisms and
produced a negative behavioral result. Before the smoke, the committed protocol and
runbook already defined it as mechanism/security calibration rather than an accuracy or
causal-improvement gate; this disposition does not redefine success after observing the
result. The result does not reveal an unmet Brain v1 architectural requirement.

No further live Phase 7 call is authorized. The official 100-plus-run controlled study,
retrieval tuning, memory correction, and Phase 8 remain deferred pending explicit owner
authorization and a new pre-registered work item.

