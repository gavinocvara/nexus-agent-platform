"""Diff facts and the independent self-reviewer."""

from nexus.software_engineer.models import ChangeCategory, ReviewAnswer, ReviewQuestion
from nexus.software_engineer.review import SelfReviewer, diff_facts

PATCH = b"""diff --git a/src/nexus/web.py b/src/nexus/web.py
--- a/src/nexus/web.py
+++ b/src/nexus/web.py
@@ -1,6 +1,8 @@
+import threading
+registry = {}
-def public_helper():
-    return 1
+def _private_helper():
+    return random.random()
diff --git a/tests/unit/test_web.py b/tests/unit/test_web.py
--- a/tests/unit/test_web.py
+++ b/tests/unit/test_web.py
@@ -1,3 +1,2 @@
-    assert public_helper() == 1
+    pass
"""


def test_diff_facts_extract_review_relevant_signals() -> None:
    facts = diff_facts(PATCH, ["src/nexus/web.py", "tests/unit/test_web.py"], "a" * 64)
    assert facts.additions == 5 and facts.deletions == 3
    assert facts.test_files_changed == ["tests/unit/test_web.py"]
    assert facts.source_files_changed == ["src/nexus/web.py"]
    assert facts.assertions_removed == 1
    assert facts.public_definitions_removed == ["public_helper"]
    assert facts.concurrency_added and facts.nondeterminism_added and facts.global_state_added
    assert not facts.secret_like_added and not facts.instruction_like_added


def test_reviewer_raises_concerns_from_facts_and_stays_unknown_without_evidence() -> None:
    facts = diff_facts(PATCH, ["src/nexus/web.py", "tests/unit/test_web.py"], "a" * 64)
    review = SelfReviewer().review(
        facts, ChangeCategory.BEHAVIOR_CHANGE, tests_ran=False, root_cause_evidence=False
    )
    answers = {item.question: item.answer for item in review.items}
    assert answers[ReviewQuestion.TEST_WEAKENED] is ReviewAnswer.CONCERN
    assert answers[ReviewQuestion.PUBLIC_BEHAVIOR] is ReviewAnswer.CONCERN
    assert answers[ReviewQuestion.RACE] is ReviewAnswer.CONCERN
    assert answers[ReviewQuestion.NONDETERMINISM] is ReviewAnswer.CONCERN
    assert answers[ReviewQuestion.HIDDEN_STATE] is ReviewAnswer.CONCERN
    assert answers[ReviewQuestion.OTHER_PATHS] is ReviewAnswer.UNKNOWN
    assert answers[ReviewQuestion.ROOT_CAUSE] is ReviewAnswer.UNKNOWN
    assert answers[ReviewQuestion.HUMAN_REVIEW] is ReviewAnswer.CONCERN
    assert review.blocking and review.requires_human
    assert review.reviewed_diff_sha256 == "a" * 64


def test_clean_mechanical_change_reviews_clear() -> None:
    patch = b"""diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-teh platform
+the platform
"""
    facts = diff_facts(patch, ["README.md"], "b" * 64)
    review = SelfReviewer().review(
        facts, ChangeCategory.DOCUMENTATION_CORRECTION, tests_ran=True, root_cause_evidence=False
    )
    assert review.concerns == []
    assert not review.blocking and not review.requires_human
