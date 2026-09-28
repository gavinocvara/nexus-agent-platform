"""Operator-owned PatchForge repository and sandbox policy tests."""

import pytest
from pydantic import ValidationError

from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.models import PhaseBudget, RunBudgets, ToolName
from nexus.patchforge.policy import (
    CommandPurpose,
    PatchForgePolicy,
    RepositoryProfile,
    SandboxCommand,
    SandboxPolicy,
)


def _phase_budget(*, calls: int = 2) -> PhaseBudget:
    return PhaseBudget(max_tool_calls=calls, max_duration_seconds=30, max_output_bytes=10_000)


def _budgets() -> RunBudgets:
    return RunBudgets(
        provisioning=_phase_budget(),
        recon=_phase_budget(),
        hypothesis=_phase_budget(),
        reproduce=_phase_budget(),
        implement=_phase_budget(),
        targeted_validate=_phase_budget(),
        full_validate=_phase_budget(),
        self_review=_phase_budget(),
        finalization_reserve=_phase_budget(calls=1),
        cleanup=_phase_budget(),
        max_implementation_loops=2,
        max_total_tool_calls=25,
        max_total_duration_seconds=400,
    )


def _sandbox() -> SandboxPolicy:
    return SandboxPolicy(
        image=f"python@sha256:{'a' * 64}",
        run_as_user="10001:10001",
        cpu_limit_millis=1000,
        memory_limit_mb=512,
        pids_limit=128,
        default_timeout_seconds=60,
        max_output_bytes=100_000,
    )


def _profile() -> RepositoryProfile:
    return RepositoryProfile(
        profile_id="nexus.python",
        profile_version=1,
        repository_url="https://github.com/gavinocvara/nexus-agent-platform",
        sandbox=_sandbox(),
        commands={
            CommandPurpose.TARGETED_TESTS: SandboxCommand(
                executable="python",
                arguments=["-m", "pytest", "{targets}"],
                timeout_seconds=120,
                max_output_bytes=100_000,
            ),
            CommandPurpose.FULL_TEST_SUITE: SandboxCommand(
                executable="python",
                arguments=["-m", "pytest"],
                timeout_seconds=600,
                max_output_bytes=1_000_000,
            ),
        },
        protected_paths=["NEXUS_PROJECT_INSTRUCTIONS.md", "BRAIN.md"],
        test_path_prefixes=["tests"],
    )


def test_repository_profile_is_operator_owned_and_canonically_hashed() -> None:
    profile = _profile()
    assert profile.owned_by == "operator"
    assert len(canonical_sha256(profile)) == 64
    assert profile.sandbox.network_disabled is True
    assert profile.sandbox.secrets_allowed is False
    assert profile.sandbox.git_directory_mounted is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("network_disabled", False),
        ("secrets_allowed", True),
        ("git_directory_mounted", True),
        ("read_only_root", False),
        ("run_as_user", "0:0"),
    ],
)
def test_sandbox_policy_cannot_relax_v1_isolation(field: str, value: object) -> None:
    payload = _sandbox().model_dump(mode="python")
    payload[field] = value
    with pytest.raises(ValidationError):
        SandboxPolicy.model_validate(payload)


@pytest.mark.parametrize(
    "repository_url",
    [
        "https://user:password@github.com/gavinocvara/nexus-agent-platform",
        "https://github.com/gavinocvara/nexus-agent-platform?token=secret",
        "https://github.com/gavinocvara/nexus-agent-platform#fragment",
    ],
)
def test_repository_profile_rejects_credentials_and_suffixes(repository_url: str) -> None:
    payload = _profile().model_dump(mode="python")
    payload["repository_url"] = repository_url
    with pytest.raises(ValidationError, match="cannot contain"):
        RepositoryProfile.model_validate(payload)


def test_patchforge_policy_disables_parallel_calls_and_memory() -> None:
    profile = _profile()
    policy = PatchForgePolicy(
        repository_profile_id=profile.profile_id,
        repository_profile_sha256=canonical_sha256(profile),
        allowed_tools=[ToolName.RUN_TEST_SUITE, ToolName.LIST_TREE],
        budgets=_budgets(),
        max_changed_files=50,
        max_diff_bytes=1_000_000,
        allow_test_file_changes=True,
    )
    assert policy.allowed_tools == [ToolName.LIST_TREE, ToolName.RUN_TEST_SUITE]
    assert policy.parallel_tool_calls is False
    assert policy.memory_enabled is False

    payload = policy.model_dump(mode="python")
    payload["parallel_tool_calls"] = True
    with pytest.raises(ValidationError):
        PatchForgePolicy.model_validate(payload)


def test_profile_commands_are_argument_vectors_not_shell_strings() -> None:
    command = _profile().commands[CommandPurpose.FULL_TEST_SUITE]
    assert command.executable == "python"
    assert command.arguments == ["-m", "pytest"]
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SandboxCommand.model_validate(
            {
                "executable": "python",
                "arguments": ["-m", "pytest"],
                "timeout_seconds": 60,
                "max_output_bytes": 1000,
                "shell": True,
            }
        )
