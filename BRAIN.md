# BRAIN.md — NEXUS Agent Memory Architecture

## Purpose

NEXUS agents should become more useful over time by retaining evaluated experience. This document defines that capability.

The word **brain** is an engineering metaphor for persistent, structured memory and adaptation. It does not imply consciousness or independent personhood.

The key design rule is:

> **Private brains, verified exchange.**

Each agent owns a private memory namespace. Agents do not casually read or rewrite one another’s memories. Useful knowledge crosses agent boundaries only through an auditable promotion and retrieval layer.

## Why Not One Giant Shared Brain?

A single mutable shared memory store is easy to build but creates serious problems:

- incorrect observations spread everywhere;
- agents overwrite one another’s assumptions;
- private role-specific experience leaks across boundaries;
- stale information becomes difficult to identify;
- contradictory memories become ambiguous;
- provenance disappears;
- an attacker only needs to poison one shared channel;
- evaluating which memory improved or harmed behavior becomes difficult.

NEXUS instead uses isolated memories plus a controlled shared knowledge ledger.

## Memory Model

Every agent receives a stable `agent_id` and private `brain_namespace`.

Example:

```text
aegisops.incident_commander
aegisops.metrics_analyst
patchforge.coder
patchforge.reviewer
sentinelqa.browser
sentinelqa.failure_analyst
```

Each brain has six conceptual layers.

### 1. Working Memory

Current-run information.

Examples:
- current alert;
- active hypothesis;
- tool observations;
- current code diff;
- browser state.

Properties:
- short TTL;
- small size;
- discarded or summarized after the run;
- not automatically treated as truth.

### 2. Episodic Memory

What the agent experienced.

Example:

```text
During incident INC-0042:
- Orders latency exceeded 2 s.
- PostgreSQL was healthy.
- Redis health probe failed.
- Redis restart restored normal latency.
```

Properties:
- timestamped;
- linked to traces and artifacts;
- immutable event history whenever practical.

### 3. Semantic Memory

Validated facts and relationships.

Example:

```text
Orders API uses Redis for cart/session caching.
```

This is stronger than an episode because it represents a durable fact, not merely an observation.

### 4. Procedural Memory

Reusable strategies.

Example:

```text
When Orders latency rises:
1. verify gateway latency;
2. inspect Orders latency;
3. check Redis connectivity;
4. inspect DB latency;
5. compare dependency traces.
```

Procedures are versioned and evaluated.

### 5. Reflective Memory

Lessons extracted after a task.

Example:

```text
The investigator wasted three tool calls querying CPU before checking the dependency graph. In similar incidents, dependency health should be checked earlier.
```

Reflection is a candidate lesson, not automatic truth.

### 6. Identity Memory

Stable role configuration.

Contains:
- mission;
- boundaries;
- approved tools;
- default reasoning strategy;
- escalation rules;
- evaluation goals.

Identity memory cannot grant itself new privileges.

## Canonical Memory Record

Design toward a typed schema similar to:

```python
class MemoryRecord:
    id: UUID
    agent_id: str
    namespace: str
    memory_type: MemoryType
    content: str
    created_at: datetime
    observed_at: datetime | None
    valid_from: datetime | None
    valid_to: datetime | None
    confidence: float | None
    source_refs: list[str]
    trace_id: str | None
    tags: list[str]
    supersedes: list[UUID]
    sensitivity: Sensitivity
    verification_status: VerificationStatus
    retention_policy: str
    version: int
```

Keep original evidence outside the summary when appropriate and reference it by ID.

## Memory Write Pipeline

Raw model output should not directly become long-term memory.

Use:

```text
run/event
→ memory candidate
→ schema validation
→ provenance attachment
→ classification
→ deduplication
→ contradiction check
→ confidence assignment
→ private persistence
→ later consolidation
```

## Consolidation

A scheduled or event-triggered consolidator can turn many episodes into fewer durable lessons.

Example:

```text
Episode 1: Redis timeout during INC-12
Episode 2: Redis timeout during INC-19
Episode 3: Redis timeout during INC-31

            ↓

Semantic:
Orders depends on Redis.

Procedural:
Check Redis health early when Orders latency increases and DB latency is normal.
```

Never delete the source episodes solely because a summary exists unless retention policy explicitly permits it.

## Knowledge Exchange

The shared exchange is not another conversational brain.

It is a curated ledger of validated engineering knowledge.

Suggested categories:
- `fact`
- `decision`
- `runbook`
- `incident_lesson`
- `benchmark_result`
- `artifact`
- `dependency`
- `procedure`
- `known_failure_mode`

A private memory can be promoted when it has:
- adequate evidence;
- clear provenance;
- appropriate scope;
- validation;
- no unresolved contradiction;
- acceptable sensitivity.

## Promotion Flow

```text
private memory
→ promotion candidate
→ evidence check
→ evaluator/verifier
→ optional human review
→ shared ledger
```

The shared record retains:
- source agent;
- source memory;
- evidence;
- time;
- version;
- status.

## Retrieval Flow

An agent asks Engram for knowledge using its Atlas identity.

Engram applies:
1. access policy;
2. current-task relevance;
3. temporal filtering;
4. keyword/vector/graph retrieval as available;
5. provenance requirements;
6. confidence/staleness checks.

The model receives relevant evidence, not an uncontrolled dump of every memory.

## Contradiction Handling

Never simply choose the newest memory and delete the old one.

Represent:

```text
fact A
valid_from = 2026-01-01
valid_to   = 2026-08-14

fact B
valid_from = 2026-08-14
valid_to   = null
supersedes = fact A
```

For genuine unresolved contradictions:

```text
status = disputed
```

and preserve both evidence chains until resolved.

## Forgetting Is a Feature

A useful brain must forget or compress low-value information.

Examples:
- working context expires quickly;
- duplicate observations are merged;
- temporary task details receive TTLs;
- stale facts are flagged;
- low-value reflections can be archived;
- sensitive information follows stricter retention.

Do not optimize for maximum memory volume.

Optimize for useful retrieval.

## Memory Security

Memory is another attack surface.

Protect against:
- prompt injection stored as memory;
- untrusted repository text becoming instructions;
- malicious webpages becoming procedures;
- credentials accidentally stored in summaries;
- cross-agent namespace leakage;
- unauthorized shared-memory promotion.

Store the distinction between:
- data;
- observation;
- instruction;
- policy.

Retrieved text from external systems is **data**, not authority.

## Learning Loop

NEXUS should improve through an evidence-driven loop:

```text
execute
→ observe
→ score
→ reflect
→ propose lesson
→ store candidate
→ benchmark
→ adopt if better
```

Examples of learnable changes:
- better tool ordering;
- improved retrieval filters;
- improved runbooks;
- better test generation;
- better diagnostic heuristics;
- better escalation behavior.

## Self-Modification Boundary

Agents do not directly rewrite their own source code or governing policies as an accepted change.

They may create a proposed patch.

PatchForge can test it.

Evaluators compare it against the baseline.

A reviewer or configured approval policy accepts or rejects it.

Every adopted self-improvement remains:
- versioned;
- testable;
- reversible;
- attributable.

## Brain Evaluation

Create memory-specific benchmarks.

Measure:
- retrieval precision;
- retrieval recall where measurable;
- factual accuracy;
- temporal accuracy;
- provenance correctness;
- contradiction handling;
- stale-memory rate;
- harmful-memory rate;
- latency;
- token overhead;
- improvement versus no-memory baseline.

For procedural memory, compare task success before and after a proposed lesson.

## Storage Evolution

### Brain v1
Use PostgreSQL tables with strong schemas.

Add pgvector only when semantic retrieval is useful.

### Brain v2
Add hybrid retrieval:
- structured filters;
- lexical search;
- vector similarity.

### Brain v3
Add explicit relationships and temporal graph capabilities when real queries justify them.

Do not begin with a complex graph database merely because the word “memory” sounds like a graph problem.

## Suggested Repository Shape

```text
brain/
├── core/
│   ├── manager.py
│   ├── retrieval.py
│   ├── writer.py
│   └── lifecycle.py
│
├── schemas/
│   ├── memory.py
│   ├── provenance.py
│   └── knowledge.py
│
├── agents/
│   └── <agent_id>/
│       └── identity.yaml
│
├── exchange/
│   ├── ledger.py
│   ├── promotion.py
│   ├── subscriptions.py
│   └── verifier.py
│
├── consolidation/
│   ├── consolidator.py
│   └── reflection.py
│
├── policies/
│   ├── access.py
│   ├── retention.py
│   └── sensitivity.py
│
├── evaluations/
│   ├── retrieval/
│   ├── temporal/
│   └── procedural/
│
└── migrations/
```

## Example: Cross-Agent Learning

AegisOps investigates an outage.

Its private episodic memory contains the entire investigation.

After verification, Engram promotes:

```text
Known failure mode:
Orders API may exhibit high latency when Redis connection pool is exhausted.

Evidence:
INC-0042
trace abc123
metrics snapshot m-77

Approved diagnostic step:
Check Redis pool saturation when Orders latency rises while PostgreSQL latency remains normal.
```

Later:

- PatchForge can retrieve the failure mode while working on a related issue.
- SentinelQA can retrieve the scenario to generate a regression test.
- AegisOps can recognize the pattern faster next time.

They share **validated engineering knowledge**, not raw thoughts.

## North Star

The goal is not an agent that merely remembers more.

The goal is a system that can demonstrate:

> “After observing, validating, and retaining previous engineering experience, this agent performs measurably better on future tasks while remaining auditable, permissioned, and reversible.”

That is the standard for NEXUS memory.
