"""Operator-owned PatchForge repository and sandbox policy contracts."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, HttpUrl, StringConstraints, field_validator, model_validator

from nexus.atlas.models import Identifier, Sha256, StrictModel
from nexus.patchforge.models import RepositoryPath, RunBudgets, ToolName


class CommandPurpose(StrEnum):
    REPRODUCTION = "reproduction"
    TARGETED_TESTS = "targeted_tests"
    FULL_TEST_SUITE = "full_test_suite"
    FORMATTER = "formatter"
    LINTER = "linter"
    TYPECHECK = "typecheck"


CommandArgument = Annotated[str, StringConstraints(min_length=1, max_length=500)]
ImageDigest = Annotated[
    str,
    StringConstraints(
        pattern=r"^(?:[^\s@]+@)?sha256:[a-f0-9]{64}$",
        max_length=500,
    ),
]


class SandboxPolicy(StrictModel):
    image: ImageDigest
    network_disabled: Literal[True] = True
    secrets_allowed: Literal[False] = False
    run_as_user: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*:[1-9][0-9]*$")]
    read_only_root: Literal[True] = True
    git_directory_mounted: Literal[False] = False
    cpu_limit_millis: int = Field(ge=100, le=64_000)
    memory_limit_mb: int = Field(ge=64, le=131_072)
    pids_limit: int = Field(ge=16, le=4096)
    default_timeout_seconds: int = Field(ge=1, le=86_400)
    max_output_bytes: int = Field(ge=1, le=100_000_000)


class SandboxCommand(StrictModel):
    executable: Identifier
    arguments: list[CommandArgument] = Field(default_factory=list, max_length=100)
    working_directory: Literal["."] | RepositoryPath = "."
    timeout_seconds: int = Field(ge=1, le=86_400)
    max_output_bytes: int = Field(ge=1, le=100_000_000)


class RepositoryProfile(StrictModel):
    schema_version: Literal[1] = 1
    profile_id: Identifier
    profile_version: int = Field(ge=1)
    owned_by: Literal["operator"] = "operator"
    repository_url: HttpUrl
    sandbox: SandboxPolicy
    commands: dict[CommandPurpose, SandboxCommand] = Field(min_length=1, max_length=50)
    protected_paths: list[RepositoryPath] = Field(default_factory=list, max_length=200)
    test_path_prefixes: list[RepositoryPath] = Field(default_factory=list, max_length=50)

    @field_validator("repository_url")
    @classmethod
    def reject_url_suffixes(cls, value: HttpUrl) -> HttpUrl:
        if any(
            item is not None
            for item in (value.username, value.password, value.query, value.fragment)
        ):
            raise ValueError("Repository profile URL cannot contain credentials or suffixes")
        return value

    @model_validator(mode="after")
    def validate_paths(self) -> "RepositoryProfile":
        if len(set(self.protected_paths)) != len(self.protected_paths):
            raise ValueError("Protected paths must be unique")
        if len(set(self.test_path_prefixes)) != len(self.test_path_prefixes):
            raise ValueError("Test path prefixes must be unique")
        for command in self.commands.values():
            if command.timeout_seconds > self.sandbox.default_timeout_seconds:
                raise ValueError("Repository command timeout exceeds sandbox policy")
            if command.max_output_bytes > self.sandbox.max_output_bytes:
                raise ValueError("Repository command output exceeds sandbox policy")
        return self


class PatchForgePolicy(StrictModel):
    schema_version: Literal[1] = 1
    agent_id: Literal["patchforge.engineer"] = "patchforge.engineer"
    repository_profile_id: Identifier
    repository_profile_sha256: Sha256
    allowed_tools: list[ToolName] = Field(min_length=1, max_length=50)
    budgets: RunBudgets
    parallel_tool_calls: Literal[False] = False
    memory_enabled: Literal[False] = False
    max_changed_files: int = Field(ge=1, le=1000)
    max_diff_bytes: int = Field(ge=1, le=100_000_000)
    allow_test_file_changes: bool
    require_reproduction_when_practical: Literal[True] = True

    @field_validator("allowed_tools")
    @classmethod
    def canonicalize_tools(cls, values: list[ToolName]) -> list[ToolName]:
        if len(set(values)) != len(values):
            raise ValueError("Allowed PatchForge tools must be unique")
        return sorted(values, key=lambda value: value.value)
