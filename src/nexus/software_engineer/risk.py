"""Formal change-risk classification for the resident Software Engineer.

Risk is the highest of three independent readings, raised one level when anything is
uncertain: the change category's intrinsic floor, the most sensitive path touched, and the
size of the change. Touching a *governing* path (the engineer's own policy, autonomy,
budgets, approval rules, security boundaries, CI, frozen evidence, or infrastructure) is
always high risk, whatever the category claims.
"""

from __future__ import annotations

from collections.abc import Sequence

from nexus.patchforge.gateway import is_sensitive_path, path_matches
from nexus.software_engineer.models import (
    ChangeCategory,
    CycleBudget,
    RiskAssessment,
    RiskLevel,
    category_risk,
    highest,
)

# Paths whose change alters what the engineer, PatchForge, SentinelQA, or Atlas may do.
# Changing them is never autonomous. Keep this list in the ADR when it changes.
GOVERNING_PATH_PREFIXES: tuple[str, ...] = (
    ".github",
    "AGENTS.md",
    "CLAUDE.md",
    "Dockerfile",
    "alembic.ini",
    "compose.yaml",
    "docs/experiments",
    "infra",
    "migrations",
    "src/nexus/atlas/policy.py",
    "src/nexus/atlas/service.py",
    "src/nexus/brain",
    "src/nexus/patchforge/attestor.py",
    "src/nexus/patchforge/gateway.py",
    "src/nexus/patchforge/policy.py",
    "src/nexus/patchforge/sandbox.py",
    "src/nexus/patchforge/workspace.py",
    "src/nexus/sentinelqa",
    "src/nexus/software_engineer",
)
# Paths that may change code behavior: medium unless a rule below says otherwise.
_BEHAVIOR_PREFIXES: tuple[str, ...] = ("src", "lab", "pyproject.toml", "ROADMAP.md")
# Documentation and state files: low.
_LOW_PREFIXES: tuple[str, ...] = (
    "README.md",
    "CHANGELOG.md",
    "CODEX_HANDOFF.md",
    "PROJECT_STATE.md",
    "BRAIN.md",
    "docs/adr",
    "docs/runbooks",
)
_TEST_PREFIXES: tuple[str, ...] = ("tests",)


def path_risk(path: str, *, deletions: int = 0) -> tuple[RiskLevel, bool, str]:
    """Return ``(level, governing, reason)`` for one repository path."""

    if path_matches(path, GOVERNING_PATH_PREFIXES):
        return RiskLevel.HIGH, True, f"{path} is a governing path"
    if is_sensitive_path(path):
        return RiskLevel.HIGH, True, f"{path} is a sensitive path"
    if path_matches(path, _TEST_PREFIXES):
        if deletions:
            return RiskLevel.MEDIUM, False, f"{path} removes test lines"
        return RiskLevel.LOW, False, f"{path} only adds test lines"
    if path_matches(path, _LOW_PREFIXES) or (
        path.endswith(".md") and not path_matches(path, _BEHAVIOR_PREFIXES)
    ):
        return RiskLevel.LOW, False, f"{path} is documentation or state"
    if path_matches(path, _BEHAVIOR_PREFIXES):
        return RiskLevel.MEDIUM, False, f"{path} can change behavior"
    return RiskLevel.HIGH, False, f"{path} is not covered by a path rule"


def classify_change(
    *,
    category: ChangeCategory,
    paths: Sequence[str],
    additions: int = 0,
    deletions: int = 0,
    diff_bytes: int = 0,
    budget: CycleBudget,
    derived_from_untrusted_text: bool = False,
) -> RiskAssessment:
    """Classify one change. When in doubt this function chooses the higher level."""

    reasons: list[str] = []
    category_level = category_risk(category)
    reasons.append(f"category {category.value} has a {category_level.value} floor")
    uncertain = category is ChangeCategory.UNKNOWN or derived_from_untrusted_text
    if category is ChangeCategory.UNKNOWN:
        reasons.append("the change category is unknown")
    if derived_from_untrusted_text:
        reasons.append("the candidate was derived from untrusted text")
    path_level = RiskLevel.LOW
    governing: list[str] = []
    if not paths:
        path_level = RiskLevel.HIGH
        uncertain = True
        reasons.append("no paths were declared, so the blast radius is unknown")
    for path in paths:
        level, is_governing, reason = path_risk(path, deletions=deletions)
        path_level = highest(path_level, level)
        if is_governing:
            governing.append(path)
        if level is not RiskLevel.LOW or is_governing:
            reasons.append(reason)
        if level is RiskLevel.HIGH and not is_governing:
            uncertain = True
    size_level = RiskLevel.LOW
    if len(paths) > budget.max_changed_files or diff_bytes > budget.max_diff_bytes:
        size_level = RiskLevel.MEDIUM
        reasons.append(
            f"the change touches {len(paths)} file(s) and {diff_bytes} diff byte(s), beyond the "
            f"autonomous limits ({budget.max_changed_files} files, {budget.max_diff_bytes} bytes)"
        )
    if deletions > additions and deletions > 50:
        size_level = highest(size_level, RiskLevel.MEDIUM)
        reasons.append("the change removes more than it adds")
    level = highest(category_level, path_level, size_level)
    if uncertain:
        level = level.raised()
        reasons.append("uncertainty raised the level")
    if governing:
        level = RiskLevel.HIGH
    return RiskAssessment(
        level=level,
        category_level=category_level,
        path_level=path_level,
        size_level=size_level,
        uncertain=uncertain,
        governing_paths=sorted(governing)[:100],
        reasons=[reason[:500] for reason in reasons][:50],
    )


__all__ = ["GOVERNING_PATH_PREFIXES", "classify_change", "path_risk"]
