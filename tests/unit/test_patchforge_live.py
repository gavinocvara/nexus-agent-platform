"""Live PatchForge boundary: authorization, preflight, SDK mapping, and one-task run."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nexus.patchforge import live
from nexus.patchforge.benchmark_corpus import default_corpus, reference_engine
from nexus.patchforge.e2e_catalog import default_catalog
from nexus.patchforge.engine import ModelMessage, ModelRequest, ModelResponse
from nexus.patchforge.models import ToolName
from nexus.patchforge.runtime import RuntimeToolAction

FAKE_KEY = "sk-test-not-a-real-key-000000000000"


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY", "NEXUS_PATCHFORGE_LIVE_ENABLED"):
        monkeypatch.delenv(name, raising=False)


def test_preflight_reports_readiness_without_revealing_the_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert live.main(["preflight"]) == 1
    assert "api_key_present=False" in capsys.readouterr().out
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("NEXUS_PATCHFORGE_LIVE_ENABLED", "true")
    assert live.main(["preflight"]) == 0
    output = capsys.readouterr()
    assert "ready=True" in output.out
    assert FAKE_KEY not in output.out + output.err


@pytest.mark.parametrize(
    ("enabled", "key", "confirmed", "message"),
    [
        (True, True, False, "--confirm-live"),
        (False, True, True, "NEXUS_PATCHFORGE_LIVE_ENABLED"),
        (True, False, True, "OPENAI_API_KEY"),
    ],
)
def test_live_runs_require_every_authorization(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, key: bool, confirmed: bool, message: str
) -> None:
    if enabled:
        monkeypatch.setenv("NEXUS_PATCHFORGE_LIVE_ENABLED", "true")
    if key:
        monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    with pytest.raises(live.LiveAuthorizationError, match=message):
        live.require_authorization(live.LiveSettings(), confirmed=confirmed)


def test_cli_refuses_an_unconfirmed_run_without_building_a_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NEXUS_PATCHFORGE_LIVE_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)

    def forbidden(settings: live.LiveSettings) -> Any:
        raise AssertionError("no client may be built without confirmation")

    code = live.main(
        ["run", "--task", "subtract_adds", "--output", str(tmp_path)], client_factory=forbidden
    )
    assert code == 2


def test_openai_client_requests_a_strict_single_action_schema() -> None:
    captured: dict[str, Any] = {}

    def create(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(
            output_text='{"tool": "list_tree", "arguments_json": "{}"}',
            usage=SimpleNamespace(input_tokens=12, output_tokens=7),
        )

    client = live.OpenAIResponsesClient(SimpleNamespace(responses=SimpleNamespace(create=create)))
    request = ModelRequest(
        model="fixture-model",
        messages=[ModelMessage(role="system", content="s"), ModelMessage(role="user", content="u")],
        max_output_tokens=256,
        tool_names=[ToolName.LIST_TREE, ToolName.ADVANCE_PHASE],
    )
    response = client.complete(request)

    assert response == ModelResponse(
        text='{"tool": "list_tree", "arguments_json": "{}"}', input_tokens=12, output_tokens=7
    )
    fmt = captured["text"]["format"]
    assert fmt["strict"] is True
    assert fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["properties"]["tool"]["enum"] == ["list_tree", "advance_phase"]
    assert captured["model"] == "fixture-model" and captured["max_output_tokens"] == 256
    assert [item["role"] for item in captured["input"]] == ["system", "user"]


class _ScriptedModel:
    def __init__(self, actions: list[RuntimeToolAction]) -> None:
        self.replies = [
            json.dumps({"tool": a.tool_name.value, "arguments_json": a.arguments.model_dump_json()})
            for a in actions
        ]

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text=self.replies.pop(0))


def test_confirmed_run_executes_one_task_and_writes_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NEXUS_PATCHFORGE_LIVE_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    task = default_corpus()[0]
    actions = [s for s in reference_engine(task) if isinstance(s, RuntimeToolAction)]

    code = live.main(
        ["run", "--task", task.name, "--confirm-live", "--output", str(tmp_path)],
        client_factory=lambda settings: _ScriptedModel(actions),
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "resolved=True" in output and "invariant_violations=0" in output
    assert FAKE_KEY not in output
    artifact = json.loads((tmp_path / f"{task.name}.json").read_text(encoding="utf-8"))
    assert artifact["score"]["resolved"] is True
    assert artifact["result"]["identity"]["engine_kind"] == "model"
    assert len(artifact["engine_calls"]) == len(actions)
    assert FAKE_KEY not in json.dumps(artifact)


def test_deterministic_catalog_never_declares_a_model_engine() -> None:
    assert {scenario.engine_kind for scenario in default_catalog()} == {"scripted"}
