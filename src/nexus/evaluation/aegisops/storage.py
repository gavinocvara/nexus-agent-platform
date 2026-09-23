"""Atomic, resume-safe local persistence for benchmark sessions."""

import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from nexus.evaluation.aegisops.benchmark_models import (
    BenchmarkManifest,
    BenchmarkMode,
    BenchmarkRunRecord,
    BenchmarkSessionStatus,
    BenchmarkSummary,
    LockedBaselineManifest,
)


class BenchmarkStorageError(RuntimeError):
    """Persisted benchmark data is missing, incompatible, or unsafe to overwrite."""


class BenchmarkStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def session_directory(self, session_id: UUID) -> Path:
        return self.root / str(session_id)

    def create_session(self, manifest: BenchmarkManifest) -> Path:
        directory = self.session_directory(manifest.benchmark_session_id)
        if directory.exists():
            raise BenchmarkStorageError(f"Benchmark session already exists: {directory}")
        (directory / "runs").mkdir(parents=True)
        self.write_manifest(manifest)
        return directory

    def write_manifest(self, manifest: BenchmarkManifest) -> Path:
        path = self.session_directory(manifest.benchmark_session_id) / "manifest.json"
        _atomic_write(path, manifest.model_dump_json(indent=2))
        return path

    def load_manifest(self, session: str | Path | UUID) -> BenchmarkManifest:
        path = self._resolve_session(session) / "manifest.json"
        try:
            return BenchmarkManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BenchmarkStorageError(f"Invalid benchmark manifest: {path}") from exc

    def write_run(self, record: BenchmarkRunRecord) -> Path:
        path = (
            self.session_directory(record.benchmark_session_id)
            / "runs"
            / f"run-{record.run_index:04d}.json"
        )
        _atomic_write(path, record.model_dump_json(indent=2))
        return path

    def load_runs(self, session: str | Path | UUID) -> list[BenchmarkRunRecord]:
        directory = self._resolve_session(session) / "runs"
        records: list[BenchmarkRunRecord] = []
        for path in sorted(directory.glob("run-*.json")):
            try:
                records.append(
                    BenchmarkRunRecord.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError) as exc:
                raise BenchmarkStorageError(f"Invalid benchmark run: {path}") from exc
        return records

    def write_summary(self, summary: BenchmarkSummary) -> Path:
        path = self.session_directory(summary.benchmark_session_id) / "summary.json"
        _atomic_write(path, summary.model_dump_json(indent=2))
        return path

    def load_summary(self, session: str | Path | UUID) -> BenchmarkSummary:
        path = self._resolve_session(session) / "summary.json"
        try:
            return BenchmarkSummary.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BenchmarkStorageError(f"Invalid benchmark summary: {path}") from exc

    def write_comparison(self, name: str, payload: str) -> Path:
        path = self.root / "comparisons" / f"{name}.json"
        _atomic_write(path, payload)
        return path

    def lock_baseline(self, session: str | Path | UUID, name: str) -> Path:
        manifest = self.load_manifest(session)
        if manifest.status is not BenchmarkSessionStatus.COMPLETED:
            raise BenchmarkStorageError("Only a completed benchmark session can be locked")
        if manifest.mode is not BenchmarkMode.BASELINE:
            raise BenchmarkStorageError("Only a repeated baseline session can be locked")
        if not manifest.identity.reproducible:
            raise BenchmarkStorageError("A dirty-tree benchmark cannot be locked as a baseline")
        if len(manifest.completed_run_indexes) != manifest.total_planned_runs:
            raise BenchmarkStorageError("Benchmark session does not contain every planned run")
        summary = self.load_summary(session)
        runs = self.load_runs(session)
        expected_indexes = list(range(1, manifest.total_planned_runs + 1))
        if summary.benchmark_session_id != manifest.benchmark_session_id:
            raise BenchmarkStorageError("Benchmark summary belongs to a different session")
        if summary.identity != manifest.identity:
            raise BenchmarkStorageError("Benchmark summary identity differs from the manifest")
        if summary.aggregate.total_runs != manifest.total_planned_runs:
            raise BenchmarkStorageError("Benchmark summary does not contain every planned run")
        if [record.run_index for record in runs] != expected_indexes:
            raise BenchmarkStorageError("Benchmark run indexes are incomplete or non-contiguous")
        if any(
            record.benchmark_session_id != manifest.benchmark_session_id
            or not record.recovery_verified
            for record in runs
        ):
            raise BenchmarkStorageError("Benchmark runs failed identity or recovery validation")
        summary_path = self._resolve_session(session) / "summary.json"
        digest = sha256(summary_path.read_bytes()).hexdigest()
        locked = LockedBaselineManifest(
            baseline_name=name,
            benchmark_session_id=manifest.benchmark_session_id,
            git_sha=manifest.identity.git_sha,
            model=manifest.identity.model,
            accepted_at=datetime.now(UTC),
            run_count=summary.aggregate.total_runs,
            aggregate_result_path=str(summary_path.resolve()),
            aggregate_result_sha256=digest,
        )
        path = self.root / "baselines" / f"{name}.json"
        if path.exists():
            raise BenchmarkStorageError(f"Baseline manifest already exists: {path}")
        _atomic_write(path, locked.model_dump_json(indent=2))
        return path

    def _resolve_session(self, session: str | Path | UUID) -> Path:
        value = Path(str(session))
        if value.is_dir():
            return value
        candidate = self.root / str(session)
        if candidate.is_dir():
            return candidate
        raise BenchmarkStorageError(f"Benchmark session not found: {session}")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
