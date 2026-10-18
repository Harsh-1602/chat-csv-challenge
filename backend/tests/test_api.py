import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

CUSTOMERS = "customer_id,name,signup_date,segment\n" + "".join(
    f"{i},Name{i},2024-01-0{i},{'A' if i % 3 else 'B'}\n" for i in range(1, 7)
)
ORDERS = (
    "order_id,customer_id,order_date,total_price\n"
    "10,1,2024-04-01,100.5\n11,1,2024-04-02,20\n12,2,2024-04-03,30\n13,3,2024-04-04,\n13,3,2024-04-04,\n"
)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(storage_dir=tmp_path, max_files=3, max_file_size_bytes=1024, cors_origins=[])
    return TestClient(create_app(settings))


def wait_for(client: TestClient, url: str) -> dict:
    for _ in range(100):
        body = client.get(url).json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("profiling did not finish")


def test_upload_profiles_tables_and_relationships(client: TestClient) -> None:
    response = client.post(
        "/api/sessions",
        files=[("files", ("Customers.csv", CUSTOMERS, "text/csv")), ("files", ("orders.csv", ORDERS, "text/csv"))],
    )
    assert response.status_code == 202
    body = wait_for(client, response.json()["status_url"])

    assert body["status"] == "completed"
    assert body["progress"] == 1.0
    assert all(f["status"] == "completed" for f in body["files"])
    profile = body["profile"]
    assert profile["total_rows"] == 11
    tables = {t["table_name"]: t for t in profile["tables"]}
    assert tables["customers"]["primary_key"] == ["customer_id"]
    assert tables["customers"]["primary_key_source"] == "sql_fallback"
    orders = tables["orders"]
    assert orders["primary_key"] == []
    assert orders["primary_key_source"] == "none"
    assert orders["duplicate_row_count"] == 1
    columns = {c["name"]: c for c in orders["columns"]}
    assert columns["total_price"]["role"] == "measure"
    assert columns["total_price"]["null_count"] == 2
    assert columns["order_date"]["role"] == "time"
    assert columns["customer_id"]["role"] == "identifier"
    assert any("duplicate" in w for w in orders["warnings"])
    segment = {c["name"]: c for c in tables["customers"]["columns"]}["segment"]
    assert segment["role"] == "dimension"
    assert segment["top_values"][0] == {"value": "A", "count": 4}

    [rel] = profile["relationships"]
    assert {k: rel[k] for k in ("from_table", "from_column", "to_table", "to_column")} == {
        "from_table": "orders",
        "from_column": "customer_id",
        "to_table": "customers",
        "to_column": "customer_id",
    }
    assert rel["status"] == "accepted"
    assert rel["source"] == "candidate"
    assert rel["cardinality"] == "many-to-one"
    assert rel["match_rate"] == 1.0
    assert rel["orphan_count"] == 0
    assert rel["parent_coverage"] == 0.5

    analysis = profile["analysis"]
    assert analysis["llm_enabled"] is False
    assert analysis["degraded"] is False
    assert profile["semantic_model"] is None


def test_rejects_non_csv(client: TestClient) -> None:
    response = client.post("/api/sessions", files=[("files", ("notes.txt", "x", "text/plain"))])
    assert response.status_code == 400


def test_rejects_oversized_file(client: TestClient) -> None:
    big = "a,b\n" + "1,2\n" * 500
    response = client.post("/api/sessions", files=[("files", ("big.csv", big, "text/csv"))])
    assert response.status_code == 413


def test_rejects_too_many_files(client: TestClient) -> None:
    files = [("files", (f"f{i}.csv", "a\n1\n", "text/csv")) for i in range(4)]
    assert client.post("/api/sessions", files=files).status_code == 400


def test_duplicate_file_names_get_unique_tables(client: TestClient) -> None:
    response = client.post(
        "/api/sessions",
        files=[("files", ("data.csv", "a\n1\n", "text/csv")), ("files", ("data.csv", "b\n2\n", "text/csv"))],
    )
    body = wait_for(client, response.json()["status_url"])
    assert [t["table_name"] for t in body["profile"]["tables"]] == ["data", "data_2"]


def test_unknown_session_returns_404(client: TestClient) -> None:
    assert client.get("/api/sessions/missing").status_code == 404
