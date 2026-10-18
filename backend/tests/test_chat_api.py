import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.chat.contracts import AgentAction, Narration, Presentation, Understanding
from app.chat.factory import ChatAgentFactory
from app.config import Settings
from app.llm import Completion
from app.main import create_app

CUSTOMERS = "customer_id,segment\n" + "".join(f"{i},{'A' if i % 2 else 'B'}\n" for i in range(1, 6))
ORDERS = "order_id,customer_id,amount\n" + "".join(f"{100 + i},{i % 5 + 1},{i * 10}\n" for i in range(8))
DIRTY_ORDERS = ORDERS + "107,3,70\n"  # duplicate of the last row


class ScriptedClient:
    """LLMClient stand-in returning queued outputs per schema."""

    model = "scripted"
    reasoning_effort = None

    def __init__(self) -> None:
        self.queues: dict[str, list] = {}

    def script(self, *outputs) -> None:
        for output in outputs:
            self.queues.setdefault(type(output).__name__, []).append(output)

    def complete(self, system, user, schema):
        return Completion(self.queues[schema.__name__].pop(0), 10, 5, 0.01)


def understood(question: str) -> Understanding:
    return Understanding(standalone_question=question, intent="data_question", output="auto",
                         time_window=None, assumptions=[], reply=None)


def final(sql: str, presentation: Presentation | None = None) -> AgentAction:
    return AgentAction(thought="", action="final_sql", sql=sql, message=None, assumptions=["all orders"],
                       presentation=presentation)


@pytest.fixture
def llm() -> ScriptedClient:
    return ScriptedClient()


@pytest.fixture
def client(tmp_path: Path, llm: ScriptedClient) -> TestClient:
    settings = Settings(storage_dir=tmp_path, max_files=3, max_file_size_bytes=4096, cors_origins=[])
    with TestClient(create_app(settings, chat_factory=ChatAgentFactory(llm))) as test_client:
        yield test_client


def upload(client: TestClient, orders: str = ORDERS) -> tuple[str, dict]:
    response = client.post("/api/sessions", files=[
        ("files", ("customers.csv", CUSTOMERS, "text/csv")), ("files", ("orders.csv", orders, "text/csv")),
    ])
    session_id = response.json()["session_id"]
    for _ in range(100):
        body = client.get(f"/api/sessions/{session_id}").json()
        if body["status"] in ("completed", "failed"):
            return session_id, body
        time.sleep(0.05)
    raise AssertionError("profiling did not finish")


def events(response) -> list[tuple[str, dict]]:
    parsed = []
    for block in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


def ask(client: TestClient, session_id: str, question: str):
    return client.post(f"/api/sessions/{session_id}/messages", json={"question": question})


def test_chat_streams_progress_then_answer(client: TestClient, llm: ScriptedClient) -> None:
    session_id, body = upload(client)
    assert body["cleaning"] == "not_needed" and body["chat_available"] is True
    llm.script(
        understood("Revenue by segment"),
        final("SELECT c.segment, SUM(o.amount) AS revenue FROM orders o JOIN customers c "
              "ON o.customer_id = c.customer_id GROUP BY 1 ORDER BY 1",
              Presentation(kind="chart", chart_type="bar", x="segment", y=["revenue"], series=None, title=None)),
        Narration(answer="Segment A brings in 180 and segment B 100."),
    )
    response = ask(client, session_id, "revenue by segment")
    assert response.status_code == 200
    stream = events(response)
    kinds = [kind for kind, _ in stream]
    assert kinds[0] == "stage" and "step" in kinds and kinds[-1] == "answer"
    answer = stream[-1][1]
    assert answer["status"] == "answered" and answer["view"] == "chart"
    assert answer["table"]["rows"] == [["A", 180], ["B", 100]]
    assert answer["chart"] == {"chart_type": "bar", "x": "segment", "y": ["revenue"], "series": None, "title": None}
    assert answer["llm_calls"] == 3 and answer["assumptions"] == ["all orders"]

    history = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [m["role"] for m in history] == ["user", "assistant"]

    csv = client.get(f"/api/sessions/{session_id}/messages/{answer['id']}/download.csv")
    assert csv.status_code == 200 and csv.text.splitlines() == ["segment,revenue", "A,180", "B,100"]


def test_chat_is_blocked_until_cleaning_is_decided(client: TestClient, llm: ScriptedClient) -> None:
    session_id, body = upload(client, DIRTY_ORDERS)
    assert body["cleaning"] == "pending"
    fixable = [i["id"] for i in body["issues"] if i["fix"]]
    assert fixable == ["orders.duplicate_rows"]
    assert ask(client, session_id, "how many orders?").status_code == 409

    decided = client.post(f"/api/sessions/{session_id}/cleaning", json={"issue_ids": fixable})
    assert decided.status_code == 200
    status = decided.json()
    assert status["cleaning"] == "applied" and len(status["applied_fixes"]) == 1
    orders = next(t for t in status["profile"]["tables"] if t["table_name"] == "orders")
    assert orders["row_count"] == 8 and orders["duplicate_row_count"] == 0
    assert client.post(f"/api/sessions/{session_id}/cleaning", json={"issue_ids": []}).status_code == 409

    llm.script(understood("How many orders?"), final("SELECT COUNT(*) AS n FROM orders"),
               Narration(answer="There are 8 orders."))
    answer = events(ask(client, session_id, "how many orders?"))[-1][1]
    assert answer["table"]["rows"] == [[8]] and answer["content"] == "There are 8 orders."


def test_skipping_cleaning_keeps_data(client: TestClient) -> None:
    session_id, _ = upload(client, DIRTY_ORDERS)
    status = client.post(f"/api/sessions/{session_id}/cleaning", json={"issue_ids": []}).json()
    assert status["cleaning"] == "skipped" and status["applied_fixes"] == []
    assert any(i["kind"] == "duplicate_rows" for i in status["issues"])


def test_chat_rejects_empty_question_and_unknown_session(client: TestClient) -> None:
    session_id, _ = upload(client)
    assert ask(client, session_id, "   ").status_code == 400
    assert ask(client, "missing", "hi").status_code == 404


def test_chat_unavailable_without_llm(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path, max_files=3, max_file_size_bytes=4096, cors_origins=[])
    client = TestClient(create_app(settings, chat_factory=ChatAgentFactory(None)))
    session_id, body = upload(client)
    assert body["chat_available"] is False
    assert ask(client, session_id, "how many orders?").status_code == 503


def test_sessions_and_history_survive_a_restart(tmp_path: Path, llm: ScriptedClient) -> None:
    settings = Settings(storage_dir=tmp_path, max_files=3, max_file_size_bytes=4096, cors_origins=[])
    with TestClient(create_app(settings, chat_factory=ChatAgentFactory(llm))) as first:
        dirty_id, _ = upload(first, DIRTY_ORDERS)
        first.post(f"/api/sessions/{dirty_id}/cleaning", json={"issue_ids": ["orders.duplicate_rows"]})
        session_id, _ = upload(first)
        llm.script(understood("How many orders?"), final("SELECT COUNT(*) AS n FROM orders"),
                   Narration(answer="There are 8 orders."))
        answer = events(ask(first, session_id, "how many orders?"))[-1][1]

    with TestClient(create_app(settings, chat_factory=ChatAgentFactory(llm))) as second:
        sessions = second.get("/api/sessions").json()
        assert [s["session_id"] for s in sessions] == [session_id, dirty_id]
        assert sessions[0]["title"] == "how many orders?" and sessions[0]["question_count"] == 1
        assert sessions[1]["title"] == "customers.csv, orders.csv" and sessions[1]["cleaning"] == "applied"
        assert sessions[0]["table_count"] == 2 and sessions[0]["total_rows"] == 13

        restored = second.get(f"/api/sessions/{dirty_id}").json()
        assert restored["status"] == "completed" and restored["cleaning"] == "applied"
        assert len(restored["applied_fixes"]) == 1

        history = second.get(f"/api/sessions/{session_id}/messages").json()
        assert [m["role"] for m in history] == ["user", "assistant"] and history[1]["id"] == answer["id"]
        csv = second.get(f"/api/sessions/{session_id}/messages/{answer['id']}/download.csv")
        assert csv.status_code == 200 and csv.text.splitlines() == ["n", "8"]

        llm.script(understood("And customers?"), final("SELECT COUNT(*) AS n FROM customers"),
                   Narration(answer="There are 5 customers."))
        events(ask(second, session_id, "and customers?"))
        assert len(second.get(f"/api/sessions/{session_id}/messages").json()) == 4
        assert second.get("/api/sessions").json()[0]["question_count"] == 2


def test_interrupted_profiling_is_marked_failed_after_restart(tmp_path: Path) -> None:
    from app.jobs import INTERRUPTED_ERROR, Job
    from app.persistence import JsonSessionRepository
    from app.schemas import FileProgress

    directory = tmp_path / "sessions" / "abc"
    (directory / "uploads").mkdir(parents=True)
    files = [FileProgress(file_name="a.csv", table_name="a", size_bytes=3, status="loading")]
    job = Job(session_id="abc", directory=directory, files=files, paths={"a": directory / "uploads" / "00_a.csv"},
              status="profiling")
    JsonSessionRepository(tmp_path / "sessions").save_session(job.to_record())

    settings = Settings(storage_dir=tmp_path, max_files=3, max_file_size_bytes=4096, cors_origins=[])
    client = TestClient(create_app(settings, chat_factory=ChatAgentFactory(None)))
    body = client.get("/api/sessions/abc").json()
    assert body["status"] == "failed" and body["error"] == INTERRUPTED_ERROR
    assert client.get("/api/sessions/abc/messages").json() == []
