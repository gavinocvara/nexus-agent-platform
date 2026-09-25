# Brain v1 Experimental Protocol

The machine-readable protocol in `brain-v1-protocol.json` is hashed into every new
Phase 7 benchmark identity. A configuration or protocol change creates a new identity
and must start a new session.

## Calibration

The single authorized `learn` investigation completed in session
`c6832bae-feb7-4216-933e-139c734c2173`. Its initial retrieval was empty, it wrote one
episodic and one procedural memory, and its post-run logical snapshot hash was
`9e7d36376c6b0c8c77d5a3f94cafcea22cb2a53a313986a2a3fe6aed4f0fd3b6`.
The diagnosis identified the component but reported the wrong failure class at 0.97
confidence. That claim remains `self_reported/unverified`; it is not corrected, promoted,
or removed based on evaluator knowledge.

Snapshot export and one five-scenario `frozen_eval` smoke remain pending. They validate
mechanics, storage isolation, budgets, failure handling, and auditability. They are not
an official evaluation and cannot support a claim that Brain improves diagnosis.

The incident prompt is identical for all five scenarios. Pre-run Brain retrieval is
therefore primarily a test of reusable procedural context, similar to a learned prompt
prefix, rather than incident-specific episodic recall. A length-matched placebo is
required before attributing any later difference to learned content.

Lexical ties tend toward recency under the deterministic rank key, and the generic prompt
may not match incident-specific episodic terms at all. Self-reported diagnoses can still
anchor later runs despite their unverified label. Future fold learning order must
therefore use the fixed pre-registered seed; the retrieval policy must not be tuned from
the targeted calibration result.

## Deferred Official Study

The pre-registered study uses five leave-one-scenario-out folds and three interleaved
arms: contemporaneous memoryless, held-out frozen Brain, and length-matched placebo.
The evaluator owns a separate contamination ledger and must reject any fold whose
snapshot contains a record mapped to its held-out scenario. The proposed scale is about
35 evaluation runs per arm plus learning runs. It is not authorized yet.

The five-scenario catalog can support only a narrow transfer claim. The latency and
unavailability pairs test transfer across services. `orders_database_unavailable` has
no same-class training peer and remains a required negative-control-like fold.

The historical 5/15 exact baseline has a wide uncertainty interval. Exact accuracy is
not sufficient: confident-wrong behavior, evidence validity, safety, invalid output,
tool-budget failures, genuine abstention, latency, and tokens are mandatory guards.
