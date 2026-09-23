"""Shared HTTP request context, logging, and error handling."""

import json
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

CORRELATION_HEADER = "X-Correlation-ID"
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="unavailable")


def get_correlation_id() -> str:
    """Return the correlation ID for the active request."""

    return _correlation_id.get()


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "service": getattr(record, "service", record.name),
            "message": record.getMessage(),
            "correlation_id": get_correlation_id(),
        }
        return json.dumps(payload, separators=(",", ":"))


def configure_logging(level: str) -> None:
    """Configure process logging with the shared JSON convention."""

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level.upper())


class ServiceError(Exception):
    """Expected service failure translated into a stable HTTP error."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Establish correlation context and log one completion event per request."""

    def __init__(self, app: Any, service: str) -> None:
        super().__init__(app)
        self.service = service
        self.logger = logging.getLogger(service)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied_id = request.headers.get(CORRELATION_HEADER, "").strip()
        correlation_id = supplied_id[:128] if supplied_id else str(uuid4())
        token: Token[str] = _correlation_id.set(correlation_id)
        try:
            response = await call_next(request)
            response.headers[CORRELATION_HEADER] = correlation_id
            self.logger.info(
                "request completed",
                extra={
                    "service": self.service,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                },
            )
            return response
        finally:
            _correlation_id.reset(token)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "correlation_id": get_correlation_id(),
            }
        },
    )


def install_service_foundation(app: FastAPI, service: str, log_level: str) -> None:
    """Install shared logging, correlation, and error behavior on an app."""

    configure_logging(log_level)
    app.add_middleware(RequestContextMiddleware, service=service)

    @app.exception_handler(ServiceError)
    async def service_error_handler(_request: Request, exc: ServiceError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        return _error_response(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(422, "invalid_request", "Request validation failed")
