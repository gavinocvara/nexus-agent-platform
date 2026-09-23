# NEXUS Agent Platform

NEXUS is a local-first engineering platform for auditable agent workflows across
incident response, software repair, QA, runtime control, and evaluated memory.

The governing specifications are:

- `NEXUS_PROJECT_INSTRUCTIONS.md`
- `NEXUS_MASTER_BUILD_PROMPT.md`
- `BRAIN.md`

This repository is currently in Phase 0: foundation. It intentionally contains
only the smallest package, tests, documentation, and CI needed to support the
next vertical slice.

## Local Setup

```powershell
py -m pip install -e ".[dev]"
py -m ruff check .
py -m mypy
py -m pytest
```

If `make` is available:

```powershell
make install-dev
make validate
```

## Current Direction

Phase 1 will begin the AegisOps lab incrementally with a small FastAPI service
surface, health checks, tests, and local runtime support.
