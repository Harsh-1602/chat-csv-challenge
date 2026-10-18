from pathlib import Path

import duckdb
import pytest

from app.chat.context import build_schema_context
from app.chat.executor import QueryError, QueryExecutor
from app.chat.guard import SQLGuard
from app.profiling import HEURISTIC_STRATEGIES, NullSink, ProfilingPipeline, SourceFile
from app.schemas import Relationship

CUSTOMERS = "customer_id,segment,signup_date\n" + "".join(
    f"{i},{'A' if i % 2 else 'B'},2024-01-{i:02d}\n" for i in range(1, 6)
)
ORDERS = "order_id,customer_id,amount\n" + "".join(f"{100 + i},{i % 5 + 1},{i * 10}\n" for i in range(8))

COLUMNS = {
    "customers": frozenset({"customer_id", "segment", "signup_date"}),
    "orders": frozenset({"order_id", "customer_id", "amount"}),
}
ORDERS_TO_CUSTOMERS = Relationship(
    from_table="orders", from_column="customer_id", to_table="customers", to_column="customer_id",
    status="accepted", source="candidate", reason="", cardinality="many-to-one", match_rate=1.0,
    orphan_count=0, parent_coverage=1.0, fan_out=1.6,
)


@pytest.fixture()
def database(tmp_path: Path) -> tuple[Path, list[SourceFile]]:
    files = []
    for name, body in (("customers.csv", CUSTOMERS), ("orders.csv", ORDERS)):
        path = tmp_path / name
        path.write_text(body)
        files.append(SourceFile(name, name.removesuffix(".csv"), path, path.stat().st_size))
    return tmp_path / "db.duckdb", files


def test_schema_context_describes_tables_keys_and_time(database) -> None:
    db_path, files = database
    profile = ProfilingPipeline(HEURISTIC_STRATEGIES).run(db_path, files, NullSink())
    context = build_schema_context(profile)

    assert context.tables == {"customers", "orders"}
    assert context.columns["orders"] == COLUMNS["orders"]
    assert "TABLE customers" in context.text and "customer_id BIGINT PRIMARY KEY" in context.text
    assert "FK -> customers.customer_id" in context.text
    assert "orders.customer_id -> customers.customer_id" in context.text
    assert context.latest_date is not None and context.latest_date.startswith("2024-01-05")


guard = SQLGuard(COLUMNS, (ORDERS_TO_CUSTOMERS,))


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT segment, COUNT(*) FROM customers GROUP BY 1;",
        "WITH t AS (SELECT * FROM orders) SELECT SUM(amount) FROM t",
        "SELECT 1 UNION ALL SELECT 2",
    ],
)
def test_guard_allows_read_only_queries(sql: str) -> None:
    result = guard.check(sql)
    assert result.ok, result.errors


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("DROP TABLE orders", "Only SELECT"),
        ("SELECT 1; DELETE FROM orders", "Exactly one"),
        ("INSERT INTO orders VALUES (1, 1, 1)", "Only SELECT"),
        ("SELECT * FROM read_csv('/etc/passwd')", "read_csv"),
        ("SELECT * FROM secrets", "Unknown table: secrets"),
        ("SELEC oops", "Only SELECT"),
        ("SELECT (1 + FROM orders", "could not be parsed"),
        ("COPY orders TO 'x.csv'", "Only SELECT"),
        ("ATTACH 'other.db'", "Only SELECT"),
    ],
)
def test_guard_blocks_unsafe_sql(sql: str, message: str) -> None:
    result = guard.check(sql)
    assert not result.ok
    assert any(message in e for e in result.errors), result.errors


def test_guard_warns_on_join_fan_out() -> None:
    # Hypothetical: a customer-level measure summed after joining the many-side orders table.
    fan_guard = SQLGuard({**COLUMNS, "customers": COLUMNS["customers"] | {"credit"}}, (ORDERS_TO_CUSTOMERS,))
    risky = fan_guard.check("SELECT SUM(c.credit) FROM customers c JOIN orders o ON o.customer_id = c.customer_id")
    assert risky.ok and risky.warnings and "fan-out" in risky.warnings[0]
    safe = fan_guard.check("SELECT SUM(o.amount) FROM customers c JOIN orders o ON o.customer_id = c.customer_id")
    assert safe.ok and not safe.warnings


def test_executor_is_read_only_and_capped(database) -> None:
    db_path, files = database
    ProfilingPipeline(HEURISTIC_STRATEGIES).run(db_path, files, NullSink())
    executor = QueryExecutor(db_path, max_rows=3)
    try:
        result = executor.run("SELECT * FROM orders ORDER BY order_id")
        assert result.columns == ["order_id", "customer_id", "amount"]
        assert result.row_count == 3 and result.truncated
        assert "(3+ rows)" in result.preview()
        with pytest.raises(QueryError):
            executor.run("DELETE FROM orders")
        with pytest.raises(QueryError):
            executor.run("SELECT nope FROM orders")
    finally:
        executor.close()
    assert duckdb.connect(str(db_path)).execute("SELECT COUNT(*) FROM orders").fetchone() == (8,)


def test_executor_times_out(database) -> None:
    db_path, files = database
    ProfilingPipeline(HEURISTIC_STRATEGIES).run(db_path, files, NullSink())
    executor = QueryExecutor(db_path, timeout_s=0.2)
    try:
        with pytest.raises(QueryError, match="time limit"):
            executor.run("SELECT COUNT(*) FROM range(10000000000) a")
    finally:
        executor.close()
