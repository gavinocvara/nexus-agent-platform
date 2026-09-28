"""Runtime boundary that future agents such as PatchForge can implement."""

from typing import Protocol, runtime_checkable
from uuid import UUID

from nexus.atlas.models import (
    Capability,
    ExecutionBudget,
    Identifier,
    Job,
    JobStatus,
    SourceRevision,
    StrictModel,
    StructuredResult,
    TaskRequest,
)


class AgentRuntimeDescriptor(StrictModel):
    agent_id: Identifier
    runtime_kind: Identifier
    runtime_version: str
    capabilities: list[Capability]


class JobDispatch(StrictModel):
    job_id: UUID
    execution_id: UUID
    request: TaskRequest
    source: SourceRevision
    budget: ExecutionBudget
    capabilities: list[Capability]

    @classmethod
    def from_job(cls, job: Job) -> "JobDispatch":
        if job.status is not JobStatus.RUNNING or job.active_execution is None:
            raise ValueError("Only a running leased job can be dispatched")
        return cls(
            job_id=job.job_id,
            execution_id=job.active_execution.execution_id,
            request=job.request,
            source=job.source,
            budget=job.budget,
            capabilities=job.capabilities,
        )


@runtime_checkable
class AgentRuntime(Protocol):
    """Execution adapter only; Atlas does not provide tools or scheduling in Phase 8."""

    @property
    def descriptor(self) -> AgentRuntimeDescriptor: ...

    async def execute(self, dispatch: JobDispatch) -> StructuredResult: ...
