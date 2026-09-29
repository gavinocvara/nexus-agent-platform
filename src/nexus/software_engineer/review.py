"""Independent self-review of a produced change.

The reviewer sees only runtime-derived facts about the diff and the change category. It
shares no state with whatever produced the change, so it cannot rubber-stamp its own
reasoning. Every question is answered ``clear``, ``concern``, or ``unknown``; an unknown
is never rounded up to clear.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import Field

from nexus.atlas.models import Sha256, StrictModel
from nexus.patchforge.gateway import is_sensitive_path, path_matches
from nexus.patchforge.models import RepositoryPath
from nexus.software_engineer.models import (
    ChangeCategory,
    ReviewAnswer,
    ReviewItem,
    ReviewQuestion,
    RiskLevel,
    SelfReview,
    category_risk,
)
from nexus.software_engineer.trust import detect_instruction_like_text

_ADDED = re.compile(r"^\+(.*)$")
_REMOVED = re.compile(r"^-(.*)$")
_CONCURRENCY = re.compile(r"\b(threading|multiprocessing|concurrent\.futures|create_task|gather)\b")
_NONDETERMINISM = re.compile(
    r"\b(random\.|time\.time\(|perf_counter\(|datetime\.now\(\)|uuid4\(|os\.urandom)"
)
_GLOBAL_STATE = re.compile(
    r"^\s*global\s+\w+|^[A-Za-z_][A-Za-z0-9_]*\s*=\s*(\[\]|\{\}|set\(\))\s*$"
)
_SECRET_LIKE = re.compile(
    r"(sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|password\s*=\s*['\"][^'\"]+)",
    re.IGNORECASE,
)
_PUBLIC_DEF = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z][A-Za-z0-9_]*)")
# Checks a test makes: pytest asserts, unittest assertions, and expected-exception blocks.
_ASSERT = re.compile(
    r"^\s*(?:assert\b|self\.(?:assert|fail)\w*\(|(?:with\s+)?pytest\.(?:raises|warns)\b)"
)
# Ways to weaken a test by adding lines: skip or expect failure instead of checking.
_TEST_SKIP = re.compile(
    r"\bpytest\.(?:skip|xfail|importorskip)\s*\(|@pytest\.mark\.(?:skip|skipif|xfail)\b"
    r"|\bunittest\.(?:skip|skipIf|skipUnless|expectedFailure)\b|\bself\.skipTest\s*\("
)
_TEST_PREFIXES = ("tests",)


class DiffFacts(StrictModel):
    """Facts a runtime derives from a unified diff; no model authored them."""

    changed_files: list[RepositoryPath] = Field(default_factory=list, max_length=1000)
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    diff_sha256: Sha256 | None = None
    test_files_changed: list[RepositoryPath] = Field(default_factory=list, max_length=500)
    source_files_changed: list[RepositoryPath] = Field(default_factory=list, max_length=500)
    assertions_removed: int = Field(default=0, ge=0)
    skips_added: int = Field(default=0, ge=0)
    """Skip or expected-failure markers added to test files."""
    public_definitions_removed: list[str] = Field(default_factory=list, max_length=200)
    concurrency_added: bool = False
    nondeterminism_added: bool = False
    global_state_added: bool = False
    secret_like_added: bool = False
    instruction_like_added: bool = False
    sensitive_paths: list[RepositoryPath] = Field(default_factory=list, max_length=100)


def diff_facts(patch: bytes, changed_files: Sequence[str], diff_sha256: str | None) -> DiffFacts:
    """Derive review facts from a Git unified diff, per changed file."""

    text = patch.decode("utf-8", errors="replace")
    additions = 0
    deletions = 0
    assertions_removed = 0
    skips_added = 0
    removed_defs: list[str] = []
    added_defs: set[str] = set()
    concurrency = nondeterminism = global_state = secret_like = instruction_like = False
    current: str | None = None
    old_path: str | None = None
    header = True  # a plain unified diff starts with its file names, without "diff --git"
    for line in text.splitlines():
        # Every file section starts afresh; a deleted file's lines belong to its old path,
        # never to whichever file the diff showed before it. File names appear only in the
        # header, so a hunk line such as "--- x" (a removed "-- x") stays content.
        if line.startswith("diff --git "):
            current = old_path = None
            header = True
            continue
        if line.startswith("@@"):
            header = False
            continue
        if header:
            if line.startswith("--- "):
                old_path = line[6:] if line.startswith("--- a/") else None
            elif line.startswith("+++ "):
                current = line[6:] if line.startswith("+++ b/") else old_path
            continue
        added = _ADDED.match(line)
        removed = _REMOVED.match(line)
        if added:
            additions += 1
            content = added.group(1)
            concurrency |= bool(_CONCURRENCY.search(content))
            nondeterminism |= bool(_NONDETERMINISM.search(content))
            global_state |= bool(_GLOBAL_STATE.search(content))
            secret_like |= bool(_SECRET_LIKE.search(content))
            instruction_like |= bool(detect_instruction_like_text(content))
            if current is not None and path_matches(current, _TEST_PREFIXES):
                skips_added += bool(_TEST_SKIP.search(content))
            definition = _PUBLIC_DEF.match(content)
            if definition:
                added_defs.add(definition.group(1))
        elif removed:
            deletions += 1
            content = removed.group(1)
            if (
                current is not None
                and path_matches(current, _TEST_PREFIXES)
                and _ASSERT.match(content)
            ):
                assertions_removed += 1
            definition = _PUBLIC_DEF.match(content)
            if (
                definition
                and not definition.group(1).startswith("_")
                and current is not None
                and not path_matches(current, _TEST_PREFIXES)
            ):
                removed_defs.append(definition.group(1))
    files = sorted(set(changed_files))
    return DiffFacts(
        changed_files=files[:1000],
        additions=additions,
        deletions=deletions,
        diff_sha256=diff_sha256,
        test_files_changed=[path for path in files if path_matches(path, _TEST_PREFIXES)][:500],
        source_files_changed=[path for path in files if not path_matches(path, _TEST_PREFIXES)][
            :500
        ],
        assertions_removed=assertions_removed,
        skips_added=skips_added,
        public_definitions_removed=sorted(set(removed_defs) - added_defs)[:200],
        concurrency_added=concurrency,
        nondeterminism_added=nondeterminism,
        global_state_added=global_state,
        secret_like_added=secret_like,
        instruction_like_added=instruction_like,
        sensitive_paths=[path for path in files if is_sensitive_path(path)][:100],
    )


class SelfReviewer:
    """Deterministic reviewer; a model-backed reviewer may later add judgment, never authority."""

    def review(
        self,
        facts: DiffFacts,
        category: ChangeCategory,
        *,
        tests_ran: bool,
        root_cause_evidence: bool,
    ) -> SelfReview:
        risk = category_risk(category)
        items: list[ReviewItem] = []

        def add(question: ReviewQuestion, answer: ReviewAnswer, note: str) -> None:
            items.append(ReviewItem(question=question, answer=answer, note=note[:500]))

        mechanical = category in {
            ChangeCategory.FORMATTING,
            ChangeCategory.TYPE_ANNOTATION,
            ChangeCategory.DOCUMENTATION_CORRECTION,
            ChangeCategory.DEAD_CODE_REMOVAL,
        }
        add(
            ReviewQuestion.ROOT_CAUSE,
            ReviewAnswer.CLEAR if mechanical or root_cause_evidence else ReviewAnswer.UNKNOWN,
            "mechanical change"
            if mechanical
            else (
                "root cause evidence recorded" if root_cause_evidence else "no root-cause evidence"
            ),
        )
        add(
            ReviewQuestion.OTHER_PATHS,
            ReviewAnswer.CLEAR if tests_ran else ReviewAnswer.UNKNOWN,
            "full suite ran" if tests_ran else "the full suite did not run",
        )
        add(
            ReviewQuestion.PUBLIC_BEHAVIOR,
            ReviewAnswer.CONCERN if facts.public_definitions_removed else ReviewAnswer.CLEAR,
            (
                "removed public definitions: " + ", ".join(facts.public_definitions_removed[:5])
                if facts.public_definitions_removed
                else "no public definition removed"
            ),
        )
        add(
            ReviewQuestion.INVARIANT,
            ReviewAnswer.CONCERN
            if risk is RiskLevel.HIGH or facts.sensitive_paths
            else ReviewAnswer.CLEAR,
            "high-risk category or sensitive path"
            if risk is RiskLevel.HIGH or facts.sensitive_paths
            else "no invariant-bearing path touched",
        )
        weakened = facts.assertions_removed or facts.skips_added
        add(
            ReviewQuestion.TEST_WEAKENED,
            ReviewAnswer.CONCERN if weakened else ReviewAnswer.CLEAR,
            f"{facts.assertions_removed} check(s) removed, {facts.skips_added} skip or "
            "expected-failure marker(s) added"
            if weakened
            else "no check removed and no skip added",
        )
        add(
            ReviewQuestion.RACE,
            ReviewAnswer.CONCERN if facts.concurrency_added else ReviewAnswer.CLEAR,
            "concurrency primitives added" if facts.concurrency_added else "no concurrency added",
        )
        add(
            ReviewQuestion.NONDETERMINISM,
            ReviewAnswer.CONCERN if facts.nondeterminism_added else ReviewAnswer.CLEAR,
            "clock, random, or uuid4 use added"
            if facts.nondeterminism_added
            else "no nondeterministic source added",
        )
        add(
            ReviewQuestion.COMPLEXITY,
            ReviewAnswer.UNKNOWN if facts.additions > 200 else ReviewAnswer.CLEAR,
            f"{facts.additions} line(s) added" if facts.additions > 200 else "small change",
        )
        add(
            ReviewQuestion.HIDDEN_STATE,
            ReviewAnswer.CONCERN if facts.global_state_added else ReviewAnswer.CLEAR,
            "module-level mutable state added"
            if facts.global_state_added
            else "no hidden state added",
        )
        add(
            ReviewQuestion.SENSITIVE_DATA,
            ReviewAnswer.CONCERN
            if facts.secret_like_added or facts.sensitive_paths
            else ReviewAnswer.CLEAR,
            "secret-shaped content or sensitive path"
            if facts.secret_like_added or facts.sensitive_paths
            else "no sensitive data",
        )
        add(
            ReviewQuestion.PROMPT_INJECTION,
            ReviewAnswer.CONCERN
            if facts.instruction_like_added or category is ChangeCategory.PROMPT_CHANGE
            else ReviewAnswer.CLEAR,
            "instruction-like text or prompt change"
            if facts.instruction_like_added or category is ChangeCategory.PROMPT_CHANGE
            else "no instruction-like text added",
        )
        covers = bool(facts.test_files_changed) or not facts.source_files_changed or mechanical
        add(
            ReviewQuestion.TESTS_COVER,
            ReviewAnswer.CLEAR if covers and tests_ran else ReviewAnswer.UNKNOWN,
            "tests changed alongside the code"
            if facts.test_files_changed
            else (
                "no test accompanies the source change"
                if facts.source_files_changed and not mechanical
                else "no source behavior changed"
            ),
        )
        add(
            ReviewQuestion.SMALLER,
            ReviewAnswer.UNKNOWN if facts.additions > 100 else ReviewAnswer.CLEAR,
            "large addition; a smaller solution was not ruled out"
            if facts.additions > 100
            else "minimal change",
        )
        add(
            ReviewQuestion.HUMAN_REVIEW,
            ReviewAnswer.CONCERN if risk is not RiskLevel.LOW else ReviewAnswer.CLEAR,
            f"{risk.value} risk category" if risk is not RiskLevel.LOW else "low-risk category",
        )
        return SelfReview(items=items, reviewed_diff_sha256=facts.diff_sha256)


__all__ = ["DiffFacts", "SelfReviewer", "diff_facts"]
