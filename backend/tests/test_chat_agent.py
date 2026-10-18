from pathlib import Path

import pytest

from app.chat.agent import ChatAgent
from app.chat.context import build_schema_context
from app.chat.contracts import AgentAction, Narration, Presentation, Understanding
from app.chat.executor import QueryExecutor, QueryResult
from app.chat.grounding import extract_numbers, ungrounded_numbers
from app.chat.guard import SQLGuard
from app.chat.narration import LLMNarrator, TemplateNarrator
from app.chat.presentation import resolve_presentation
from app.chat.sql_agent import SQLAgent
from app.chat.understanding import LLMUnderstander, PassThroughUnderstander
from app.llm import LLMError
from app.profiling import HEURISTIC_STRATEGIES, NullSink, ProfilingPipeline, SourceFile

CUSTOMERS = "customer_id,segment\n" + "".join(f"{i},{'A' if i % 2 else 'B'}\n" for i in range(1, 6))
ORDERS = "order_id,customer_id,amount,order_date\n" + "".join(
    f"{100 + i},{i % 5 + 1},{i * 10},2024-0{i % 3 + 1}-15\n" for i in range(8)
)


class ScriptedLLM:
    """StructuredLLM stand-in: returns queued outputs per schema and records the prompts it saw."""

    def __init__(self, **queues) -> None:
        self.queues = {name: list(items) for name, items in queues.items()}
        self.calls: list[tuple[str, str]] = []

    def parse(self, step, system, user, schema):
        self.calls.append((schema.__name__, user))
        item = self.queues[schema.__name__].pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def act(action: str, sql: str | None = None, **kw) -> AgentAction:
    return AgentAction(thought="", action=action, sql=sql, message=kw.get("message"),
                       assumptions=kw.get("assumptions", []), presentation=kw.get("presentation"))


def understood(question: str, intent: str = "data_question", **kw) -> Understanding:
    return Understanding(standalone_question=question, intent=intent, output=kw.get("output", "auto"),
                         time_window=None, assumptions=kw.get("assumptions", []), reply=kw.get("reply"))


@pytest.fixture()
def env(tmp_path: Path):
    files = []
    for name, body in (("customers.csv", CUSTOMERS), ("orders.csv", ORDERS)):
        path = tmp_path / name
        path.write_text(body)
        files.append(SourceFile(name, name.removesuffix(".csv"), path, path.stat().st_size))
    db_path = tmp_path / "db.duckdb"
    profile = ProfilingPipeline(HEURISTIC_STRATEGIES).run(db_path, files, NullSink())
    context = build_schema_context(profile)
    executor = QueryExecutor(db_path)
    yield context, executor
    executor.close()


def make_agent(context, executor, llm, max_steps=4, repair=True) -> SQLAgent:
    return SQLAgent(llm, SQLGuard(context.columns, context.relationships), executor, max_steps=max_steps, repair=repair)


def test_sql_agent_explores_then_answers(env) -> None:
    context, executor = env
    llm = ScriptedLLM(AgentAction=[
        act("run_sql", "SELECT DISTINCT segment FROM customers"),
        act("final_sql", "SELECT SUM(amount) AS revenue FROM orders", assumptions=["all orders"]),
    ])
    out = make_agent(context, executor, llm).solve(context, [], "total revenue?")
    assert out.status == "answered" and out.result.rows == [[280]]
    assert [s.outcome for s in out.steps] == ["ok", "final"]
    assert "RESULT" in llm.calls[1][1] and "segment" in llm.calls[1][1]


def test_sql_agent_repairs_from_rejection_and_db_error(env) -> None:
    context, executor = env
    llm = ScriptedLLM(AgentAction=[
        act("final_sql", "SELECT * FROM sales"),
        act("final_sql", "SELECT revenue FROM orders"),
        act("final_sql", "SELECT COUNT(*) AS n FROM orders"),
    ])
    out = make_agent(context, executor, llm).solve(context, [], "how many orders?")
    assert out.status == "answered" and out.result.rows == [[8]]
    assert [s.outcome for s in out.steps] == ["rejected", "error", "final"]
    assert "Unknown table" in llm.calls[1][1] and "DATABASE ERROR" in llm.calls[2][1]


def test_sql_agent_without_repair_fails_fast(env) -> None:
    context, executor = env
    llm = ScriptedLLM(AgentAction=[act("final_sql", "SELECT * FROM sales")])
    out = make_agent(context, executor, llm, max_steps=1, repair=False).solve(context, [], "q")
    assert out.status == "failed" and len(llm.calls) == 1


def test_sql_agent_sends_empty_result_back_once(env) -> None:
    context, executor = env
    empty = "SELECT order_id FROM orders WHERE amount > 1000"
    llm = ScriptedLLM(AgentAction=[act("final_sql", empty), act("final_sql", empty)])
    out = make_agent(context, executor, llm).solve(context, [], "big orders?")
    assert out.status == "answered" and out.result.row_count == 0
    assert [s.outcome for s in out.steps] == ["needs_check", "final"]
    assert out.warnings and "no rows" in out.warnings[0]


def test_sql_agent_last_step_run_sql_counts_as_final(env) -> None:
    context, executor = env
    llm = ScriptedLLM(AgentAction=[act("run_sql", "SELECT 1 AS x"), act("run_sql", "SELECT 2 AS x")])
    out = make_agent(context, executor, llm, max_steps=2).solve(context, [], "q")
    assert out.status == "answered" and out.result.rows == [[2]]


def test_sql_agent_passes_clarify_through(env) -> None:
    context, executor = env
    llm = ScriptedLLM(AgentAction=[act("clarify", message="Which metric?")])
    out = make_agent(context, executor, llm).solve(context, [], "best customers?")
    assert out.status == "clarify" and out.message == "Which metric?"


def test_presentation_rules() -> None:
    single = QueryResult(["revenue"], [[280.0]], False, 0.0)
    assert resolve_presentation(None, single).kind == "text"
    series = QueryResult(["month", "revenue"], [["2024-01-01", 10], ["2024-02-01", 20], ["2024-03-01", 15]], False, 0.0)
    assert resolve_presentation(None, series).chart_type == "line"  # auto: dimension + measure becomes a chart
    wide = QueryResult(["a", "b", "c"], [["x", "y", 1], ["z", "w", 2]], False, 0.0)
    assert resolve_presentation(None, wide).kind == "table"  # two dimensions: no safe auto chart
    assert resolve_presentation(None, series, "chart").chart_type == "line"
    assert resolve_presentation(None, series, "table").kind == "table"
    bad = Presentation(kind="chart", chart_type="bar", x="nope", y=["revenue"], series=None, title=None)
    assert resolve_presentation(bad, series, "chart").kind == "chart"  # falls back to a valid auto chart
    cats = QueryResult(["segment", "n"], [["A", 3], ["B", 2]], False, 0.0)
    pie = Presentation(kind="chart", chart_type="pie", x="segment", y=["n"], series=None, title="Mix")
    assert resolve_presentation(pie, cats).chart_type == "pie"


def test_grounding_flags_invented_numbers() -> None:
    result = QueryResult(["segment", "revenue", "share"], [["A", 1234.5, 0.625], ["B", 740.0, 0.375]], False, 0.0)
    assert [n for _, n, _ in extract_numbers("$1.2k and 62.5%")] == [1200.0, 62.5]
    assert ungrounded_numbers("A made $1,234.50 (62.5% of revenue), B made $740.", result) == []
    assert ungrounded_numbers("A made $9,999.", result) == ["$9,999"]
    assert ungrounded_numbers("Amounts in cents divided by 100.0 for 2018.", result, context=["cents / 100.0 in 2018"]) == []


def test_chat_agent_routes_non_data_questions(env) -> None:
    context, executor = env
    llm = ScriptedLLM(Understanding=[understood("hi", "chitchat", reply="Hello!")])
    agent = ChatAgent(context, LLMUnderstander(llm), make_agent(context, executor, llm), TemplateNarrator())
    answer = agent.ask("hi", [])
    assert answer.status == "chitchat" and answer.message == "Hello!" and answer.sql is None


def test_chat_agent_end_to_end_with_grounded_narration(env) -> None:
    context, executor = env
    llm = ScriptedLLM(
        Understanding=[understood("Total revenue across all orders", assumptions=["revenue = sum of amount"])],
        AgentAction=[act("final_sql", "SELECT SUM(amount) AS revenue FROM orders")],
        Narration=[Narration(answer="Total revenue is 280.")],
    )
    agent = ChatAgent(context, LLMUnderstander(llm), make_agent(context, executor, llm), LLMNarrator(llm))
    answer = agent.ask("what's revenue", [])
    assert answer.status == "answered" and answer.message == "Total revenue is 280."
    assert answer.presentation.kind == "text" and answer.assumptions == ["revenue = sum of amount"]
    assert "revenue = sum of amount" in llm.calls[1][1]  # understanding brief reaches the SQL step
    turn = answer.to_turn()
    assert turn.sql and "280" in turn.result_preview


def test_chat_agent_replaces_ungrounded_narration_and_survives_llm_errors(env) -> None:
    context, executor = env
    llm = ScriptedLLM(
        Understanding=[LLMError("down")],
        AgentAction=[act("final_sql", "SELECT SUM(amount) AS revenue FROM orders")],
        Narration=[Narration(answer="Revenue was 4,500.")],
    )
    agent = ChatAgent(context, LLMUnderstander(llm), make_agent(context, executor, llm), LLMNarrator(llm))
    answer = agent.ask("revenue?", [])
    assert answer.status == "answered" and "4,500" not in answer.message and "280" in answer.message
    assert len(answer.notes) == 2


def test_pass_through_understander_keeps_question(env) -> None:
    context, _ = env
    u = PassThroughUnderstander().understand(context, [], "top 5 customers")
    assert u.standalone_question == "top 5 customers" and u.intent == "data_question"
