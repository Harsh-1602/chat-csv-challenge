from pathlib import Path

from fastapi.testclient import TestClient

from app.chat.factory import ChatAgentFactory
from app.config import Settings
from app.main import create_app

from test_chat_api import DIRTY_ORDERS, upload


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(storage_dir=tmp_path, max_files=3, max_file_size_bytes=4096, cors_origins=[])
    return TestClient(create_app(settings, chat_factory=ChatAgentFactory(None)))


def rows_url(session_id: str, table: str) -> str:
    return f"/api/sessions/{session_id}/tables/{table}/rows"


def test_table_rows_are_paginated_and_sortable_without_an_llm(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        session_id, _ = upload(client)

        first = client.get(rows_url(session_id, "orders"), params={"limit": 3}).json()
        assert [c["name"] for c in first["columns"]] == ["order_id", "customer_id", "amount"]
        assert first["total_rows"] == 8 and first["offset"] == 0 and first["limit"] == 3
        assert [r[0] for r in first["rows"]] == [100, 101, 102]

        last = client.get(rows_url(session_id, "orders"), params={"offset": 6, "limit": 3}).json()
        assert [r[0] for r in last["rows"]] == [106, 107]

        top = client.get(rows_url(session_id, "orders"), params={"sort": "amount", "desc": True, "limit": 2}).json()
        assert [r[2] for r in top["rows"]] == [70, 60] and top["sort"] == "amount" and top["descending"] is True


def test_table_rows_reject_unknown_names_and_bad_limits(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        session_id, _ = upload(client)
        assert client.get(rows_url(session_id, "nope")).status_code == 404
        assert client.get(rows_url(session_id, "orders__raw")).status_code == 404
        assert client.get(rows_url(session_id, "orders"), params={"sort": "amount; DROP"}).status_code == 404
        assert client.get(rows_url(session_id, "orders"), params={"limit": 500}).status_code == 422
        assert client.get(rows_url("missing", "orders")).status_code == 404


def test_table_rows_wait_for_the_cleaning_decision_then_show_cleaned_data(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        session_id, body = upload(client, DIRTY_ORDERS)
        assert body["cleaning"] == "pending"
        assert client.get(rows_url(session_id, "orders")).status_code == 409

        client.post(f"/api/sessions/{session_id}/cleaning", json={"issue_ids": ["orders.duplicate_rows"]})
        page = client.get(rows_url(session_id, "orders")).json()
        assert page["total_rows"] == 8 and len(page["rows"]) == 8
