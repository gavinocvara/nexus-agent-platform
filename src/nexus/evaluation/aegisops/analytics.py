"""Deterministic aggregate, calibration, scenario, and tool-use analysis."""

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from math import ceil
from statistics import median
from uuid import UUID

from nexus.aegisops.models import DiagnosisStatus
from nexus.evaluation.aegisops.benchmark_models import (
    AggregateAnalysis,
    BaselineIdentity,
    BenchmarkRunRecord,
    BenchmarkRunStatus,
    BenchmarkSummary,
    ConfidenceBucket,
    ScenarioAnalysis,
    ToolUsageAnalysis,
)


def summarize(
    session_id: UUID,
    identity: BaselineIdentity,
    runs: list[BenchmarkRunRecord],
) -> BenchmarkSummary:
    return BenchmarkSummary(
        analysis_version=2,
        benchmark_session_id=session_id,
        generated_at=datetime.now(UTC),
        identity=identity,
        aggregate=_aggregate(runs),
        confidence_calibration=_confidence_buckets(runs),
        scenarios=_scenario_analysis(runs),
        tools=_tool_analysis(runs),
    )


def _rate(count: int, total: int) -> float:
    return count / total if total else 0.0


def _mean(values: Sequence[float | int]) -> float:
    return sum(values) / len(values) if values else 0.0


def _optional_total(values: list[int | None]) -> int | None:
    return (
        sum(value for value in values if value is not None)
        if all(value is not None for value in values)
        else None
    )


def _aggregate(runs: list[BenchmarkRunRecord]) -> AggregateAnalysis:
    total = len(runs)
    completed = sum(run.run_status is BenchmarkRunStatus.COMPLETED for run in runs)
    scored = [run.score for run in runs if run.score is not None]
    references = sum(score.evidence_reference_count for score in scored)
    valid_references = sum(score.valid_evidence_reference_count for score in scored)
    exact = sum(score.exact_diagnosis for score in scored)
    tool_calls = [len(run.tool_calls) for run in runs]
    latencies = [run.duration_ms for run in runs]
    end_to_end_latencies = [
        run.end_to_end_duration_ms for run in runs if run.end_to_end_duration_ms is not None
    ]
    input_tokens = _optional_total(
        [run.usage.input_tokens if run.usage is not None else None for run in runs]
    )
    output_tokens = _optional_total(
        [run.usage.output_tokens if run.usage is not None else None for run in runs]
    )
    total_tokens = _optional_total(
        [run.usage.total_tokens if run.usage is not None else None for run in runs]
    )
    sorted_calls = sorted(tool_calls)
    p95 = (
        float(sorted_calls[ceil(0.95 * len(sorted_calls)) - 1]) if len(sorted_calls) >= 20 else None
    )
    tool_call_total = sum(tool_calls)
    investigations_with_memory = sum(run.brain.retrieval_count > 0 for run in runs)
    brain_latencies = [run.brain.retrieval_latency_ms for run in runs if run.brain.enabled]
    return AggregateAnalysis(
        total_runs=total,
        completed_runs=completed,
        completed_run_rate=_rate(completed, total),
        component_accuracy=_rate(sum(score.component_correct for score in scored), total),
        failure_class_accuracy=_rate(sum(score.failure_class_correct for score in scored), total),
        exact_diagnosis_accuracy=_rate(exact, total),
        abstention_rate=_rate(sum(score.abstained for score in scored), total),
        genuine_abstention_rate=_rate(
            sum(
                run.run_status is BenchmarkRunStatus.COMPLETED
                and run.agent_diagnosis is not None
                and run.agent_diagnosis.status is not DiagnosisStatus.DIAGNOSED
                for run in runs
            ),
            total,
        ),
        confident_wrong_rate=_rate(
            sum(
                run.score is not None
                and not run.score.exact_diagnosis
                and not run.score.abstained
                and run.confidence is not None
                and run.confidence >= 0.8
                for run in runs
            ),
            total,
        ),
        invalid_output_rate=_rate(
            sum(run.run_status is BenchmarkRunStatus.INVALID_OUTPUT for run in runs), total
        ),
        backend_failure_rate=_rate(
            sum(
                run.run_status is BenchmarkRunStatus.BACKEND_FAILURE or run.backend_failures > 0
                for run in runs
            ),
            total,
        ),
        tool_budget_failure_rate=_rate(
            sum(run.run_status is BenchmarkRunStatus.TOOL_BUDGET_EXCEEDED for run in runs),
            total,
        ),
        timeout_rate=_rate(
            sum(run.run_status is BenchmarkRunStatus.TIMEOUT for run in runs), total
        ),
        brain_failure_rate=_rate(
            sum(run.run_status is BenchmarkRunStatus.BRAIN_FAILURE for run in runs), total
        ),
        valid_evidence_reference_rate=_rate(valid_references, references),
        unsupported_claim_rate=_rate(sum(run.unsupported_claims > 0 for run in runs), total),
        unsafe_attempt_rate=_rate(
            sum(run.forbidden_capability_attempts > 0 for run in runs), total
        ),
        runs_with_unsupported_claims=sum(run.unsupported_claims > 0 for run in runs),
        runs_with_conflicting_evidence=sum(run.conflicting_evidence_references > 0 for run in runs),
        average_tool_calls=_mean(tool_calls),
        median_tool_calls=float(median(tool_calls)) if tool_calls else 0,
        p95_tool_calls=p95,
        average_latency_ms=_mean(latencies),
        median_latency_ms=float(median(latencies)) if latencies else 0,
        average_end_to_end_latency_ms=(
            _mean(end_to_end_latencies) if end_to_end_latencies else None
        ),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        average_total_tokens=(total_tokens / total if total_tokens is not None and total else None),
        exact_diagnoses_per_tool_call=(exact / tool_call_total if tool_call_total else None),
        exact_diagnoses_per_1000_tokens=(
            exact / (total_tokens / 1000) if total_tokens is not None and total_tokens > 0 else None
        ),
        brain_retrieval_count=sum(run.brain.retrieval_count for run in runs),
        brain_memory_hit_rate=_rate(investigations_with_memory, total),
        investigations_using_retrieved_memory=investigations_with_memory,
        brain_memory_write_count=sum(run.brain.memory_write_count for run in runs),
        average_brain_retrieval_latency_ms=_mean(brain_latencies),
        brain_attributable_input_tokens=sum(
            run.brain.brain_attributable_input_tokens for run in runs
        ),
    )


def _confidence_buckets(runs: list[BenchmarkRunRecord]) -> list[ConfidenceBucket]:
    boundaries = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
    buckets: list[ConfidenceBucket] = []
    for index, (lower, upper) in enumerate(boundaries):
        members = [
            run
            for run in runs
            if run.confidence is not None
            and run.score is not None
            and lower <= run.confidence
            and (run.confidence < upper or (index == len(boundaries) - 1 and run.confidence <= 1))
        ]
        buckets.append(
            ConfidenceBucket(
                lower_bound=lower,
                upper_bound=upper,
                count=len(members),
                accuracy=(
                    _rate(
                        sum(run.score.exact_diagnosis for run in members if run.score is not None),
                        len(members),
                    )
                    if members
                    else None
                ),
                average_confidence=(
                    _mean([run.confidence for run in members if run.confidence is not None])
                    if members
                    else None
                ),
                small_sample=len(members) < 30,
            )
        )
    return buckets


def _scenario_analysis(runs: list[BenchmarkRunRecord]) -> list[ScenarioAnalysis]:
    analyses: list[ScenarioAnalysis] = []
    for scenario_id in sorted({run.scenario_id for run in runs}):
        members = [run for run in runs if run.scenario_id == scenario_id]
        scores = [run.score for run in members if run.score is not None]
        confidences = [run.confidence for run in members if run.confidence is not None]
        token_values = [
            run.usage.total_tokens
            for run in members
            if run.usage is not None and run.usage.total_tokens is not None
        ]
        wrong_components: Counter[str] = Counter()
        wrong_failures: Counter[str] = Counter()
        for run in members:
            hypothesis = (
                run.agent_diagnosis.primary_hypothesis if run.agent_diagnosis is not None else None
            )
            if hypothesis is not None and run.score is not None:
                if not run.score.component_correct:
                    wrong_components[hypothesis.component.value] += 1
                if not run.score.failure_class_correct:
                    wrong_failures[hypothesis.failure_class.value] += 1
        analyses.append(
            ScenarioAnalysis(
                scenario_id=scenario_id,
                failure_class=members[0].failure_class,
                success_count=sum(score.exact_diagnosis for score in scores),
                total_runs=len(members),
                component_accuracy=_rate(
                    sum(score.component_correct for score in scores), len(members)
                ),
                failure_class_accuracy=_rate(
                    sum(score.failure_class_correct for score in scores), len(members)
                ),
                exact_accuracy=_rate(sum(score.exact_diagnosis for score in scores), len(members)),
                average_confidence=_mean(confidences) if confidences else None,
                average_tool_calls=_mean([len(run.tool_calls) for run in members]),
                average_latency_ms=_mean([run.duration_ms for run in members]),
                average_total_tokens=(
                    _mean(token_values) if len(token_values) == len(members) else None
                ),
                common_wrong_component=(
                    wrong_components.most_common(1)[0][0] if wrong_components else None
                ),
                common_wrong_failure_class=(
                    wrong_failures.most_common(1)[0][0] if wrong_failures else None
                ),
                abstentions=sum(score.abstained for score in scores),
                backend_failures=sum(run.backend_failures > 0 for run in members),
            )
        )
    return analyses


def _tool_analysis(runs: list[BenchmarkRunRecord]) -> list[ToolUsageAnalysis]:
    analyses: list[ToolUsageAnalysis] = []
    names = sorted({call.tool for run in runs for call in run.tool_calls})
    for name in names:
        calls = [call for run in runs for call in run.tool_calls if call.tool == name]
        runs_using = sum(any(call.tool == name for call in run.tool_calls) for run in runs)
        analyses.append(
            ToolUsageAnalysis(
                tool=name,
                calls=len(calls),
                run_usage_rate=_rate(runs_using, len(runs)),
                average_position=_mean([call.position for call in calls]),
                backend_success_rate=_rate(sum(call.success for call in calls), len(calls)),
            )
        )
    return analyses
