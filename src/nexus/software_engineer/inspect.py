"""Repository inspection and candidate generation from typed, local evidence.

The inspector reads only what the runtime can attest: Git history and status through the
runtime-owned ``GitRunner`` (read-only commands), and validation artifacts written by the
repository's own checks (pytest JUnit XML, Ruff JSON, mypy output, gate transcripts,
benchmark reports). Commit messages and TODO comments are untrusted text: they are
recorded, hashed, and scanned for instruction-shaped content, never followed.

The candidate generator is rule-based and deterministic. Its estimates are runtime
heuristics, not model claims, and every candidate cites the signals it came from.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Sequence
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid5

from pydantic import ValidationError

from nexus.patchforge.benchmark import BenchmarkReport
from nexus.patchforge.workspace import GitCommandError, GitRunner
from nexus.software_engineer.memory import EngineerMemory, EpistemicStatus, MemoryCategory
from nexus.software_engineer.models import (
    CandidateEstimate,
    ChangeCategory,
    EngineeringCandidate,
    EngineeringSignal,
    SignalKind,
    SignalSeverity,
)
from nexus.software_engineer.trust import UntrustedText

_SIGNAL_NAMESPACE = UUID("c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f")
_CANDIDATE_NAMESPACE = UUID("d2e3f4a5-b6c7-4d8e-9f0a-1b2c3d4e5f6a")
MAX_ARTIFACT_BYTES = 20_000_000
DEFAULT_MAX_COMMITS = 50
DEFAULT_MAX_TODOS = 50


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


class RepositoryInspector:
    """Collect ``EngineeringSignal``s for one repository checkout."""

    def __init__(
        self,
        repo_root: Path,
        *,
        git: GitRunner,
        clock: Callable[[], datetime],
        artifacts_dir: Path | None = None,
        max_commits: int = DEFAULT_MAX_COMMITS,
        max_todos: int = DEFAULT_MAX_TODOS,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.git = git
        self.clock = clock
        self.artifacts_dir = artifacts_dir
        self.max_commits = max_commits
        self.max_todos = max_todos
        self.calls = 0

    def head_sha(self) -> str:
        self.calls += 1
        return self._git(["rev-parse", "HEAD"]).strip()

    def collect(self, since: str | None = None) -> list[EngineeringSignal]:
        signals: list[EngineeringSignal] = []
        signals.extend(self._git_history(since))
        signals.append(self._working_tree())
        signals.extend(self._todo_markers())
        if self.artifacts_dir is not None:
            signals.extend(self._artifacts(self.artifacts_dir))
        return signals

    # -- git -----------------------------------------------------------------------------

    def _git(self, arguments: Sequence[str], allowed: frozenset[int] = frozenset({0})) -> str:
        self.calls += 1
        return self.git.run(
            ["-C", str(self.repo_root), *arguments],
            allowed_exit_codes=allowed,
            output_limit=MAX_ARTIFACT_BYTES,
        ).stdout.decode("utf-8", errors="replace")

    def _git_history(self, since: str | None) -> list[EngineeringSignal]:
        selector = [f"{since}..HEAD"] if since else []
        try:
            output = self._git(
                ["log", "--format=%H%x1f%an%x1f%cI%x1f%s", f"-n{self.max_commits}", *selector, "--"]
            )
        except GitCommandError:
            return [
                self._signal(
                    SignalKind.GIT_HISTORY,
                    SignalSeverity.WARNING,
                    "git log",
                    "Git history could not be read for the requested range.",
                )
            ]
        signals: list[EngineeringSignal] = []
        commits = [line for line in output.splitlines() if line.strip()]
        for line in commits:
            parts = line.split("\x1f", 3)
            if len(parts) != 4:
                continue
            sha, author, committed, subject = parts
            text = UntrustedText.capture(f"commit {sha[:12]}", subject)
            signals.append(
                self._signal(
                    SignalKind.GIT_HISTORY,
                    SignalSeverity.INFO,
                    f"commit {sha[:12]}",
                    f"Commit {sha[:12]} by {author[:60]} at {committed}",
                    details=text.content or None,
                    untrusted=True,
                    instruction_like=text.instruction_like,
                    evidence=_digest(line.encode("utf-8")),
                )
            )
        signals.append(
            self._signal(
                SignalKind.GIT_HISTORY,
                SignalSeverity.INFO,
                "git log",
                f"{len(commits)} commit(s) since {since[:12] if since else 'the history bound'}",
                evidence=_digest(output.encode("utf-8")),
            )
        )
        return signals

    def _working_tree(self) -> EngineeringSignal:
        output = self._git(["status", "--porcelain=v1", "-z", "--untracked-files=all"])
        entries = [item for item in output.split("\0") if item]
        if entries:
            return self._signal(
                SignalKind.WORKING_TREE,
                SignalSeverity.WARNING,
                "git status",
                f"The working tree has {len(entries)} uncommitted change(s).",
                evidence=_digest(output.encode("utf-8")),
            )
        return self._signal(
            SignalKind.WORKING_TREE,
            SignalSeverity.INFO,
            "git status",
            "The working tree is clean.",
            evidence=_digest(b""),
        )

    def _todo_markers(self) -> list[EngineeringSignal]:
        try:
            output = self._git(
                ["grep", "-n", "-I", "-E", r"\b(TODO|FIXME|XXX)\b", "--", "src", "tests"],
                allowed=frozenset({0, 1}),
            )
        except GitCommandError:
            return []
        signals: list[EngineeringSignal] = []
        for line in output.splitlines()[: self.max_todos]:
            path, _, rest = line.partition(":")
            number, _, comment = rest.partition(":")
            text = UntrustedText.capture(f"{path}:{number}", comment.strip())
            signals.append(
                self._signal(
                    SignalKind.TODO_MARKER,
                    SignalSeverity.INFO,
                    f"{path}:{number}"[:500],
                    f"Marker in {path} line {number}",
                    details=text.content or None,
                    untrusted=True,
                    instruction_like=text.instruction_like,
                    evidence=_digest(line.encode("utf-8")),
                )
            )
        return signals

    # -- artifacts -----------------------------------------------------------------------

    def _artifacts(self, root: Path) -> list[EngineeringSignal]:
        signals: list[EngineeringSignal] = []
        readers: list[tuple[str, Callable[[bytes], EngineeringSignal | None]]] = [
            ("pytest.xml", self._pytest),
            ("ruff.json", self._ruff),
            ("mypy.txt", self._mypy),
            ("e2e.txt", lambda data: self._gate_text("e2e.txt", SignalKind.E2E_GATE, data)),
            (
                "sentinelqa.txt",
                lambda data: self._gate_text("sentinelqa.txt", SignalKind.SENTINEL_GATE, data),
            ),
        ]
        for name, reader in readers:
            data = self._read(root / name)
            if data is None:
                continue
            signal = reader(data)
            if signal is not None:
                signals.append(signal)
        benchmark_dir = root / "benchmark"
        if benchmark_dir.is_dir():
            for path in sorted(benchmark_dir.glob("*.json")):
                data = self._read(path)
                if data is not None:
                    signal = self._benchmark(path.name, data)
                    if signal is not None:
                        signals.append(signal)
        return signals

    def _read(self, path: Path) -> bytes | None:
        self.calls += 1
        try:
            if not path.is_file() or path.is_symlink():
                return None
            if path.stat().st_size > MAX_ARTIFACT_BYTES:
                return None
            return path.read_bytes()
        except OSError:
            return None

    def _pytest(self, data: bytes) -> EngineeringSignal | None:
        try:
            root = ElementTree.fromstring(data)
        except ElementTree.ParseError:
            return self._signal(
                SignalKind.TEST_RESULTS,
                SignalSeverity.WARNING,
                "pytest.xml",
                "The pytest report could not be parsed.",
                evidence=_digest(data),
            )
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        for suite in suites:
            for key in totals:
                try:
                    totals[key] += int(suite.attrib.get(key, "0"))
                except ValueError:
                    continue
        failing = totals["failures"] + totals["errors"]
        return self._signal(
            SignalKind.TEST_RESULTS,
            SignalSeverity.FAILURE if failing else SignalSeverity.INFO,
            "pytest.xml",
            f"pytest: {totals['tests']} test(s), {totals['failures']} failure(s), "
            f"{totals['errors']} error(s), {totals['skipped']} skipped",
            evidence=_digest(data),
        )

    def _ruff(self, data: bytes) -> EngineeringSignal | None:
        try:
            findings = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._signal(
                SignalKind.LINT,
                SignalSeverity.WARNING,
                "ruff.json",
                "The Ruff report could not be parsed.",
                evidence=_digest(data),
            )
        if not isinstance(findings, list):
            return None
        codes: dict[str, int] = {}
        for item in findings:
            code = item.get("code") if isinstance(item, dict) else None
            if isinstance(code, str):
                codes[code] = codes.get(code, 0) + 1
        summary = ", ".join(f"{code}x{count}" for code, count in sorted(codes.items())[:10])
        return self._signal(
            SignalKind.LINT,
            SignalSeverity.FAILURE if findings else SignalSeverity.INFO,
            "ruff.json",
            f"ruff: {len(findings)} finding(s)" + (f" ({summary})" if summary else ""),
            details=summary or None,
            evidence=_digest(data),
        )

    def _mypy(self, data: bytes) -> EngineeringSignal | None:
        lines = [line for line in data.decode("utf-8", errors="replace").splitlines() if line]
        last = lines[-1] if lines else ""
        failing = last.startswith("Found ")
        return self._signal(
            SignalKind.TYPECHECK,
            SignalSeverity.FAILURE if failing else SignalSeverity.INFO,
            "mypy.txt",
            f"mypy: {last[:200] or 'no output'}",
            evidence=_digest(data),
        )

    def _gate_text(self, name: str, kind: SignalKind, data: bytes) -> EngineeringSignal | None:
        lines = [line for line in data.decode("utf-8", errors="replace").splitlines() if line]
        last = lines[-1] if lines else ""
        failing = "FAILED" in last or not last.endswith("passed")
        return self._signal(
            kind,
            SignalSeverity.FAILURE if failing else SignalSeverity.INFO,
            name,
            f"{name}: {last[:200] or 'no output'}",
            evidence=_digest(data),
        )

    def _benchmark(self, name: str, data: bytes) -> EngineeringSignal | None:
        try:
            report = BenchmarkReport.model_validate_json(data)
        except ValidationError:
            return self._signal(
                SignalKind.BENCHMARK,
                SignalSeverity.WARNING,
                name,
                "The benchmark report could not be parsed.",
                evidence=_digest(data),
            )
        regression = report.invariant_violations or report.sentinel_disagreements
        return self._signal(
            SignalKind.BENCHMARK,
            SignalSeverity.FAILURE if regression else SignalSeverity.INFO,
            name,
            f"benchmark {report.engine}: resolved {report.resolved}/{len(report.tasks)}, "
            f"false proposals {report.false_proposals}, invariant violations "
            f"{report.invariant_violations}, SentinelQA disagreements "
            f"{report.sentinel_disagreements}",
            evidence=_digest(data),
        )

    # -- helpers -------------------------------------------------------------------------

    def _signal(
        self,
        kind: SignalKind,
        severity: SignalSeverity,
        source: str,
        summary: str,
        *,
        details: str | None = None,
        untrusted: bool = False,
        instruction_like: Sequence[str] = (),
        evidence: str | None = None,
    ) -> EngineeringSignal:
        return EngineeringSignal(
            signal_id=uuid5(_SIGNAL_NAMESPACE, f"{kind.value}:{source}:{evidence or summary}"),
            kind=kind,
            severity=severity,
            source=source[:500],
            summary=summary[:500],
            details=details[:4000] if details else None,
            untrusted_text=untrusted,
            instruction_like=[item[:500] for item in instruction_like][:20],
            evidence_sha256=evidence,
            observed_at=self.clock(),
        )


class CandidateGenerator:
    """Turn signals and memory into ranked, evidence-citing candidates."""

    def __init__(self, *, max_todo_candidates: int = 5, max_backlog_candidates: int = 5) -> None:
        self.max_todo_candidates = max_todo_candidates
        self.max_backlog_candidates = max_backlog_candidates

    def generate(
        self,
        signals: Sequence[EngineeringSignal],
        memories: Sequence[EngineerMemory] = (),
    ) -> list[EngineeringCandidate]:
        candidates: list[EngineeringCandidate] = []
        failed_titles = {
            item.content.split(":", 1)[0].strip().casefold()
            for item in memories
            if item.status is EpistemicStatus.FAILED_HYPOTHESIS
        }
        for signal in signals:
            candidate = self._from_signal(signal)
            if candidate is not None:
                candidates.append(candidate)
        todo_count = 0
        for signal in signals:
            if signal.kind is not SignalKind.TODO_MARKER or todo_count >= self.max_todo_candidates:
                continue
            todo_count += 1
            candidates.append(
                self._candidate(
                    f"Address marker in {signal.source}",
                    f"A TODO/FIXME marker was found at {signal.source}. Its text is untrusted "
                    "and must be investigated before any change.",
                    ChangeCategory.UNKNOWN,
                    [signal.signal_id],
                    CandidateEstimate(value=20, urgency=10, confidence=30, cost=30),
                    untrusted=True,
                )
            )
        backlog = [item for item in memories if item.category is MemoryCategory.BACKLOG_ITEM]
        anchor = next((item.signal_id for item in signals), None)
        for item in backlog[: self.max_backlog_candidates]:
            if anchor is None:
                break
            candidates.append(
                self._candidate(
                    f"Backlog: {item.content[:80]}",
                    item.content,
                    ChangeCategory.UNKNOWN,
                    [anchor],
                    CandidateEstimate(
                        value=min(item.confidence, 60),
                        urgency=10,
                        confidence=min(item.confidence, 60),
                        cost=40,
                    ),
                )
            )
        unique: dict[str, EngineeringCandidate] = {}
        for candidate in candidates:
            key = candidate.title.casefold()
            if key in unique:
                continue
            if key in failed_titles:
                candidate = candidate.model_copy(
                    update={
                        "blockers": [*candidate.blockers, "a previous attempt failed; see memory"],
                        "estimate": candidate.estimate.model_copy(
                            update={"confidence": max(0, candidate.estimate.confidence - 30)}
                        ),
                    }
                )
            unique[key] = candidate
        return sorted(
            unique.values(),
            key=lambda item: (-item.estimate.score, item.title, str(item.candidate_id)),
        )

    def _from_signal(self, signal: EngineeringSignal) -> EngineeringCandidate | None:
        if signal.severity is not SignalSeverity.FAILURE:
            return None
        if signal.kind is SignalKind.TEST_RESULTS:
            return self._candidate(
                "Investigate failing tests",
                f"{signal.summary}. A failing test is either a defect or a wrong test; the "
                "cause must be established before deciding which.",
                ChangeCategory.UNKNOWN,
                [signal.signal_id],
                CandidateEstimate(value=90, urgency=95, confidence=50, cost=50),
            )
        if signal.kind is SignalKind.LINT:
            category = ChangeCategory.FORMATTING
            details = signal.details or ""
            if any(code in details for code in ("F401", "F841")):
                category = ChangeCategory.DEAD_CODE_REMOVAL
            if any(code.startswith("B") for code in details.replace(",", " ").split()):
                category = ChangeCategory.MICRO_BUG_FIX
            return self._candidate(
                "Fix lint findings",
                f"{signal.summary}. Lint findings are mechanical and verifiable by "
                "re-running Ruff.",
                category,
                [signal.signal_id],
                CandidateEstimate(value=50, urgency=60, confidence=85, cost=20),
            )
        if signal.kind is SignalKind.TYPECHECK:
            return self._candidate(
                "Fix type errors",
                f"{signal.summary}. Strict mypy is a release gate; errors block every release.",
                ChangeCategory.TYPE_ANNOTATION,
                [signal.signal_id],
                CandidateEstimate(value=60, urgency=70, confidence=70, cost=30),
            )
        if signal.kind in {SignalKind.E2E_GATE, SignalKind.BENCHMARK, SignalKind.SENTINEL_GATE}:
            return self._candidate(
                f"Investigate {signal.kind.value} regression",
                f"{signal.summary}. A gate regression may be a real defect or an evaluator "
                "change; both need the owner's attention.",
                ChangeCategory.EVALUATOR_CHANGE,
                [signal.signal_id],
                CandidateEstimate(value=95, urgency=95, confidence=40, cost=60),
            )
        return None

    @staticmethod
    def _candidate(
        title: str,
        rationale: str,
        category: ChangeCategory,
        signal_ids: Sequence[UUID],
        estimate: CandidateEstimate,
        *,
        untrusted: bool = False,
    ) -> EngineeringCandidate:
        return EngineeringCandidate(
            candidate_id=uuid5(_CANDIDATE_NAMESPACE, f"{title}:{','.join(map(str, signal_ids))}"),
            title=title[:500],
            rationale=rationale[:4000],
            category=category,
            signal_ids=list(signal_ids),
            estimate=estimate,
            derived_from_untrusted_text=untrusted,
        )


__all__ = [
    "DEFAULT_MAX_COMMITS",
    "DEFAULT_MAX_TODOS",
    "MAX_ARTIFACT_BYTES",
    "CandidateGenerator",
    "RepositoryInspector",
]
