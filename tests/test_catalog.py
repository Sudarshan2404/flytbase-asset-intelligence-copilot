from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_overview_and_wells_use_database(mini_db: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(mini_db))
    client = TestClient(app)
    overview = client.get("/api/overview")
    assert overview.status_code == 200
    body = overview.json()
    assert body["counts"]["wells"] >= 1
    assert body["identity_authenticated"] is False
    wells = client.get("/api/wells", params={"q": "WELL-0001"})
    assert wells.status_code == 200
    assert wells.json()["wells"][0]["asset_id"] == "WELL-0001"
    detail = client.get("/api/wells/WELL-0001")
    assert detail.status_code == 200
    assert "production" in detail.json()


def test_unknown_well_is_404(mini_db: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(mini_db))
    client = TestClient(app)
    assert client.get("/api/wells/WELL-9999").status_code == 404


def test_compliance_and_knowledge_endpoints(mini_db: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(mini_db))
    client = TestClient(app)
    compliance = client.get("/api/compliance", params={"asset_id": "WELL-0001"})
    assert compliance.status_code == 200
    assert "findings" in compliance.json()
    notes = client.get("/api/knowledge/notes", params={"asset_id": "WELL-0001"})
    assert notes.status_code == 200
    assert notes.json()["scope"] == "shared"
    claims = client.get("/api/knowledge/claims")
    assert claims.status_code == 200
    corrections = client.get("/api/knowledge/corrections")
    assert corrections.status_code == 200


def test_conversations_are_listed_for_the_requesting_user_only(mini_db: Path, monkeypatch) -> None:
    from app.domain.memory import add_message, start_conversation
    from app.db import connect, init_schema

    monkeypatch.setenv("DATABASE_PATH", str(mini_db))
    conn = connect(mini_db)
    init_schema(conn)
    cid = start_conversation(conn, "U-201", started_at="2026-10-16T09:00:00Z", title="Q6")
    add_message(
        conn,
        conversation_id=cid,
        user_id="U-201",
        role="user",
        content="downtime",
        created_at="2026-10-16T09:00:00Z",
    )
    conn.commit()
    conn.close()
    client = TestClient(app)
    michael = client.get("/api/conversations", params={"user_id": "U-201"})
    jessica = client.get("/api/conversations", params={"user_id": "U-202"})
    assert michael.status_code == 200
    assert michael.json()["identity_authenticated"] is False
    assert michael.json()["visuals_persisted"] is False
    assert any(item["conversation_id"] == cid for item in michael.json()["conversations"])
    assert jessica.json()["conversations"] == []


def test_chat_response_includes_visuals_list(mini_db: Path, monkeypatch) -> None:
    from app.copilot.provider import FunctionCall, ModelTurn, set_provider

    monkeypatch.setenv("DATABASE_PATH", str(mini_db))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    class Scripted:
        def __init__(self) -> None:
            self.n = 0

        def generate(self, contents, tools):
            self.n += 1
            if self.n == 1:
                return ModelTurn(
                    function_calls=[FunctionCall(name="get_well_status", arguments={"asset_id": "WELL-0001"})]
                )
            return ModelTurn(text='{"answer":"WELL-0001 is not producing.","visual":{"type":"status_card"}}')

    set_provider(Scripted())
    try:
        client = TestClient(app)
        response = client.post("/chat", json={"message": "Is WELL-0001 producing?", "user_id": "U-201"})
        assert response.status_code == 200
        body = response.json()
        assert "visuals" in body
        assert body["visuals"][0]["type"] == "status_card"
        assert body["identity_authenticated"] is False
    finally:
        set_provider(None)


def test_chat_downtime_returns_bar_chart_visuals(mini_db: Path, monkeypatch) -> None:
    from app.copilot.provider import FunctionCall, ModelTurn, set_provider

    monkeypatch.setenv("DATABASE_PATH", str(mini_db))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    class Scripted:
        def __init__(self) -> None:
            self.n = 0

        def generate(self, contents, tools):
            self.n += 1
            if self.n == 1:
                return ModelTurn(
                    function_calls=[
                        FunctionCall(
                            name="get_top_downtime_wells",
                            arguments={"area": "SW-23", "start": "2026-06-16", "end": "2026-10-16", "limit": 3},
                        )
                    ]
                )
            return ModelTurn(text='{"answer":"Northeast comparison uses downtime days.","visual":{"type":"bar_chart"}}')

    set_provider(Scripted())
    try:
        client = TestClient(app)
        response = client.post("/chat", json={"message": "Compare downtime in SW-23", "user_id": "U-201"})
        assert response.status_code == 200
        types = [item["type"] for item in response.json()["visuals"]]
        assert "bar_chart" in types
        bar = next(item for item in response.json()["visuals"] if item["type"] == "bar_chart")
        assert bar["points"]
        assert bar["series"]
    finally:
        set_provider(None)
