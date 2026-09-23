"""Developer CLI for the exact Phase 4 diagnostic service layer."""

import argparse
from collections.abc import Sequence

from nexus.diagnostics.models import (
    ApplicationService,
    CorrelationInput,
    DependencySummaryInput,
    DiagnosticResult,
    DiagnosticService,
    DiagnosticWindow,
    LogLevel,
    LogSearchInput,
    RecentErrorsInput,
    RequestSummaryInput,
    ServiceInput,
    TraceInput,
)
from nexus.diagnostics.service import DiagnosticServiceLayer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NEXUS read-only diagnostic tools")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("services")

    health = commands.add_parser("health")
    health.add_argument("service", choices=[item.value for item in DiagnosticService])
    commands.add_parser("system-health")

    requests = commands.add_parser("requests")
    requests.add_argument("service", choices=[item.value for item in ApplicationService])
    requests.add_argument(
        "--window", choices=[item.value for item in DiagnosticWindow], default="5m"
    )

    dependency = commands.add_parser("dependency")
    dependency.add_argument("service", choices=[item.value for item in ApplicationService])
    dependency.add_argument("dependency", choices=[item.value for item in DiagnosticService])
    dependency.add_argument(
        "--window", choices=[item.value for item in DiagnosticWindow], default="5m"
    )

    commands.add_parser("database-health")

    logs = commands.add_parser("logs")
    logs.add_argument("service", choices=[item.value for item in ApplicationService])
    logs.add_argument("--window", choices=[item.value for item in DiagnosticWindow], default="5m")
    logs.add_argument("--level", choices=[item.value for item in LogLevel])
    logs.add_argument("--correlation-id")
    logs.add_argument("--status-code", type=int)
    logs.add_argument("--limit", type=int, default=20)

    errors = commands.add_parser("errors")
    errors.add_argument("service", choices=[item.value for item in ApplicationService])
    errors.add_argument("--window", choices=[item.value for item in DiagnosticWindow], default="5m")
    errors.add_argument("--limit", type=int, default=20)

    request = commands.add_parser("request")
    request.add_argument("correlation_id")
    find_traces = commands.add_parser("find-traces")
    find_traces.add_argument("correlation_id")
    trace = commands.add_parser("trace")
    trace.add_argument("trace_id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one diagnostic operation and serialize its typed result as JSON."""

    args = _parser().parse_args(argv)
    result: DiagnosticResult
    with DiagnosticServiceLayer() as diagnostics:
        match args.command:
            case "services":
                result = diagnostics.list_services()
            case "health":
                result = diagnostics.get_service_health(ServiceInput(service=args.service))
            case "system-health":
                result = diagnostics.get_system_health()
            case "requests":
                result = diagnostics.get_request_summary(
                    RequestSummaryInput(service=args.service, window=args.window)
                )
            case "dependency":
                result = diagnostics.get_dependency_summary(
                    DependencySummaryInput(
                        service=args.service,
                        dependency=args.dependency,
                        window=args.window,
                    )
                )
            case "database-health":
                result = diagnostics.get_database_health()
            case "logs":
                result = diagnostics.search_logs(
                    LogSearchInput(
                        service=args.service,
                        window=args.window,
                        level=args.level,
                        correlation_id=args.correlation_id,
                        status_code=args.status_code,
                        limit=args.limit,
                    )
                )
            case "errors":
                result = diagnostics.get_recent_errors(
                    RecentErrorsInput(
                        service=args.service,
                        window=args.window,
                        limit=args.limit,
                    )
                )
            case "request":
                result = diagnostics.get_request_evidence(
                    CorrelationInput(correlation_id=args.correlation_id)
                )
            case "find-traces":
                result = diagnostics.find_traces(
                    CorrelationInput(correlation_id=args.correlation_id)
                )
            case "trace":
                result = diagnostics.get_trace(TraceInput(trace_id=args.trace_id))
            case _:
                raise AssertionError("Unhandled diagnostic command")
    print(result.model_dump_json(indent=2))
    return 0 if result.success else 2


if __name__ == "__main__":
    raise SystemExit(main())
