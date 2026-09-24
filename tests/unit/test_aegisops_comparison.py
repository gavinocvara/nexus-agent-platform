from pathlib import Path

import pytest

from nexus.evaluation.aegisops.benchmark_models import BenchmarkSummary
from nexus.evaluation.aegisops.comparison import compare_summaries

FIXTURES = Path(__file__).parents[1] / "fixtures" / "benchmarks"


def _load(name: str) -> BenchmarkSummary:
    return BenchmarkSummary.model_validate_json((FIXTURES / name).read_text(encoding="utf-8"))


def test_identical_comparison_has_zero_deltas() -> None:
    baseline = _load("baseline-summary.json")
    comparison = compare_summaries(baseline, baseline)
    assert comparison.comparison_type == "same_baseline"
    assert comparison.warnings == []
    assert all(delta.absolute_delta == 0 for delta in comparison.deltas.values())


def test_comparison_reports_improvements_regressions_and_mixed_tradeoffs() -> None:
    baseline = _load("baseline-summary.json")
    candidate = _load("candidate-summary.json")
    comparison = compare_summaries(baseline, candidate)
    assert comparison.deltas["exact_diagnosis_accuracy"].absolute_delta == pytest.approx(0.2)
    assert comparison.deltas["average_tool_calls"].regression_threshold_exceeded is True
    assert comparison.deltas["average_latency_ms"].absolute_delta == -100
    assert "Git SHAs differ" in comparison.warnings


def test_regression_thresholds_flag_quality_decrease() -> None:
    baseline = _load("candidate-summary.json")
    candidate = _load("baseline-summary.json")
    comparison = compare_summaries(baseline, candidate)
    assert comparison.deltas["exact_diagnosis_accuracy"].regression_threshold_exceeded
    assert comparison.deltas["unsupported_claim_rate"].regression_threshold_exceeded


def test_missing_metrics_failed_runs_and_cross_model_are_explicit() -> None:
    baseline = _load("baseline-summary.json")
    candidate = _load("candidate-summary.json")
    changed_aggregate = candidate.aggregate.model_copy(update={"average_total_tokens": None})
    changed_identity = candidate.identity.model_copy(
        update={
            "model": "different-model",
            "instruction_hash": "x" * 64,
            "tool_registry_hash": "y" * 64,
            "scenario_catalog_hash": "z" * 64,
        }
    )
    changed = candidate.model_copy(
        update={"aggregate": changed_aggregate, "identity": changed_identity}
    )
    comparison = compare_summaries(baseline, changed)
    assert comparison.comparison_type == "cross_model"
    assert comparison.deltas["average_total_tokens"].absolute_delta is None
    assert "Model identifiers differ; this is a cross-model comparison" in comparison.warnings
    assert "Investigator instruction hashes differ" in comparison.warnings
    assert "Diagnostic tool-registry hashes differ" in comparison.warnings
    assert "Scenario-catalog hashes differ" in comparison.warnings
    assert baseline.aggregate.completed_run_rate < 1


def test_incompatible_schema_is_not_compared() -> None:
    baseline = _load("baseline-summary.json")
    candidate = _load("candidate-summary.json").model_copy(update={"evaluation_schema_version": 2})
    comparison = compare_summaries(baseline, candidate)
    assert comparison.comparison_type == "incompatible"
    assert comparison.comparable is False
    assert all(delta.absolute_delta is None for delta in comparison.deltas.values())


def test_brain_candidate_remains_schema_compatible_and_explicit() -> None:
    baseline = _load("baseline-summary.json")
    candidate = _load("candidate-summary.json")
    candidate = candidate.model_copy(
        update={
            "identity": candidate.identity.model_copy(
                update={
                    "baseline_name": "aegisops-brain-v1",
                    "brain_enabled": True,
                    "brain_namespace": "aegisops.investigator",
                    "brain_schema_version": 1,
                    "brain_memory_sha256": "b" * 64,
                    "brain_read_only": True,
                    "brain_max_retrieved_memories": 3,
                    "brain_max_context_tokens": 800,
                }
            ),
            "aggregate": candidate.aggregate.model_copy(
                update={
                    "brain_retrieval_count": 4,
                    "brain_memory_hit_rate": 0.8,
                    "investigations_using_retrieved_memory": 4,
                }
            ),
        }
    )
    comparison = compare_summaries(baseline, candidate)
    assert comparison.comparable is True
    assert "Brain enablement differs" in comparison.warnings
    assert "Brain memory identities differ" in comparison.warnings
    assert comparison.deltas["brain_retrieval_count"].candidate == 4
    assert "completed_run_rate" in comparison.deltas
    assert "tool_budget_failure_rate" in comparison.deltas
