# Repository Hygiene Audit (2026-09)

Audit of `main` at `c28fe5c` (NEXUS 0.9.0), done on branch `maintenance/repo-hygiene`.
This file records the decisions that are not visible from the diff itself. Those are the
items deliberately left unchanged, and the remaining debt that belongs to active or
future work.

## Kept Deliberately

- ADRs 0001-0009 are unchanged. They are historical decision records. Forward-looking
  sentences in them, for example "Brain v1 remains blocked on a genuine accepted
  baseline" in ADR 0007, describe the state when each ADR was accepted. ADR 0010 records
  the execution-order change instead of editing older ADRs.
- `docs/experiments/*` keeps its recorded observations, hashes, and dispositions. The
  only change is a clearly labeled addendum appended to the Brain v1 calibration report.
  `brain-v1-protocol.json` is byte-identical (SHA-256 `c6c3cff8...68ed`).
- Benchmark fixtures that carry old versions (`nexus_version` 0.7.0/0.7.3/0.7.4, the
  synthetic `C:\old-host` lock path) are intentional compatibility and legacy-parsing
  test data.
- `LegacyLockedBaselineManifest` and analysis-version-1 handling are required to parse
  the immutable Phase 6 lock and summary.
- `AegisOpsEvaluationHarness` (the Phase 5 multi-scenario harness) has no CLI entry
  point. It is still exported, tested, and guarded against writable Brain mode, so it
  stays as the documented Phase 5 path.
- Frozen AegisOps modules keep two unused names, `INSTRUCTION_VERSION` and the
  `ToolInvoker` alias. Removing them would not change any behavior hash, but editing
  frozen-behavior modules for trivial dead names adds review risk without value.
- SDK hook and output-schema methods that static analysis reports as unused
  (`on_llm_end`, `is_strict_json_schema`, and similar) are called by the Agents SDK.
- CI's focused test steps re-run subsets of the full suite. They add no coverage but
  were left alone to avoid conflicting with PatchForge CI changes.

## Remaining Debt

1. `nexus.atlas.config.AtlasSettings` is defined but not imported anywhere. The
   documented variable `NEXUS_ATLAS_DATABASE_PATH` therefore has no effect yet. Wire it
   when the first Atlas runtime entry point exists, or remove it. This is the Atlas/
   PatchForge surface, so it is left to that work.
2. `analytics._rate` returns `0.0` for a zero denominator. As a result,
   `valid_evidence_reference_rate` reads `0.0` rather than unavailable when no evidence
   references exist, which misstates a comparison against the baseline's `1.0`.
3. `brain_attributable_input_tokens` counts rendered memory once per run, but the
   context is resent on every model request.
4. The rejected over-budget call requests are not persisted. That hides the second-turn
   demand behind a budget failure.
5. The investigator is not told its 12-call budget, and SDK parallel tool calls are on.
   This is frozen Phase 5 behavior and must not change for the AegisOps baseline. New
   agents (PatchForge) should expose budgets and restrict parallel calls from the start.
6. `docs/runbooks/aegisops-brain-v1.md` mixes operating steps with the historical
   record of the single authorized calibration. Split them if Brain runs are ever
   authorized again.
7. No automated Markdown link check exists in CI. This audit used a one-off local
   script over relative links and backticked repository paths.
