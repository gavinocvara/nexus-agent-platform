"""Capture and verify the pristine specification reference from Git objects.

The lock is read from the object store of the exact source commit, never from a working
tree, so a dirty checkout or an in-flight candidate cannot influence it. Symbolic links
and submodules inside the specification set fail closed: they cannot be attested as
regular test files.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from nexus.patchforge.canonical import canonical_sha256
from nexus.patchforge.gateway import path_matches
from nexus.patchforge.models import RepositoryPath
from nexus.patchforge.policy import RepositoryProfile
from nexus.patchforge.workspace import TOOL_CACHE_DIRECTORIES, GitCommandError, GitRunner
from nexus.sentinelqa.models import (
    SpecificationEntry,
    SpecificationKind,
    SpecificationLock,
    is_evaluation_config_path,
)

MAX_SPECIFICATION_ENTRIES = 5000
MAX_SPECIFICATION_BYTES = 50_000_000
MAX_TREE_FILES = 200_000
_IGNORED_DIRECTORIES = TOOL_CACHE_DIRECTORIES | {"__pycache__", ".git"}
_REPOSITORY_PATH: TypeAdapter[RepositoryPath] = TypeAdapter(RepositoryPath)


class SpecificationLockError(RuntimeError):
    """The specification reference cannot be captured or trusted."""


@dataclass(frozen=True, slots=True)
class TreeObject:
    mode: str
    object_type: str
    object_name: str
    size: int | None
    path: str


def list_tree(git: GitRunner, repository: Path, revision: str) -> list[TreeObject]:
    """Every object in the commit's tree with mode, type, name, size, and path."""

    output = git.run(
        ["-C", str(repository), "ls-tree", "-r", "-l", "-z", revision, "--"],
        output_limit=50_000_000,
    ).stdout
    objects: list[TreeObject] = []
    for record in output.split(b"\0"):
        if not record:
            continue
        try:
            meta, path = record.split(b"\t", 1)
            mode, object_type, object_name, size = meta.decode("ascii").split()
            decoded = path.decode("utf-8", errors="strict")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SpecificationLockError("Git returned a malformed tree listing") from exc
        objects.append(
            TreeObject(
                mode=mode,
                object_type=object_type,
                object_name=object_name,
                size=None if size == "-" else int(size),
                path=decoded,
            )
        )
    return objects


def classify_specification_path(
    path: str, profile: RepositoryProfile, additional_paths: Sequence[str] = ()
) -> SpecificationKind | None:
    """Which part of the specification a path belongs to, or ``None`` if it is code."""

    if path_matches(path, profile.test_path_prefixes):
        return SpecificationKind.TEST
    if is_evaluation_config_path(path):
        return SpecificationKind.EVALUATION_CONFIG
    if path_matches(path, additional_paths):
        return SpecificationKind.OPERATOR
    return None


def capture_specification_lock(
    git: GitRunner,
    repository: Path,
    source_sha: str,
    profile: RepositoryProfile,
    *,
    captured_at: datetime,
    additional_paths: Sequence[str] = (),
) -> SpecificationLock:
    """Lock the specification set of ``source_sha`` from the repository's Git objects."""

    if captured_at.utcoffset() is None:
        raise SpecificationLockError("Lock capture time must be timezone-aware")
    try:
        head = (
            git.run(["-C", str(repository), "rev-parse", "--verify", f"{source_sha}^{{commit}}"])
            .stdout_text()
            .strip()
        )
    except GitCommandError as exc:
        raise SpecificationLockError("Source commit is not present in the repository") from exc
    if head != source_sha:
        raise SpecificationLockError("Source commit is not present in the repository")
    entries: list[SpecificationEntry] = []
    total_bytes = 0
    for item in list_tree(git, repository, source_sha):
        kind = classify_specification_path(item.path, profile, additional_paths)
        if kind is None:
            continue
        try:
            path = _REPOSITORY_PATH.validate_python(item.path)
        except ValidationError as exc:
            raise SpecificationLockError(
                f"Specification path is not a portable repository path: {item.path}"
            ) from exc
        if item.object_type != "blob" or item.mode not in {"100644", "100755"}:
            raise SpecificationLockError(
                f"Specification entry is not a regular file: {item.path} ({item.mode})"
            )
        content = git.run(
            ["-C", str(repository), "cat-file", "blob", item.object_name],
            output_limit=MAX_SPECIFICATION_BYTES,
        ).stdout
        total_bytes += len(content)
        if total_bytes > MAX_SPECIFICATION_BYTES:
            raise SpecificationLockError("Specification set exceeds the size bound")
        entries.append(
            SpecificationEntry(
                path=path,
                kind=kind,
                mode="100755" if item.mode == "100755" else "100644",
                sha256=sha256(content).hexdigest(),
                size_bytes=len(content),
            )
        )
        if len(entries) > MAX_SPECIFICATION_ENTRIES:
            raise SpecificationLockError("Specification set exceeds the entry bound")
    entries.sort(key=lambda entry: entry.path)
    return SpecificationLock(
        repository_url=profile.repository_url,
        source_sha=source_sha,
        profile_id=profile.profile_id,
        profile_sha256=canonical_sha256(profile),
        test_path_prefixes=list(profile.test_path_prefixes),
        entries=entries,
        captured_at=captured_at,
    )


@dataclass(frozen=True, slots=True)
class FileState:
    """What a path looks like on disk, as SentinelQA sees it."""

    exists: bool
    regular: bool
    executable: bool
    sha256: str | None

    @property
    def mode(self) -> str | None:
        if not self.regular:
            return None
        return "100755" if self.executable else "100644"


def inspect_file(root: Path, relative: str) -> FileState:
    """Inspect a path without following symbolic links anywhere along it."""

    current = root
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink():
            return FileState(exists=True, regular=False, executable=False, sha256=None)
    if not current.exists():
        return FileState(exists=False, regular=False, executable=False, sha256=None)
    if not current.is_file():
        return FileState(exists=True, regular=False, executable=False, sha256=None)
    content = current.read_bytes()
    executable = bool(current.stat().st_mode & 0o100)
    return FileState(
        exists=True, regular=True, executable=executable, sha256=sha256(content).hexdigest()
    )


def walk_tree(root: Path) -> list[tuple[str, str, str]]:
    """Every file below ``root`` as ``(path, mode, sha256)``, sorted, caches excluded.

    Symbolic links are recorded with mode ``120000`` and the digest of their target text,
    so a link replacing a file always changes the fingerprint.
    """

    records: list[tuple[str, str, str]] = []
    for directory, subdirectories, files in os.walk(root, followlinks=False):
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if name not in _IGNORED_DIRECTORIES and not (Path(directory) / name).is_symlink()
        )
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")):
                continue
            full = Path(directory) / name
            relative = full.relative_to(root).as_posix()
            if full.is_symlink():
                target = os.readlink(full).encode("utf-8", errors="surrogateescape")
                records.append((relative, "120000", sha256(target).hexdigest()))
                continue
            if not full.is_file():
                continue
            mode = "100755" if full.stat().st_mode & 0o100 else "100644"
            records.append((relative, mode, sha256(full.read_bytes()).hexdigest()))
            if len(records) > MAX_TREE_FILES:
                raise SpecificationLockError("Tree exceeds the file bound")
        # Symbolic links to directories are listed by os.walk under subdirectories only
        # when followlinks is set; record them so a directory swap is visible too.
        for name in sorted(os.listdir(directory)):
            full = Path(directory) / name
            if full.is_symlink() and full.is_dir():
                target = os.readlink(full).encode("utf-8", errors="surrogateescape")
                records.append(
                    (full.relative_to(root).as_posix(), "120000", sha256(target).hexdigest())
                )
    records.sort()
    return records


def tree_sha256(root: Path) -> str:
    """Content fingerprint of a tree; identical trees hash identically on every platform."""

    return canonical_sha256(
        {
            "files": [
                {"path": path, "mode": mode, "sha256": digest}
                for path, mode, digest in walk_tree(root)
            ]
        }
    )


def specification_files_in_tree(
    root: Path, profile: RepositoryProfile, additional_paths: Sequence[str] = ()
) -> dict[str, SpecificationKind]:
    """Every specification-classified path present in a tree, including untracked ones."""

    found: dict[str, SpecificationKind] = {}
    for path, _mode, _digest in walk_tree(root):
        kind = classify_specification_path(path, profile, additional_paths)
        if kind is not None:
            found[path] = kind
    return found


def compare_tree_with_lock(
    root: Path, lock: SpecificationLock
) -> tuple[list[str], list[str], list[str]]:
    """Return ``(modified, deleted, not_regular)`` locked paths as found in ``root``."""

    modified: list[str] = []
    deleted: list[str] = []
    not_regular: list[str] = []
    for entry in lock.entries:
        state = inspect_file(root, entry.path)
        if not state.exists:
            deleted.append(entry.path)
        elif not state.regular:
            not_regular.append(entry.path)
        elif state.sha256 != entry.sha256 or state.mode != entry.mode:
            modified.append(entry.path)
    return modified, deleted, not_regular


def paths_under(paths: Iterable[str], prefixes: Sequence[str]) -> list[str]:
    return sorted(path for path in paths if path_matches(path, prefixes))


__all__ = [
    "MAX_SPECIFICATION_BYTES",
    "MAX_SPECIFICATION_ENTRIES",
    "MAX_TREE_FILES",
    "FileState",
    "SpecificationLockError",
    "TreeObject",
    "capture_specification_lock",
    "classify_specification_path",
    "compare_tree_with_lock",
    "inspect_file",
    "list_tree",
    "paths_under",
    "specification_files_in_tree",
    "tree_sha256",
    "walk_tree",
]
