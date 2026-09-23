import json
import logging

from nexus.web import JsonFormatter


def test_json_formatter_includes_request_metadata() -> None:
    record = logging.LogRecord(
        name="users",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request completed",
        args=(),
        exc_info=None,
    )
    record.service = "users"
    record.method = "GET"
    record.path = "/users/1"
    record.status_code = 200

    payload = json.loads(JsonFormatter().format(record))

    assert payload["service"] == "users"
    assert payload["method"] == "GET"
    assert payload["path"] == "/users/1"
    assert payload["status_code"] == 200
