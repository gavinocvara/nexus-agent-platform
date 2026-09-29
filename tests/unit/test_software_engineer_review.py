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


DELETIONS = b"""diff --git a/src/pkg/api.py b/src/pkg/api.py
deleted file mode 100644
index 1111111..0000000
--- a/src/pkg/api.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def public_entry():
-    return 1
diff --git a/tests/test_api.py b/tests/test_api.py
deleted file mode 100644
index 2222222..0000000
--- a/tests/test_api.py
+++ /dev/null
@@ -1,4 +0,0 @@
-def test_entry():
-    assert public_entry() == 1
-    self.assertEqual(public_entry(), 1)
-    with pytest.raises(ValueError):
"""


def test_deleted_files_are_attributed_to_their_own_paths() -> None:
    """A deleted file's lines once counted against the previous file, or none at all when it
    came first: deleting a public module or a whole test file then reviewed clear."""

    facts = diff_facts(DELETIONS, ["src/pkg/api.py", "tests/test_api.py"], None)
    assert facts.public_definitions_removed == ["public_entry"]
    assert facts.assertions_removed == 3  # assert, unittest assertion, expected exception
    review = SelfReviewer().review(
        facts, ChangeCategory.DEAD_CODE_REMOVAL, tests_ran=True, root_cause_evidence=False
    )
    answers = {item.question: item.answer for item in review.items}
    assert answers[ReviewQuestion.PUBLIC_BEHAVIOR] is ReviewAnswer.CONCERN
    assert answers[ReviewQuestion.TEST_WEAKENED] is ReviewAnswer.CONCERN
    assert review.requires_human


def test_tests_weakened_by_added_skips_are_a_concern() -> None:
    patch = b"""diff --git a/tests/test_api.py b/tests/test_api.py
--- a/tests/test_api.py
+++ b/tests/test_api.py
@@ -1,2 +1,4 @@
+import pytest
+@pytest.mark.skip(reason="flaky")
 def test_entry():
+    pytest.xfail("known")
     assert public_entry() == 1
"""
    facts = diff_facts(patch, ["tests/test_api.py"], None)
    assert facts.skips_added == 2 and facts.assertions_removed == 0
    review = SelfReviewer().review(
        facts, ChangeCategory.TEST_REPAIR, tests_ran=True, root_cause_evidence=False
    )
    answers = {item.question: item.answer for item in review.items}
    assert answers[ReviewQuestion.TEST_WEAKENED] is ReviewAnswer.CONCERN
    assert review.blocking
    # The same markers in production code are not test weakening.
    source = patch.replace(b"tests/test_api.py", b"src/pkg/api.py")
    assert diff_facts(source, ["src/pkg/api.py"], None).skips_added == 0


def test_hunk_lines_that_look_like_headers_stay_content() -> None:
    patch = b"""diff --git a/db/schema.sql b/db/schema.sql
--- a/db/schema.sql
+++ b/db/schema.sql
@@ -1,2 +1,2 @@
--- a comment
+++counter
diff --git a/tests/test_db.py b/tests/test_db.py
--- a/tests/test_db.py
+++ b/tests/test_db.py
@@ -1 +1 @@
-    assert rows == 2
+    assert rows
"""
    facts = diff_facts(patch, ["db/schema.sql", "tests/test_db.py"], None)
    assert (facts.additions, facts.deletions) == (2, 2)
    assert facts.assertions_removed == 1


def test_any_concern_needs_a_human_even_on_a_non_critical_question() -> None:
    """A concern is the reviewer finding a problem; it never weighs less than an unknown."""

    patch = b"""diff --git a/src/pkg/api.py b/src/pkg/api.py
--- a/src/pkg/api.py
+++ b/src/pkg/api.py
@@ -1 +1,2 @@
+import threading
 x = 1
diff --git a/tests/test_api.py b/tests/test_api.py
--- a/tests/test_api.py
+++ b/tests/test_api.py
@@ -1 +1,2 @@
 def test_x():
+    assert x == 1
"""
    review = SelfReviewer().review(
        diff_facts(patch, ["src/pkg/api.py", "tests/test_api.py"], None),
        ChangeCategory.MICRO_REFACTOR,
        tests_ran=True,
        root_cause_evidence=True,
    )
    answers = {item.question: item.answer for item in review.items}
    assert answers[ReviewQuestion.RACE] is ReviewAnswer.CONCERN
    assert [item.question for item in review.concerns] == [ReviewQuestion.RACE]
    assert not review.blocking  # RACE is not a critical question
    assert review.requires_human
