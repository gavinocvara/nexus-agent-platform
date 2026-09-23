# ADR 0001: Phase 0 Repository Foundation

## Status

Accepted

## Context

NEXUS needs a professional foundation before service, agent, memory, and runtime
architecture are introduced. The governing specifications require typed Python,
quality tooling, CI, documentation, and a local-first workflow.

## Decision

Start with a minimal Python 3.12 package in `src/nexus`, configured with pytest,
Ruff, and mypy. Add only baseline documentation, state tracking, safe environment
defaults, and CI validation.

Do not create the full target monorepo tree until concrete vertical slices need
those directories.

## Consequences

- The repository has immediate validation gates.
- Phase 1 can add AegisOps code without reorganizing the project.
- Empty architectural folders are avoided until they represent real code or docs.
