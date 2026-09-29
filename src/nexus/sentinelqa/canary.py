"""Runner-integrity canary: a planted failure the test runner must report.

The candidate's code runs in the same interpreter as the test runner. Without touching a
locked file or leaving a scanned marker it can change how results are *reported*: print a
forged summary line and force the exit status, or patch the runner's reports so every test
reads as passed. No check made from outside the process can prevent that, but SentinelQA
can check that the runner still tells the truth about one result whose correct outcome it
knows. Before a pass, it plants a test module that must fail next to the pristine tests,
runs the operator's full-suite command on the verification tree, and requires the runner
to report exactly that failure by name, with a failing exit status.

The canary catches manipulation that is not aware of it: forged summaries, forced exit
statuses, globally patched reports, swallowed exceptions. Code written to recognise and
spare the canary is not caught; ADR 0011 records that residual risk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from uuid import UUID

from nexus.patchforge.sandbox import SandboxExecution, SandboxStatus
from nexus.sentinelqa.models import SpecificationCounts, SpecificationKind, SpecificationLock
from nexus.sentinelqa.pytest_summary import parse_pytest_summary

CANARY_PREFIX = "test_sentinelqa_canary_"
_TOKEN = re.compile(r"^[0-9a-f]{16}$")
_MODULE = re.compile(r"^test_sentinelqa_canary_(?P<token>[0-9a-f]{16})\.py$")
_FAILED_LINE = re.compile(r"^FAILED (?P<node>\S+)(?: - .*)?$")
_MAX_STDOUT_SCAN_LINES = 2000


class CanaryError(RuntimeError):
    """The canary could not be planted faithfully."""


@dataclass(frozen=True, slots=True)
class RunnerCanary:
    """One review's canary: a test module that must fail, planted in ``directory``."""

    directory: str
    token: str

    def __post_init__(self) -> None:
        if not _TOKEN.fullmatch(self.token):
            raise ValueError("Canary token must be 16 lowercase hexadecimal characters")
        parts = PurePosixPath(self.directory).parts
        if not parts or ".." in parts or self.directory.startswith("/"):
            raise ValueError("Canary directory must be a relative repository path")

    @property
    def function(self) -> str:
        return f"{CANARY_PREFIX}{self.token}"

    @property
    def path(self) -> str:
        return f"{self.directory}/{self.function}.py"

    @property
    def content(self) -> bytes:
        return (
            '"""SentinelQA runner-integrity canary, planted for one review and removed '
            "afterwards.\n\nThis test must fail. A runner that reports it as anything but "
            "failed cannot be\ntrusted to report the specification's failures either.\n"
            '"""\n\n\n'
            f"def {self.function}() -> None:\n"
            '    raise AssertionError("SentinelQA runner-integrity canary: this failure is '
            'expected")\n'
        ).encode()

    @property
    def sha256(self) -> str:
        return sha256(self.content).hexdigest()

    def reported_failed(self, stdout: bytes) -> bool:
        """Whether the runner's short summary names this canary, at its planted path, as a
        failed test. A module of the same name elsewhere in the tree does not count."""

        node_id = f"{self.path}::{self.function}"
        lines = stdout.decode("utf-8", errors="replace").splitlines()[-_MAX_STDOUT_SCAN_LINES:]
        for raw in lines:
            match = _FAILED_LINE.match(raw.strip())
            if match is None:
                continue
            node = match.group("node")
            # pytest names tests relative to its rootdir: the tree root, or an ancestor.
            if node == node_id or node.endswith("/" + node_id):
                return True
        return False


def runner_canary(review_id: UUID, lock: SpecificationLock) -> RunnerCanary | None:
    """The canary for one review, or ``None`` if no locked pytest module shows where to put it.

    The canary goes into the directory that holds the most locked test modules, so the
    command that collects the specification collects the canary too. Its token is derived
    from the review; secrecy would not help, because code in the same process can list the
    directory anyway.
    """

    modules: dict[str, int] = {}
    for entry in lock.entries:
        if entry.kind is not SpecificationKind.TEST:
            continue
        path = PurePosixPath(entry.path)
        name = path.name
        if not name.endswith(".py") or not (name.startswith("test_") or name.endswith("_test.py")):
            continue
        if len(path.parts) < 2:
            continue
        directory = path.parent.as_posix()
        modules[directory] = modules.get(directory, 0) + 1
    if not modules:
        return None
    directory = min(modules, key=lambda item: (-modules[item], item))
    token = sha256(f"sentinelqa-runner-canary:{review_id}".encode()).hexdigest()[:16]
    return RunnerCanary(directory=directory, token=token)


def plant_canary(root: Path, canary: RunnerCanary) -> None:
    """Write the canary into a real directory of ``root``; never follow or replace anything."""

    current = root
    for part in PurePosixPath(canary.directory).parts:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise CanaryError("Canary directory is not a real directory of the tree")
    target = root.joinpath(*canary.path.split("/"))
    if target.exists() or target.is_symlink():
        raise CanaryError("Canary path already exists in the tree")
    with target.open("xb") as handle:
        handle.write(canary.content)


def remove_canary(root: Path, canary: RunnerCanary) -> None:
    target = root.joinpath(*canary.path.split("/"))
    if target.is_symlink() or target.is_file():
        target.unlink()


def planted_canaries(root: Path) -> list[RunnerCanary]:
    """Canaries present in ``root``; used only by sandboxes that never run repository code."""

    found: list[RunnerCanary] = []
    for path in sorted(root.rglob(f"{CANARY_PREFIX}*.py")):
        match = _MODULE.fullmatch(path.name)
        if match is None or not path.is_file() or path.is_symlink():
            continue
        directory = path.parent.relative_to(root).as_posix()
        if directory == ".":
            continue
        found.append(RunnerCanary(directory=directory, token=match.group("token")))
    return found


def report_planted_canaries(execution: SandboxExecution, workspace: Path) -> SandboxExecution:
    """What an honest pytest adds to a scripted run when canaries are planted in its tree.

    Scripted and content-oracle sandboxes never execute repository code, so they cannot
    collect the canary themselves. This reports each planted canary as failed, the way
    pytest's short summary does, and returns a failed execution with updated counts. A run
    without a parseable summary, or one that did not complete, is returned unchanged.
    """

    canaries = planted_canaries(workspace)
    if not canaries or execution.status not in {SandboxStatus.SUCCEEDED, SandboxStatus.FAILED}:
        return execution
    counts = parse_pytest_summary(execution.stdout)
    if counts is None:
        return execution
    counts = counts.model_copy(update={"failed": counts.failed + len(canaries)})
    lines = [f"FAILED {item.path}::{item.function} - AssertionError" for item in canaries]
    stdout = execution.stdout + ("\n".join([*lines, _format_summary(counts)]) + "\n").encode()
    return SandboxExecution.model_validate(
        {
            **execution.model_dump(),
            "status": SandboxStatus.FAILED,
            "exit_code": execution.exit_code or 1,
            "stdout": stdout,
            "error_code": execution.error_code or "command_failed",
        }
    )


def _format_summary(counts: SpecificationCounts) -> str:
    order = ("failed", "passed", "skipped", "deselected", "xfailed", "xpassed", "errors")
    parts = [
        f"{getattr(counts, name)} {'error' if name == 'errors' else name}"
        for name in order
        if getattr(counts, name)
    ]
    return ", ".join(parts) + " in 0.01s"


__all__ = [
    "CANARY_PREFIX",
    "CanaryError",
    "RunnerCanary",
    "plant_canary",
    "planted_canaries",
    "remove_canary",
    "report_planted_canaries",
    "runner_canary",
]
