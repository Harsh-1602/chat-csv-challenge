import json
from pathlib import Path

import pytest

from app.llm import LLMError, TrackedLLM
from app.profiling import (
    HEURISTIC_STRATEGIES,
    NullSink,
    ProfilingPipeline,
    ProfilingPipelineFactory,
    SourceFile,
    Strategies,
    safe_table_name,
)
from app.profiling.contracts import (
    ColumnUnderstanding,
    MetricDraft,
    ProposedLink,
    RelationshipReview,
    LinkDecision,
    SemanticModelDraft,
    SynonymDraft,
    TableUnderstanding,
)
from app.profiling.interpreters import LLMTableInterpreter
from app.profiling.judges import LLMRelationshipJudge
from app.profiling.semantics import LLMSemanticModeler
from app.config import LLMSettings
from app.llm import Completion

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

CUSTOMERS = "customer_id,full_name,segment\n" + "".join(f"{i},Name{i},{'A' if i % 2 else 'B'}\n" for i in range(1, 6))
ORDERS = "order_id,buyer,amount\n" + "".join(f"{100 + i},{i % 5 + 1},{i * 10}\n" for i in range(8))


def col(name: str, role: str, refs: str | None = None) -> ColumnUnderstanding:
    return ColumnUnderstanding(name=name, role=role, semantic_type=role, description=f"The {name}.", unit=None,
                               references_entity=refs)


UNDERSTANDING = {
    "customers": TableUnderstanding(
        entity="customer", description="Customers.", grain="one row per customer", primary_key=["customer_id"],
        columns=[col("customer_id", "identifier"), col("full_name", "text"), col("segment", "dimension")],
    ),
    # Over-specified key: SQL should minimise it to order_id. `amount` is omitted to exercise gap-filling.
    "orders": TableUnderstanding(
        entity="order", description="Orders.", grain="one row per order", primary_key=["order_id", "buyer"],
        columns=[col("order_id", "identifier"), col("buyer", "identifier", "customer")],
    ),
}


class FakeClient:
    """Deterministic stand-in for the OpenAI client (implements app.llm.LLMClient)."""

    model = "fake-model"
    reasoning_effort = "medium"

    def __init__(self, fail_steps: set[str] = frozenset()) -> None:
        self.fail_steps = fail_steps
        self.steps: list[str] = []

    def complete(self, system, user, schema):
        step = schema.__name__
        self.steps.append(step)
        if step in self.fail_steps:
            raise LLMError("boom")
        if schema is TableUnderstanding:
            table = next(name for name in UNDERSTANDING if f'"table": "{name}"' in user)
            out = UNDERSTANDING[table]
        elif schema is RelationshipReview:
            payload = json.loads(user)
            out = RelationshipReview(
                decisions=[LinkDecision(candidate_id=c["id"], decision="reject", reason="no") for c in payload["candidates"]],
                missing_links=[
                    ProposedLink(from_column="orders.buyer", to_column="customers.customer_id", reason="buyer is a customer"),
                    ProposedLink(from_column="orders.order_id", to_column="orders.order_id", reason="bogus self link"),
                ],
            )
        else:
            out = SemanticModelDraft(
                dataset_summary="Orders by customers.",
                metrics=[
                    MetricDraft(name="revenue", table="orders", expression="SUM(amount)", description="Total."),
                    MetricDraft(name="broken", table="orders", expression="SUM(no_such_column)", description="Bad."),
                ],
                synonyms=[SynonymDraft(term="client", maps_to="customers")],
                caveats=[],
                example_questions=["What is total revenue?"],
            )
        return Completion(output=out, prompt_tokens=10, completion_tokens=5, latency_s=0.0)


def write_dataset(tmp_path: Path) -> list[SourceFile]:
    files = []
    for name, body in (("customers.csv", CUSTOMERS), ("orders.csv", ORDERS)):
        path = tmp_path / name
        path.write_text(body)
        files.append(SourceFile(name, name.removesuffix(".csv"), path, path.stat().st_size))
    return files


def llm_pipeline(client: FakeClient) -> ProfilingPipeline:
    llm = TrackedLLM(client)
    return ProfilingPipeline(
        Strategies(LLMTableInterpreter(llm), LLMRelationshipJudge(llm), LLMSemanticModeler(llm)), usage=llm
    )


def test_safe_table_name() -> None:
    existing: set[str] = set()
    assert safe_table_name("Order Items.csv", existing) == "order_items"
    assert safe_table_name("order-items.csv", existing) == "order_items_2"
    assert safe_table_name("2024 sales.csv", existing) == "t_2024_sales"
    assert safe_table_name("$$$.csv", existing) == "table"


def test_llm_path_is_verified_by_sql(tmp_path: Path) -> None:
    client = FakeClient()
    profile = llm_pipeline(client).run(tmp_path / "db.duckdb", write_dataset(tmp_path), NullSink())

    tables = {t.table_name: t for t in profile.tables}
    assert tables["customers"].primary_key == ["customer_id"]
    assert tables["customers"].primary_key_source == "llm_verified"
    assert tables["orders"].primary_key == ["order_id"]
    assert tables["orders"].primary_key_source == "llm_minimised"
    roles = {c.name: c.role for c in tables["orders"].columns}
    assert roles == {"order_id": "identifier", "buyer": "identifier", "amount": "measure"}

    [link] = profile.relationships  # the self-link proposal is discarded
    assert (link.from_table, link.from_column, link.to_table, link.to_column) == ("orders", "buyer", "customers", "customer_id")
    assert (link.status, link.source, link.match_rate) == ("accepted", "llm_proposed", 1.0)

    metrics = {m.name: m for m in profile.semantic_model.metrics}
    assert metrics["revenue"].valid and metrics["revenue"].error is None
    assert not metrics["broken"].valid and metrics["broken"].error

    analysis = profile.analysis
    assert analysis.llm_enabled and not analysis.degraded
    assert (analysis.model, analysis.reasoning_effort) == ("fake-model", "medium")
    assert analysis.llm_calls == 4 and analysis.prompt_tokens == 40 and analysis.completion_tokens == 20


def test_llm_failure_degrades_to_heuristics(tmp_path: Path) -> None:
    client = FakeClient(fail_steps={"RelationshipReview", "SemanticModelDraft"})
    profile = llm_pipeline(client).run(tmp_path / "db.duckdb", write_dataset(tmp_path), NullSink())

    assert profile.table_count == 2
    assert profile.analysis.degraded
    assert len(profile.analysis.notes) == 2
    assert profile.semantic_model is None
    # Heuristic judge cannot link `buyer` (name does not match), so nothing is accepted.
    assert all(r.status != "accepted" for r in profile.relationships)


def test_factory_respects_disabled_llm(tmp_path: Path) -> None:
    client = FakeClient()
    factory = ProfilingPipelineFactory(LLMSettings(enabled=False, model="m", reasoning_effort="medium"), client)
    profile = factory.create().run(tmp_path / "db.duckdb", write_dataset(tmp_path), NullSink())
    assert not factory.llm_enabled
    assert client.steps == []
    assert not profile.analysis.llm_enabled


def test_unreadable_file_is_reported(tmp_path: Path) -> None:
    files = write_dataset(tmp_path)
    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"a,b\n\xff\xfe,1\n")
    profile = ProfilingPipeline(HEURISTIC_STRATEGIES).run(
        tmp_path / "db.duckdb", files + [SourceFile("bad.csv", "bad", bad, bad.stat().st_size)], NullSink()
    )
    assert profile.table_count == 2
    assert [f.file_name for f in profile.failed_files] == ["bad.csv"]


@pytest.mark.skipif(not DATA_DIR.exists(), reason="sample data not available")
def test_sample_dataset_profile(tmp_path: Path) -> None:
    names: set[str] = set()
    files = [SourceFile(f.name, safe_table_name(f.name, names), f, f.stat().st_size) for f in sorted(DATA_DIR.glob("*.csv"))]

    class Recorder(NullSink):
        def __init__(self) -> None:
            self.statuses: list[str] = []
            self.fractions: list[float] = []

        def file_status(self, table, status, error=None) -> None:
            self.statuses.append(status)

        def progress(self, fraction, stage) -> None:
            self.fractions.append(fraction)

    sink = Recorder()
    profile = ProfilingPipeline(HEURISTIC_STRATEGIES).run(tmp_path / "db.duckdb", files, sink)

    assert profile.table_count == 8
    assert profile.total_rows == 78_206
    assert not profile.failed_files
    assert sink.statuses.count("completed") == 8
    assert sink.fractions == sorted(sink.fractions) and max(sink.fractions) < 1.0
    links = {(r.from_table, r.from_column, r.to_table) for r in profile.relationships if r.status == "accepted"}
    assert {
        ("orders", "customer_id", "customers"),
        ("order_items", "order_id", "orders"),
        ("order_items", "product_id", "products"),
        ("products", "supplier_id", "suppliers"),
        ("payment", "order_id", "orders"),
        ("shipments", "order_id", "orders"),
        ("reviews", "customer_id", "customers"),
        ("reviews", "product_id", "products"),
    } <= links
