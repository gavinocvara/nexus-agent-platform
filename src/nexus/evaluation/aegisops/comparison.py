"""Factual benchmark comparison with explicit compatibility warnings."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from nexus.evaluation.aegisops.benchmark_models import (
    BenchmarkComparison,
    BenchmarkSummary,
    MetricDelta,
    RegressionThresholds,
)


def compare_summaries(
    baseline: BenchmarkSummary,
    candidate: BenchmarkSummary,
    thresholds: RegressionThresholds | None = None,
) -> BenchmarkComparison:
    resolved = thresholds or RegressionThresholds()
    warnings: list[str] = []
    incompatible = (
        baseline.benchmark_schema_version != candidate.benchmark_schema_version
        or baseline.evaluation_schema_version != candidate.evaluation_schema_version
    )
    left = baseline.identity
    right = candidate.identity
    if left.model != right.model:
        warnings.append("Model identifiers differ; this is a cross-model comparison")
    if left.git_sha != right.git_sha:
        warnings.append("Git SHAs differ")
    if left.instruction_hash != right.instruction_hash:
        warnings.append("Investigator instruction hashes differ")
    if left.tool_registry_hash != right.tool_registry_hash:
        warnings.append("Diagnostic tool-registry hashes differ")
    if left.diagnosis_schema_hash != right.diagnosis_schema_hash:
        warnings.append("Diagnosis-schema hashes differ")
    if left.scenario_catalog_hash != right.scenario_catalog_hash:
        warnings.append("Scenario-catalog hashes differ")
    comparison_type: Literal["same_baseline", "cross_model", "incompatible"]
    if incompatible:
        warnings.append("Benchmark or evaluation schema versions are incompatible")
        comparison_type = "incompatible"
    elif left.model != right.model:
        comparison_type = "cross_model"
    else:
        comparison_type = "same_baseline"

    a = baseline.aggregate
    b = candidate.aggregate
    metrics: dict[str, tuple[float | int | None, float | int | None]] = {
        "exact_diagnosis_accuracy": (a.exact_diagnosis_accuracy, b.exact_diagnosis_accuracy),
        "component_accuracy": (a.component_accuracy, b.component_accuracy),
        "failure_class_accuracy": (a.failure_class_accuracy, b.failure_class_accuracy),
        "average_tool_calls": (a.average_tool_calls, b.average_tool_calls),
        "average_latency_ms": (a.average_latency_ms, b.average_latency_ms),
        "average_total_tokens": (a.average_total_tokens, b.average_total_tokens),
        "valid_evidence_reference_rate": (
            a.valid_evidence_reference_rate,
            b.valid_evidence_reference_rate,
        ),
        "unsupported_claim_rate": (a.unsupported_claim_rate, b.unsupported_claim_rate),
        "abstention_rate": (a.abstention_rate, b.abstention_rate),
        "timeout_rate": (a.timeout_rate, b.timeout_rate),
        "unsafe_attempt_rate": (a.unsafe_attempt_rate, b.unsafe_attempt_rate),
    }
    deltas = {
        name: _delta(name, before, after, resolved, incompatible)
        for name, (before, after) in metrics.items()
    }
    return BenchmarkComparison(
        created_at=datetime.now(UTC),
        comparison_type=comparison_type,
        comparable=not incompatible,
        baseline_session_id=_session_id(baseline),
        candidate_session_id=_session_id(candidate),
        warnings=warnings,
        thresholds=resolved,
        deltas=deltas,
    )


def _session_id(summary: BenchmarkSummary) -> UUID:
    return summary.benchmark_session_id


def _delta(
    name: str,
    baseline: float | int | None,
    candidate: float | int | None,
    thresholds: RegressionThresholds,
    incompatible: bool,
) -> MetricDelta:
    if baseline is None or candidate is None or incompatible:
        return MetricDelta(
            baseline=baseline,
            candidate=candidate,
            absolute_delta=None,
            relative_delta=None,
        )
    absolute = float(candidate) - float(baseline)
    relative = absolute / float(baseline) if baseline != 0 else None
    exceeded = False
    if name == "exact_diagnosis_accuracy":
        exceeded = absolute < -thresholds.exact_accuracy_decrease
    elif name == "unsupported_claim_rate":
        exceeded = absolute > thresholds.unsupported_claim_rate_increase
    elif name == "unsafe_attempt_rate":
        exceeded = absolute > thresholds.unsafe_attempt_rate_increase
    elif name == "average_tool_calls" and relative is not None:
        exceeded = relative > thresholds.average_tool_calls_increase_fraction
    elif name == "average_latency_ms" and relative is not None:
        exceeded = relative > thresholds.average_latency_increase_fraction
    return MetricDelta(
        baseline=baseline,
        candidate=candidate,
        absolute_delta=absolute,
        relative_delta=relative,
        regression_threshold_exceeded=exceeded,
    )
