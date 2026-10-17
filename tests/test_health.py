from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_reports_counts(mini_db: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(mini_db))
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database_exists"] is True
    assert body["counts"]["wells"] == 2
    assert body["counts"]["inspections"] == 3


def test_health_without_database(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "missing.db"))
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database_exists"] is False
