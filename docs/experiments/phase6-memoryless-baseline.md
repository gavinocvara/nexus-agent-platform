# Phase 6 Memoryless Baseline Record

This document records the accepted Phase 6 evidence before Phase 7 Brain work is
committed. The local baseline lock and session artifacts remain immutable and ignored
by Git.

## Completed Live Sequence

- Targeted replay: `021d6835-f3d0-4214-85c2-e02c0217a29d` (1 run)
- Five-scenario smoke: `362ccb39-aa2a-4a80-9136-4b1ecb91ab1c` (5 runs)
- Repeated baseline: `757df553-a0d0-4d11-845a-b972b456ee83` (15 runs)
- Baseline name: `aegisops-memoryless-v1`
- Source Git SHA: `52bd5f47dfbf5591e5263911b1c64dab66158d90`
- Accepted at: `2026-09-24T19:46:21.561393Z`

## Immutable Evidence

- Locked summary SHA-256:
  `2078803c4e20852c435b10d0ab93206dd748d9b78cd81dc7e1a01c0cfde71301`
- Historical lock-file SHA-256:
  `b6a46c51bf235f4bbe4465ee060f5d92cf0b0571a3835ce0c67ff062c0209d74`
- The schema-3 lock contains a host-specific absolute Windows path and binds only
  `summary.json`. It is preserved as historical evidence and will not be rewritten.

## Frozen Behavior Identity

- Model: `gpt-5.6-sol`
- NEXUS version: `0.7.4`
- Agents SDK: `0.22.3`
- Investigator instruction SHA-256:
  `d858a63116e8579a4e19a19f01cea5441aa840213380455449528ca385dad456`
- Tool-registry SHA-256:
  `b75d71fff306ab51f445eba5d9a438dc394d02c810ec7fceb4110975a15cbfbf`
- Diagnosis-schema SHA-256:
  `aea17a6803fe3c49f1d1e3268b5f58072e05b5edd5dce9fb72514b96a5b720ab`
- Scenario-catalog SHA-256:
  `6b47e28ee3d5c13619903d9885212022c49940224e333188d177a40e46214e2c`
- Evaluator: `aegisops-evaluator-v1`
- Limits: 12 diagnostic calls, 10 turns, 120-second agent loop

## Aggregate Result

- Completion: `66.6667%` (10/15)
- Component accuracy: `46.6667%`
- Failure-class accuracy: `33.3333%`
- Exact diagnosis accuracy: `33.3333%` (5/15)
- Abstention: `53.3333%`; this historical metric includes five tool-budget failures
  as well as genuine abstentions and must not be interpreted alone.
- Tool-budget failure: `33.3333%`
- Invalid output, backend failure, timeout, unsupported claim, unsafe attempt: `0%`
- Valid evidence-reference rate: `100%`
- Average/median diagnostic calls: `11.2` / `12`
- Total/average tokens: `130842` / `8722.8`

Phase 7 was authorized only after this baseline was accepted. A contemporaneous
Phase 7 memoryless control is required before making any Brain improvement claim.
