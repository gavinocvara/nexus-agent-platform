"""Capability, budget, repository, and actor policy for Atlas."""

from collections.abc import Iterable
from urllib.parse import urlsplit

from nexus.atlas.models import (
    ActorIdentity,
    ActorType,
    AgentPolicy,
    Capability,
    ExecutionBudget,
    Job,
    JobSpec,
)


class AtlasPolicyError(PermissionError):
    """Raised when a requested operation is outside explicit Atlas policy."""


class AgentRegistry:
    """Small explicit registry of policies; runtime implementations remain external."""

    def __init__(self, policies: Iterable[AgentPolicy] = ()) -> None:
        self._policies: dict[str, AgentPolicy] = {}
        for policy in policies:
            if policy.agent_id in self._policies:
                raise ValueError(f"Duplicate Atlas agent policy: {policy.agent_id}")
            self._policies[policy.agent_id] = policy

    def get(self, agent_id: str) -> AgentPolicy:
        try:
            return self._policies[agent_id]
        except KeyError as exc:
            raise AtlasPolicyError(f"Atlas agent is not registered: {agent_id}") from exc

    def validate_job(self, spec: JobSpec) -> None:
        agent = self.get(spec.agent_id)
        reviewer = self.get(spec.reviewer_agent_id)
        missing = set(spec.capabilities) - set(agent.capabilities)
        if missing:
            values = ", ".join(sorted(item.value for item in missing))
            raise AtlasPolicyError(f"Agent lacks requested capabilities: {values}")
        _require_budget_within(spec.budget, agent.max_budget)
        repository = str(spec.source.repository_url)
        if not any(
            _repository_within(repository, str(prefix)) for prefix in agent.repository_prefixes
        ):
            raise AtlasPolicyError("Source repository is outside the agent policy")
        if Capability.REVIEW_SUBMIT not in reviewer.capabilities:
            raise AtlasPolicyError("Reviewer agent lacks review.submit capability")

    def require_assigned_agent(self, job: Job, actor: ActorIdentity) -> None:
        if actor.actor_type is not ActorType.AGENT or actor.actor_id != job.agent_id:
            raise AtlasPolicyError("Operation requires the assigned agent identity")
        self.get(actor.actor_id)

    def require_reviewer(self, job: Job, actor: ActorIdentity) -> None:
        if actor.actor_type is not ActorType.AGENT or actor.actor_id != job.reviewer_agent_id:
            raise AtlasPolicyError("Operation requires the assigned reviewer identity")
        policy = self.get(actor.actor_id)
        if Capability.REVIEW_SUBMIT not in policy.capabilities:
            raise AtlasPolicyError("Reviewer agent lacks review.submit capability")

    @staticmethod
    def require_human(actor: ActorIdentity) -> None:
        if actor.actor_type is not ActorType.HUMAN:
            raise AtlasPolicyError("Operation requires a human actor")

    @staticmethod
    def require_operator(actor: ActorIdentity) -> None:
        if actor.actor_type not in {ActorType.SYSTEM, ActorType.HUMAN}:
            raise AtlasPolicyError("Operation requires a system or human operator")

    @staticmethod
    def require_system(actor: ActorIdentity) -> None:
        if actor.actor_type is not ActorType.SYSTEM:
            raise AtlasPolicyError("Operation requires the Atlas system actor")


def _require_budget_within(requested: ExecutionBudget, maximum: ExecutionBudget) -> None:
    for field in ExecutionBudget.model_fields:
        if getattr(requested, field) > getattr(maximum, field):
            raise AtlasPolicyError(f"Requested budget exceeds policy field: {field}")


def _repository_within(repository: str, prefix: str) -> bool:
    candidate = urlsplit(repository)
    allowed = urlsplit(prefix)
    if candidate.scheme != allowed.scheme or candidate.netloc != allowed.netloc:
        return False
    candidate_path = candidate.path.rstrip("/")
    allowed_path = allowed.path.rstrip("/")
    return candidate_path == allowed_path or candidate_path.startswith(f"{allowed_path}/")
