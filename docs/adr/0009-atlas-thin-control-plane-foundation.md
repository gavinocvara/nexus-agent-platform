# ADR 0009: Atlas Thin Control Plane Foundation

## Status

Accepted

## Context

Phase 8 needs only the typed control-plane substrate required by a future PatchForge
runtime. NEXUS does not yet need a network service, distributed scheduler, unrestricted
execution environment, credential broker, or multi-agent planner. The immediate risk is
not scale; it is losing determinism, authorization boundaries, idempotency, or audit
history around long-lived jobs.

Atlas must model this reviewed path without implementing either agent:

```text
Atlas
-> dispatch PatchForge
-> receive PatchResult
-> require SentinelQA review
-> human approval
-> completion
```

## Decision

Create a library-first `nexus.atlas` package with strict Pydantic contracts, a closed
state machine, an explicit policy registry, a transactional SQLite repository, and a
synchronous command service. Do not add a FastAPI process, background worker, model
call, tool runner, or deployment surface in Phase 8.

The durable `Job` envelope contains a UUID, assigned agent and reviewer identities,
typed task request, immutable source repository and commit SHA, bounded execution
budget, requested capabilities, lifecycle and approval states, structured result,
review, human decision, typed failure, execution lease, timestamps, and optimistic
revision.

The normal lifecycle is:

```text
created -> validated -> queued -> running -> awaiting_review
        -> approved -> completed
        -> rejected -> failed
```

Any nonterminal execution state may also enter `failed` explicitly. `running -> queued`
is the sole recovery edge and is legal only through the expired-lease recovery command.
The complete state-pair matrix is deterministic and exhaustively tested. Review
recording is an explicit audited same-state mutation, not a hidden transition.

## Permission Model

An `AgentPolicy` declares one identity, runtime kind/version, maximum budget, repository
prefixes, and a closed capability set. Phase 8 capabilities are only source read,
worktree write, bounded test execution, patch creation, review read, and review submit.
There is no shell, merge, deployment, production remediation, Kubernetes, memory, or
credential capability.

Validation requires the requested capability set and budget to be subsets of the
assigned policy and the source repository to match an allowlisted prefix. Starting and
submitting results require the assigned agent identity. Reviews require the assigned
reviewer and `review.submit`. Approval/rejection requires a human actor. Recovery and
completion require the Atlas system actor.

## Persistence And Idempotency

Use one local SQLite database at `.nexus/atlas/atlas.sqlite3` by default. SQLite is
adequate for the current single-node deterministic control-plane boundary and avoids
premature service infrastructure. A narrow `SQLiteAtlasStore` keeps migration to
PostgreSQL behind the repository boundary when concurrent workers justify it.

Every successful command atomically writes:

1. the complete canonical typed job snapshot;
2. one append-only typed audit event;
3. one command-idempotency record containing the request digest and exact response.

Command IDs are globally unique. Exact replay returns the original response even if the
job has since advanced. Reuse with different input fails. Every mutation also requires
the expected job revision; stale writers fail rather than overwrite newer state.
SQLite uses full synchronous durability, a write-ahead log, foreign keys, immediate
write transactions, unique job/event sequence constraints, and database triggers that
reject update or deletion of audit and command-idempotency records.

An active run carries a bounded lease derived from the job's maximum duration. A crash
leaves the complete running job and lease durable. After expiry, an explicit system
recovery command records the expired execution ID, appends a recovery event, and returns
the job to `queued`. No startup hook silently changes state.

## Audit Design

Every successful command emits one `AuditEvent` with a deterministic event UUID, job
sequence, resulting revision, typed actor, command ID, timestamp, explicit before/after
status, and a discriminated payload. Payloads separately model creation, transition,
result digest, review, approval, failure, and recovery data. Event sequence is always
`job_revision + 1`.

Audit events are append-only at both the Python interface and SQLite trigger layer.
Denied or malformed commands do not change durable job state or append a misleading
success event; callers receive typed policy, transition, idempotency, or concurrency
errors.

## PatchForge And SentinelQA Boundary

`AgentRuntimeDescriptor`, `JobDispatch`, and the async `AgentRuntime` protocol are the
only execution integration surface. `JobDispatch.from_job` accepts only a running job
with an active lease. `PatchResult` records base/proposed SHAs, a content-addressed patch
artifact, repository-relative changed files, and typed check results. Atlas stores the
result but does not create patches or run commands.

SentinelQA will later return the existing typed `ReviewResult`. Atlas permits human
approval only after a review exists and its verdict is `passed`; rejection remains an
explicit decision followed by an explicit failed closeout.

## Deferred Work

- PatchForge and SentinelQA runtime implementations
- worker scheduling, retries before lease expiry, cancellation transport, and queues
- FastAPI or remote control-plane APIs
- unrestricted shell, generic subprocess, credential, GitHub merge, and deployment tools
- PostgreSQL migration and multi-node claiming
- model routing, MCP, tracing export, dashboards, Kubernetes, Engram, and shared memory
- AegisOps remediation and Phase 9 work

## Consequences

- PatchForge can be added by implementing one typed runtime protocol rather than
  changing job storage or approval semantics.
- All lifecycle changes are deterministic, attributable, replay-safe, and recoverable
  after process failure.
- SQLite deliberately limits Atlas v1 to a local single-node control plane; that is an
  accepted scope constraint, not an implicit distributed-execution claim.
- Phase 5 investigator and Phase 7 Brain behavior remain isolated and hash-pinned.
