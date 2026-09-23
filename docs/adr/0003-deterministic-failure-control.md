# ADR 0003: Deterministic Failure Control and Ground-Truth Isolation

## Status

Accepted

## Context

Future AegisOps investigators need repeatable incidents with known answers. The
evaluator must know which fault was injected, while an investigator must see only
ordinary operational symptoms. Random chaos and destructive infrastructure faults
would make this early benchmark harder to reproduce and recover.

## Decision

Use two deliberately separate layers.

Service processes contain only a small operational fault registry and an ephemeral,
lock-protected controller. A process can hold one active fault. Effects are limited
to unavailable responses, a fixed bounded delay, or simulated dependency failure.
State disappears on restart and reset is idempotent. No fault mutates PostgreSQL,
the filesystem, schema, or credentials.

Evaluator ground truth lives in versioned JSON files under `lab/scenarios/v1` and
must validate against Pydantic models. It contains scenario identity, root cause,
expected symptoms, unaffected components, activation routing, and recovery. This
data is loaded by the evaluator runner, not by service request handling.

Users and Orders mount `__lab` control routes only when
`NEXUS_LAB_FAILURES_ENABLED=true`. The default is false. Gateway never proxies
these routes. Compose enables them because it is explicitly the incident lab.

Every runner execution follows:

```text
defined -> baseline verified -> active -> symptoms verified
        -> reset -> recovery verified
```

Reset runs even when symptom verification fails.

## Consequences

- Incidents are repeatable, bounded, non-destructive, and fast enough for CI.
- Ordinary errors and health responses contain symptoms but no ground-truth labels.
- Evaluator data can later be compared with an agent diagnosis without changing
  scenario storage.
- The lab control plane is privileged test infrastructure, not a production API.
- Real process, network, and Kubernetes faults remain deferred to later phases.
