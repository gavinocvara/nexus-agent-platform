"""PatchForge software-engineering agent contracts."""

from nexus.patchforge.gateway import GatewayResult, ToolGateway
from nexus.patchforge.models import EngineeringTask, PatchResult, RunIdentity
from nexus.patchforge.runtime import PatchForgeLifecycle, RuntimeSnapshot
from nexus.patchforge.sandbox import DockerSandbox, FakeSandbox, SandboxExecutor
from nexus.patchforge.workspace import WorkspaceManager

__all__ = [
    "DockerSandbox",
    "EngineeringTask",
    "FakeSandbox",
    "GatewayResult",
    "PatchResult",
    "PatchForgeLifecycle",
    "RunIdentity",
    "RuntimeSnapshot",
    "SandboxExecutor",
    "ToolGateway",
    "WorkspaceManager",
]
