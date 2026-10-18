from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.jobs import CopyConflictError, JobStore

from test_chat_api import DIRTY_ORDERS, upload
from test_tables_api import make_client, rows_url


def copy(client: TestClient, session_id: str):
    return client.post(f"/api/sessions/{session_id}/copy")


def test_copy_starts_a_ready_session_on_the_same_cleaned_data(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        source_id, _ = upload(client, DIRTY_ORDERS)
        client.post(f"/api/sessions/{source_id}/cleaning", json={"issue_ids": ["orders.duplicate_rows"]})
        client.get(rows_url(source_id, "orders"))  # holds a read-only connection open on the source database

        response = copy(client, source_id)
        assert response.status_code == 201
        new_id = response.json()["session_id"]
        assert new_id != source_id

        source, new = client.get(f"/api/sessions/{source_id}").json(), client.get(f"/api/sessions/{new_id}").json()
        assert new["status"] == "completed" and new["cleaning"] == "applied"
        assert new["applied_fixes"] == source["applied_fixes"]
        assert new["profile"] == source["profile"]
        assert client.get(rows_url(new_id, "orders")).json()["total_rows"] == 8
        assert client.get(f"/api/sessions/{new_id}/messages").json() == []
        assert {s["session_id"] for s in client.get("/api/sessions").json()} == {source_id, new_id}


def test_copied_session_is_independent_and_survives_a_restart(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        source_id, _ = upload(client, DIRTY_ORDERS)
        new_id = copy(client, source_id).json()["session_id"]
        assert client.get(f"/api/sessions/{new_id}").json()["cleaning"] == "pending"

        # Cleaning the copy must not touch the source.
        client.post(f"/api/sessions/{new_id}/cleaning", json={"issue_ids": ["orders.duplicate_rows"]})
        assert client.get(f"/api/sessions/{source_id}").json()["cleaning"] == "pending"

    with make_client(tmp_path) as client:
        restored = client.get(f"/api/sessions/{new_id}").json()
        assert restored["status"] == "completed" and restored["cleaning"] == "applied"
        assert client.get(rows_url(new_id, "orders")).json()["total_rows"] == 8


def test_copy_rejects_unknown_and_unfinished_sessions(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        assert copy(client, "missing").status_code == 404

    store = JobStore(tmp_path / "store", pipeline_factory=None)  # copying never runs the pipeline
    session_id, directory = store.new_session_dir()
    store.create(session_id, directory, files=[], paths={})  # still queued
    with pytest.raises(CopyConflictError):
        store.copy_dataset(session_id)
    assert len(list(store.sessions_dir.iterdir())) == 1  # the half-made copy was cleaned up
