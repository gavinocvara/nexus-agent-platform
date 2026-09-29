"""SentinelQA specification lock: Git-object capture, classification, and tree checks."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.e2e import FixtureRepository, materialize_fixture
from nexus.patchforge.e2e_catalog import CALCULATOR, calculator_profile
from nexus.patchforge.workspace import GitRunner
from nexus.sentinelqa.lock import (
    SpecificationLockError,
    capture_specification_lock,
    classify_specification_path,
    compare_tree_with_lock,
    inspect_file,
    specification_files_in_tree,
    tree_sha256,
    walk_tree,
)
from nexus.sentinelqa.models import SpecificationKind, SpecificationLock

NOW = datetime(2026, 1, 1, tzinfo=UTC)
# Pinned identities of the calculator fixture's specification lock. A change here means the
# lock algorithm, the fixture, or the operator profile changed; each must be deliberate.
CALCULATOR_SOURCE_SHA = "8ee76861743bca5deee4a120b9863c5bc67df6fe"
CALCULATOR_TEST_SHA256 = "58020fb1803cb52a32eac4aa38eca696fec925e288e62de68f8cf73c151235b5"
CALCULATOR_LOCK_SHA256 = "61092c13a65d12dc727a8b9fe9b9bb6781c84a94e581ba77aa4ef4b7fa374e04"


def _repository(tmp_path: Path, fixture: FixtureRepository = CALCULATOR) -> tuple[Path, str]:
    git = GitRunner(tmp_path / "git")
    source = tmp_path / "source"
    sha = materialize_fixture(fixture, source, git, NOW)
    return source, sha


def test_lock_is_captured_from_git_objects_not_the_working_tree(tmp_path: Path) -> None:
    source, sha = _repository(tmp_path)
    git = GitRunner(tmp_path / "git")
    # Dirty the checkout: the lock must still describe the committed specification.
    (source / "tests" / "test_calculator.py").write_text("tampered\n", encoding="utf-8")
    lock = capture_specification_lock(git, source, sha, calculator_profile(), captured_at=NOW)
    assert [item.path for item in lock.entries] == ["tests/test_calculator.py"]
    entry = lock.entries[0]
    assert entry.kind is SpecificationKind.TEST and entry.mode == "100644"
    assert entry.size_bytes == len(CALCULATOR.files["tests/test_calculator.py"])
    assert lock.profile_sha256 == canonical_sha256(calculator_profile())
    assert lock.source_sha == sha
    # Identity excludes the capture time.
    later = lock.model_copy(update={"captured_at": datetime(2027, 1, 1, tzinfo=UTC)})
    assert later.lock_sha256 == lock.lock_sha256
    with pytest.raises(SpecificationLockError, match="timezone-aware"):
        capture_specification_lock(
            git, source, sha, calculator_profile(), captured_at=NOW.replace(tzinfo=None)
        )
    with pytest.raises(SpecificationLockError, match="not present"):
        capture_specification_lock(git, source, "0" * 40, calculator_profile(), captured_at=NOW)


def test_lock_identity_is_pinned(tmp_path: Path) -> None:
    source, sha = _repository(tmp_path)
    lock = capture_specification_lock(
        GitRunner(tmp_path / "git"), source, sha, calculator_profile(), captured_at=NOW
    )
    assert sha == CALCULATOR_SOURCE_SHA
    assert lock.entries[0].sha256 == CALCULATOR_TEST_SHA256
    assert lock.lock_sha256 == CALCULATOR_LOCK_SHA256
    assert SpecificationLock.model_validate_json(lock.model_dump_json()) == lock


def test_evaluation_config_and_operator_paths_are_locked_everywhere(tmp_path: Path) -> None:
    fixture = FixtureRepository(
        name="configured",
        files={
            "calculator.py": "x = 1\n",
            "tests/test_x.py": "def test_x():\n    assert True\n",
            "pyproject.toml": "[tool.pytest.ini_options]\n",
            "pkg/conftest.py": "",
            "site/extra.pth": "pkg\n",
            "data/expected.json": "{}\n",
            "README.md": "docs\n",
        },
    )
    source, sha = _repository(tmp_path, fixture)
    profile = calculator_profile()
    lock = capture_specification_lock(
        GitRunner(tmp_path / "git"),
        source,
        sha,
        profile,
        captured_at=NOW,
        additional_paths=["data"],
    )
    kinds = {item.path: item.kind for item in lock.entries}
    assert kinds == {
        "data/expected.json": SpecificationKind.OPERATOR,
        "pkg/conftest.py": SpecificationKind.EVALUATION_CONFIG,
        "pyproject.toml": SpecificationKind.EVALUATION_CONFIG,
        "site/extra.pth": SpecificationKind.EVALUATION_CONFIG,
        "tests/test_x.py": SpecificationKind.TEST,
    }
    assert classify_specification_path("calculator.py", profile) is None
    assert classify_specification_path("tests/conftest.py", profile) is SpecificationKind.TEST


def test_symlinked_specification_cannot_be_locked(tmp_path: Path) -> None:
    source, sha = _repository(tmp_path)
    git = GitRunner(tmp_path / "git")
    target = source / "tests" / "test_calculator.py"
    target.unlink()
    os.symlink("../calculator.py", target)
    location = ["-C", str(source), "-c", "core.autocrlf=false"]
    git.run([*location, "add", "--all", "--", "."])
    git.run(
        [*location, "commit", "--quiet", "--no-verify", "-m", "symlink"],
        environment_overrides={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@nexus.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@nexus.invalid",
        },
    )
    linked = git.run([*location, "rev-parse", "HEAD"]).stdout_text().strip()
    assert linked != sha
    with pytest.raises(SpecificationLockError, match="not a regular file"):
        capture_specification_lock(git, source, linked, calculator_profile(), captured_at=NOW)


def test_tree_helpers_see_symlinks_modes_and_exclude_caches(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_a.py").write_text("a\n", encoding="utf-8")
    (root / "code.py").write_text("code\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "code.cpython-312.pyc").write_bytes(b"\x00")
    (root / ".pytest_cache").mkdir()
    (root / ".pytest_cache" / "v").write_text("cache\n", encoding="utf-8")
    baseline = tree_sha256(root)
    assert [path for path, _mode, _digest in walk_tree(root)] == ["code.py", "tests/test_a.py"]
    # Caches never change the fingerprint; content, mode, and symlinks always do.
    (root / ".pytest_cache" / "w").write_text("more\n", encoding="utf-8")
    assert tree_sha256(root) == baseline
    os.chmod(root / "code.py", 0o755)
    executable = tree_sha256(root)
    assert executable != baseline
    (root / "tests" / "test_a.py").unlink()
    os.symlink("../code.py", root / "tests" / "test_a.py")
    linked = tree_sha256(root)
    assert linked != executable
    assert [mode for _path, mode, _digest in walk_tree(root) if _path == "tests/test_a.py"] == [
        "120000"
    ]
    state = inspect_file(root, "tests/test_a.py")
    assert state.exists and not state.regular and state.sha256 is None
    assert inspect_file(root, "missing.py").exists is False
    assert inspect_file(root, "code.py").mode == "100755"
    assert specification_files_in_tree(root, calculator_profile()) == {
        "tests/test_a.py": SpecificationKind.TEST
    }


def test_compare_tree_with_lock_reports_each_kind_of_drift(tmp_path: Path) -> None:
    source, sha = _repository(tmp_path)
    git = GitRunner(tmp_path / "git")
    lock = capture_specification_lock(git, source, sha, calculator_profile(), captured_at=NOW)
    assert compare_tree_with_lock(source, lock) == ([], [], [])
    test_file = source / "tests" / "test_calculator.py"
    test_file.write_text("changed\n", encoding="utf-8")
    assert compare_tree_with_lock(source, lock) == (["tests/test_calculator.py"], [], [])
    test_file.unlink()
    assert compare_tree_with_lock(source, lock) == ([], ["tests/test_calculator.py"], [])
    os.symlink("../calculator.py", test_file)
    assert compare_tree_with_lock(source, lock) == ([], [], ["tests/test_calculator.py"])


def test_lock_rejects_unsorted_or_duplicate_entries(tmp_path: Path) -> None:
    source, sha = _repository(tmp_path)
    lock = capture_specification_lock(
        GitRunner(tmp_path / "git"), source, sha, calculator_profile(), captured_at=NOW
    )
    duplicate = [lock.entries[0], lock.entries[0]]
    with pytest.raises(ValueError, match="unique and sorted"):
        lock.model_copy(update={"entries": duplicate}).model_validate(
            lock.model_copy(update={"entries": duplicate}).model_dump()
        )
