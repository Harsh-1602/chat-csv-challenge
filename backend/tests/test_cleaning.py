from pathlib import Path

import duckdb
import pytest

from app.cleaning import DataCleaner, data_notes
from app.profiling import HEURISTIC_STRATEGIES, NullSink, ProfilingPipeline, SourceFile
from app.schemas import Metric, SemanticModel

DIRTY = (
    "id,region,amount,sold_on,zip\n"
    "1, North ,$1200,2024-01-05,02134\n"
    "2,north,N/A,01/07/2024,02135\n"
    "3,South,300,2024/01/09,10001\n"
    "4,south,\"1,500\",2024-01-10,10002\n"
    "4,south,\"1,500\",2024-01-10,10002\n"
    "5,North,250.5,n/a,10003\n"
)


@pytest.fixture()
def dataset(tmp_path: Path):
    path = tmp_path / "sales.csv"
    path.write_text(DIRTY)
    db = tmp_path / "db.duckdb"
    profile = ProfilingPipeline(HEURISTIC_STRATEGIES).run(
        db, [SourceFile("sales.csv", "sales", path, path.stat().st_size)], NullSink()
    )
    return db, profile


def test_detects_fixable_and_informational_issues(dataset) -> None:
    db, profile = dataset
    issues = {i.id: i for i in DataCleaner().detect(db, profile)}
    assert {
        "sales.duplicate_rows", "sales.region.whitespace", "sales.region.case_variants",
        "sales.amount.null_tokens", "sales.amount.numeric_text",
        "sales.sold_on.null_tokens", "sales.sold_on.date_text",
    } <= set(issues)
    assert "sales.zip.numeric_text" not in issues  # leading zeros: a code, not a number
    assert issues["sales.duplicate_rows"].affected_rows == 1
    assert "'N/A'" in issues["sales.amount.null_tokens"].examples
    assert all(i.fix for i in issues.values() if i.kind not in ("missing_values", "outliers"))


def test_apply_rebuilds_tables_and_refreshes_profile(dataset) -> None:
    db, profile = dataset
    cleaner = DataCleaner()
    issues = cleaner.detect(db, profile)
    result = cleaner.apply(db, profile, issues, [i.id for i in issues if i.fix])

    table = result.profile.tables[0]
    columns = {c.name: c for c in table.columns}
    assert table.row_count == 5 and table.duplicate_row_count == 0
    assert table.primary_key == ["id"]
    assert columns["amount"].data_type == "DOUBLE" and columns["amount"].role == "measure"
    assert columns["sold_on"].data_type == "DATE" and columns["sold_on"].role == "time"
    assert columns["zip"].data_type == "VARCHAR"
    assert len(result.applied) == 7
    assert all(i.fix is None for i in result.issues)

    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute("SELECT region, amount, CAST(sold_on AS VARCHAR) FROM sales ORDER BY id").fetchall()
    assert rows == [
        ("North", 1200.0, "2024-01-05"), ("North", None, "2024-01-07"), ("south", 300.0, "2024-01-09"),
        ("south", 1500.0, "2024-01-10"), ("North", 250.5, None),
    ]
    assert con.execute("SELECT count(*) FROM sales__raw").fetchone()[0] == 6
    con.close()


def test_apply_only_selected_fixes(dataset) -> None:
    db, profile = dataset
    cleaner = DataCleaner()
    issues = cleaner.detect(db, profile)
    result = cleaner.apply(db, profile, issues, ["sales.duplicate_rows"])
    assert result.profile.tables[0].row_count == 5
    remaining = {i.id for i in result.issues}
    assert "sales.duplicate_rows" not in remaining and "sales.amount.numeric_text" in remaining


def test_nothing_selected_leaves_data_untouched(dataset) -> None:
    db, profile = dataset
    cleaner = DataCleaner()
    issues = cleaner.detect(db, profile)
    result = cleaner.apply(db, profile, issues, [])
    assert result.applied == [] and result.profile is profile


def test_cleaning_revalidates_metrics_and_records_fixes(dataset) -> None:
    db, profile = dataset
    raw_metric = Metric(name="revenue", table="sales", description="Total sales",
                        expression="SUM(TRY_CAST(REPLACE(REPLACE(amount, '$', ''), ',', '') AS DOUBLE))", valid=True)
    plain_metric = Metric(name="orders", table="sales", description="Orders", expression="COUNT(*)", valid=True)
    profile = profile.model_copy(update={"semantic_model": SemanticModel(
        dataset_summary="Sales", synonyms=[], example_questions=[],
        metrics=[raw_metric, plain_metric], caveats=["Region casing is inconsistent."])})
    cleaner = DataCleaner()
    issues = cleaner.detect(db, profile)
    result = cleaner.apply(db, profile, issues, [i.id for i in issues if i.fix])

    semantic = result.profile.semantic_model
    assert [m.valid for m in semantic.metrics] == [False, True]  # REPLACE no longer works on a DOUBLE column
    assert semantic.caveats[0].startswith("Data cleaning was already applied")
    assert semantic.caveats[1] == "Region casing is inconsistent."


def test_data_notes_surface_for_queries_touching_the_column(dataset) -> None:
    db, profile = dataset
    notes = data_notes(DataCleaner().detect(db, profile))
    relevant = notes.relevant("SELECT sum(amount) FROM sales")
    assert any("amount" in n for n in relevant)
    assert not any("region:" in n for n in relevant)
