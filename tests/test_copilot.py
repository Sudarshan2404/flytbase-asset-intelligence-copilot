from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.copilot.config import CopilotConfigError, get_api_key, get_model_id
from app.copilot.loop import run_tool_loop
from app.copilot.provider import (
    FunctionCall,
    GeminiProvider,
    ModelTurn,
    ProviderError,
    classify_provider_error,
    parse_text_function_calls,
    sdk_function_declarations,
    set_provider,
)
from app.copilot.tools import tool_declarations
from app.copilot.schemas import ChatRequest
from app.copilot.service import handle_chat
from app.copilot.tools import ToolValidationError, execute_tool
from app.db import connect, init_schema
from app.domain.knowledge import record_statement
from app.domain.memory import add_message, start_conversation
from app.main import app


class ScriptedProvider:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)

    def generate(self, contents, tools) -> ModelTurn:
        if not self.turns:
            raise AssertionError("No scripted model turns remain")
        return self.turns.pop(0)


def _conn(mini_db: Path):
    conn = connect(mini_db)
    init_schema(conn)
    return conn


def test_missing_api_key(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(CopilotConfigError, match="GEMINI_API_KEY"):
        get_api_key()


def test_default_model_id(monkeypatch) -> None:
    monkeypatch.delenv("COPILOT_MODEL", raising=False)
    assert get_model_id() == "gemma-4-26b-a4b-it"
    monkeypatch.setenv("COPILOT_MODEL", "gemma-4-31b-it")
    assert get_model_id() == "gemma-4-31b-it"


def test_unknown_tool_and_bad_arguments(mini_db: Path) -> None:
    conn = _conn(mini_db)
    ctx = {"user_id": "U-201"}
    with pytest.raises(ToolValidationError, match="Unknown tool"):
        execute_tool("drop_database", {}, conn, ctx)
    with pytest.raises(ToolValidationError, match="asset_id"):
        execute_tool("get_well_status", {"asset_id": "not-a-well"}, conn, ctx)
    with pytest.raises(ToolValidationError, match="user_id"):
        execute_tool("get_well_status", {"asset_id": "WELL-0001", "user_id": "U-999"}, conn, ctx)
    conn.close()


def test_tool_iteration_limit(mini_db: Path) -> None:
    conn = _conn(mini_db)
    repeating = ModelTurn(function_calls=[FunctionCall(name="get_well_status", arguments={"asset_id": "WELL-0001"})])
    provider = ScriptedProvider([repeating] * 8)
    response = run_tool_loop(
        provider,
        conn,
        user_id="U-201",
        user_message="Is WELL-0001 producing?",
        history=[],
    )
    assert response.error == "tool_iteration_limit"
    assert response.tool_trace
    assert "invented" in response.answer.lower() or "limit" in response.answer.lower()
    conn.close()


def test_malformed_provider_response(mini_db: Path) -> None:
    conn = _conn(mini_db)

    class BadProvider:
        def generate(self, contents, tools):
            return "not a turn"

    response = run_tool_loop(BadProvider(), conn, user_id="U-201", user_message="hello", history=[])
    assert response.error == "malformed_provider_response"
    conn.close()


def test_citations_come_only_from_tool_results(mini_db: Path) -> None:
    conn = _conn(mini_db)
    provider = ScriptedProvider(
        [
            ModelTurn(function_calls=[FunctionCall(name="get_well_status", arguments={"asset_id": "WELL-0001"})]),
            ModelTurn(text='{"answer":"WELL-0001 is not producing. I also used INS-FAKE-999.","visual":{"type":"status_card"}}'),
        ]
    )
    response = run_tool_loop(
        provider,
        conn,
        user_id="U-201",
        user_message="Is WELL-0001 producing oil right now?",
        history=[],
    )
    ids = {item.record_id for item in response.citations}
    assert "INS-FAKE-999" not in ids
    assert any(item.record_id and str(item.record_id).startswith("INS-") or (item.record_id or "").startswith("generated:") for item in response.citations)
    assert response.visual is not None
    assert response.visual.type == "status_card"
    conn.close()


def test_insufficient_evidence_does_not_invent_facts(mini_db: Path) -> None:
    conn = _conn(mini_db)
    provider = ScriptedProvider(
        [
            ModelTurn(function_calls=[FunctionCall(name="get_well_status", arguments={"asset_id": "WELL-0002"})]),
            ModelTurn(text='{"answer":"There is no inspection for WELL-0002, so production cannot be determined."}'),
        ]
    )
    response = run_tool_loop(
        provider,
        conn,
        user_id="U-201",
        user_message="Is WELL-0002 producing?",
        history=[],
    )
    assert "cannot be determined" in response.answer.lower() or "no inspection" in response.answer.lower()
    assert "INS-FAKE" not in response.answer
    conn.close()


def test_conflicts_and_pending_confirmation_in_response(mini_db: Path) -> None:
    conn = _conn(mini_db)
    record_statement(
        conn,
        author_user_id="U-102",
        asset_id="WELL-0001",
        text="WELL-0001 is back online.",
        asserted_state="producing",
        created_at="2026-10-16T12:00:00Z",
    )
    provider = ScriptedProvider(
        [
            ModelTurn(function_calls=[FunctionCall(name="get_claims", arguments={"asset_id": "WELL-0001"})]),
            ModelTurn(text='{"answer":"The latest inspection still shows WELL-0001 not producing."}'),
        ]
    )
    response = run_tool_loop(
        provider,
        conn,
        user_id="U-201",
        user_message="Is WELL-0001 online?",
        history=[],
    )
    assert response.conflicts
    assert any(item.kind in {"conflict", "unconfirmed_claim"} for item in response.conflicts)
    conn.close()


def test_record_claim_tool_uses_requesting_user(mini_db: Path) -> None:
    conn = _conn(mini_db)
    provider = ScriptedProvider(
        [
            ModelTurn(
                function_calls=[
                    FunctionCall(
                        name="record_user_claim",
                        arguments={
                            "asset_id": "WELL-0001",
                            "text": "WELL-0001 is running fine.",
                            "asserted_state": "producing",
                        },
                    )
                ]
            ),
            ModelTurn(text='{"answer":"That claim conflicts with the latest inspection."}'),
        ]
    )
    response = run_tool_loop(
        provider,
        conn,
        user_id="U-204",
        user_message="WELL-0001 is running fine.",
        history=[],
    )
    assert response.pending_confirmation
    assert response.pending_confirmation[0].asset_id == "WELL-0001"
    conn.close()


def test_chat_endpoint_uses_mocked_model(mini_db: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(mini_db))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider = ScriptedProvider(
        [
            ModelTurn(function_calls=[FunctionCall(name="get_well_status", arguments={"asset_id": "WELL-0001"})]),
            ModelTurn(text='{"answer":"WELL-0001 is not producing oil.","visual":{"type":"status_card"}}'),
        ]
    )
    set_provider(provider)
    try:
        client = TestClient(app)
        response = client.post("/chat", json={"message": "Is WELL-0001 producing?", "user_id": "U-201"})
        assert response.status_code == 200
        body = response.json()
        assert body["answer"].startswith("WELL-0001")
        assert body["identity_authenticated"] is False
        assert body["user_id"] == "U-201"
        assert body["conversation_id"]
        assert body["error"] is None
    finally:
        set_provider(None)


def test_chat_missing_key_does_not_fabricate_answer(mini_db: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(mini_db))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    set_provider(None)
    client = TestClient(app)
    response = client.post("/chat", json={"message": "Hello", "user_id": "U-201"})
    assert response.status_code == 200
    body = response.json()
    assert body["error"]
    assert "GEMINI_API_KEY" in body["error"]
    assert "not configured" in body["answer"].lower()


def test_private_memory_isolation_in_chat(mini_db: Path) -> None:
    conn = _conn(mini_db)
    conversation = start_conversation(conn, "U-201", started_at="2026-10-15T09:00:00Z")
    add_message(
        conn,
        conversation_id=conversation,
        user_id="U-201",
        role="user",
        content="top downtime wells",
        created_at="2026-10-15T09:00:00Z",
        entities=[("well", "WELL-0121"), ("well", "WELL-0138"), ("well", "WELL-0107")],
    )
    conn.commit()
    jessica = handle_chat(
        conn,
        ChatRequest(message="For the three wells we discussed yesterday, what happened?", user_id="U-202"),
        provider=ScriptedProvider([]),
    )
    assert "no earlier conversation" in jessica.answer.lower()
    assert jessica.tool_trace == []
    conn.close()


def test_follow_up_uses_same_user_memory(mini_db: Path) -> None:
    conn = _conn(mini_db)
    conversation = start_conversation(conn, "U-201", started_at="2026-10-15T09:00:00Z")
    add_message(
        conn,
        conversation_id=conversation,
        user_id="U-201",
        role="user",
        content="top wells",
        created_at="2026-10-15T09:00:00Z",
        entities=[("well", "WELL-0001")],
    )
    conn.commit()
    provider = ScriptedProvider(
        [
            ModelTurn(function_calls=[FunctionCall(name="get_discussed_wells", arguments={})]),
            ModelTurn(text='{"answer":"You previously discussed WELL-0001."}'),
        ]
    )
    response = handle_chat(
        conn,
        ChatRequest(message="Remind me about those wells we discussed.", user_id="U-201"),
        provider=provider,
    )
    assert "WELL-0001" in response.answer
    conn.close()


def test_sdk_tool_declarations_use_parameters_field() -> None:
    declarations = sdk_function_declarations(tool_declarations())
    assert declarations
    first = declarations[0]
    assert set(first) == {"name", "description", "parameters"}
    assert "parameters_json_schema" not in first
    assert first["parameters"]["type"] == "object"
    assert "properties" in first["parameters"]


def test_parse_gemma_native_tool_call_text() -> None:
    text = '<|tool_call>call:get_well_status{asset_id:<|"|>WELL-0001<|"|>}<tool_call|>'
    calls = parse_text_function_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "get_well_status"
    assert calls[0].arguments["asset_id"] == "WELL-0001"


def test_provider_error_sanitizes_secrets() -> None:
    class FakeAPIError(Exception):
        code = 500
        status = "INTERNAL"
        message = "500 INTERNAL using key=AIzaSyDummySecretValueXX"

    error = classify_provider_error(FakeAPIError())
    assert error.http_status == 500
    assert error.provider_status == "INTERNAL"
    rendered = error.sanitized_message()
    assert "AIza" not in rendered
    assert "http_500" in rendered
    assert "provider_http_error" in rendered


def test_native_tools_http_500_falls_back_once_without_retrying() -> None:
    class FakeAPIError(Exception):
        def __init__(self) -> None:
            self.code = 500
            self.status = "INTERNAL"
            self.message = "INTERNAL"
            super().__init__("500 INTERNAL")

    class FakeResponse:
        function_calls = []
        candidates = []
        text = '{"name":"get_well_status","arguments":{"asset_id":"WELL-0001"}}'

    class FakeModels:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            config = kwargs.get("config")
            if config is not None and getattr(config, "tools", None):
                raise FakeAPIError()
            return FakeResponse()

    class FakeClient:
        def __init__(self) -> None:
            self.models = FakeModels()

    client = FakeClient()
    provider = GeminiProvider(api_key="unused", model="gemma-4-26b-a4b-it", client=client)
    turn = provider.generate(
        [{"role": "user", "parts": [{"text": "Is WELL-0001 producing?"}]}],
        [{"name": "get_well_status", "description": "status", "parameters": {"type": "object", "properties": {}}}],
    )
    assert len(client.models.calls) == 2
    assert client.models.calls[0]["config"].tools
    assert client.models.calls[1]["config"].tools is None
    assert turn.function_calls[0].name == "get_well_status"

    turn = provider.generate(
        [{"role": "user", "parts": [{"text": "again"}]}],
        [{"name": "get_well_status", "description": "status", "parameters": {"type": "object", "properties": {}}}],
    )
    assert len(client.models.calls) == 3
    assert client.models.calls[2]["config"].tools is None
    assert turn.function_calls[0].name == "get_well_status"


def test_non_500_provider_error_is_not_retried() -> None:
    class FakeAPIError(Exception):
        def __init__(self) -> None:
            self.code = 400
            self.status = "INVALID_ARGUMENT"
            self.message = "bad schema"
            super().__init__("400 INVALID_ARGUMENT")

    class FakeModels:
        calls = 0

        def generate_content(self, **kwargs):
            self.calls += 1
            raise FakeAPIError()

    class FakeClient:
        def __init__(self) -> None:
            self.models = FakeModels()

    provider = GeminiProvider(api_key="unused", model="gemma-4-26b-a4b-it", client=FakeClient())
    with pytest.raises(ProviderError) as exc:
        provider.generate(
            [{"role": "user", "parts": [{"text": "hello"}]}],
            [{"name": "get_well_status", "parameters": {"type": "object", "properties": {}}}],
        )
    assert exc.value.http_status == 400
    assert provider._client.models.calls == 1


@pytest.mark.live
def test_live_model_smoke() -> None:
    if os.environ.get("RUN_LIVE_COPILOT") != "1":
        pytest.skip("Set RUN_LIVE_COPILOT=1 and GEMINI_API_KEY to run the live smoke test")
    get_api_key()
    provider = GeminiProvider.from_env()
    turn = provider.generate(
        [{"role": "user", "parts": [{"text": "Call get_weather for Kyoto."}]}],
        [
            {
                "name": "get_weather",
                "description": "Get current weather for a given location.",
                "parameters": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                },
            }
        ],
    )
    assert turn.function_calls or turn.text
