"""Shared harness for chat experiments: profile each eval dataset once, run a chat variant over the gold
questions (follow-ups use the variant's own earlier answers as history), score and write results."""

from __future__ import annotations

import json
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.chat.agent import ChatAnswer  # noqa: E402
from app.chat.executor import QueryExecutor, QueryResult  # noqa: E402
from app.chat.factory import ChatAgentFactory, ChatConfig  # noqa: E402
from app.chat.memory import Turn  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.llm import build_llm_client  # noqa: E402
from app.profiling import NullSink, ProfilingPipelineFactory, SourceFile, safe_table_name  # noqa: E402
from app.schemas import DatasetProfile  # noqa: E402

DATASETS_DIR = ROOT / "eval" / "datasets"
QUESTIONS_DIR = ROOT / "eval" / "questions"
WORK_DIR = ROOT / "experiments" / "chat" / ".work"
DATASETS = ["jaffle_shop", "chinook", "tpch"]
SQL_TYPES = {"scalar", "table", "chart"}
GOLD_MAX_ROWS = 5000
DEFAULT_TOLERANCE = 0.01
PARALLEL_QUESTIONS = 6


# ---------------------------------------------------------------- data


def profiled_dataset(dataset: str, client) -> tuple[Path, DatasetProfile]:
    """Profile with the app pipeline (LLM on) once; cache the DuckDB file and profile JSON."""
    work = WORK_DIR / dataset
    db_path, profile_path = work / "dataset.duckdb", work / "profile.json"
    if db_path.exists() and profile_path.exists():
        return db_path, DatasetProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))
    work.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    existing: set[str] = set()
    sources = [
        SourceFile(p.name, safe_table_name(p.name, existing), p, p.stat().st_size)
        for p in sorted((DATASETS_DIR / dataset).glob("*.csv"))
    ]
    profile = ProfilingPipelineFactory(load_settings().llm, client).create().run(db_path, sources, NullSink())
    profile_path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    return db_path, profile


def load_questions(dataset: str) -> dict:
    return json.loads((QUESTIONS_DIR / f"{dataset}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- scoring

_TIME_SUFFIX = re.compile(r"(T| )00:00:00(\.0+)?$")


def _norm(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return float(value)
    text = _TIME_SUFFIX.sub("", str(value).strip())
    try:
        return float(text)
    except ValueError:
        return text


def _equal(a: Any, b: Any, tol: float) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b)
        return abs(a - b) <= max(tol, 1e-9 * max(abs(a), abs(b)))
    return a == b


def _sort_key(v: Any) -> tuple:
    if v is None:
        return (0, "")
    if isinstance(v, float):
        return (1, round(v, 2))
    return (2, str(v))


def _scaled(values: list, k: float) -> list:
    return [v * k if isinstance(v, float) else v for v in values]


def _vectors_match(gold: list, pred: list, ordered: bool, tol: float) -> float | None:
    """Scale factor applied to pred that makes the columns equal, or None. Percent vs fraction
    (62.5 vs 0.625) is a presentation choice, not a different answer, so x100 / x0.01 are accepted."""
    if len(gold) != len(pred):
        return None
    numeric = all(isinstance(v, float) for v in gold + pred if v is not None)
    for k in (1.0, 100.0, 0.01) if numeric else (1.0,):
        g, p = gold, _scaled(pred, k)
        if not ordered:
            g, p = sorted(g, key=_sort_key), sorted(p, key=_sort_key)
        if all(_equal(a, b, tol) for a, b in zip(g, p)):
            return k
    return None


def execution_match(gold: QueryResult, pred: QueryResult, ordered: bool, tol: float) -> tuple[bool, float]:
    """Each gold column must map to a distinct predicted column with the same values (extra columns allowed),
    then rows must agree (in order when the question fixes an order). Returns (match, column recall)."""
    if gold.row_count != pred.row_count:
        return False, 0.0
    g_cols = [[_norm(r[i]) for r in gold.rows] for i in range(len(gold.columns))]
    p_cols = [[_norm(r[i]) for r in pred.rows] for i in range(len(pred.columns))]
    mapping: list[tuple[int, float]] = []
    used: set[int] = set()
    for g in g_cols:
        for j, p in enumerate(p_cols):
            k = None if j in used else _vectors_match(g, p, ordered, tol)
            if k is not None:
                used.add(j)
                mapping.append((j, k))
                break
    recall = len(mapping) / max(1, len(g_cols))
    if len(mapping) != len(g_cols):
        return False, recall
    g_rows = [[_norm(v) for v in r] for r in gold.rows]
    p_rows = [[_scaled([_norm(r[j])], k)[0] for j, k in mapping] for r in pred.rows]
    if not ordered:
        g_rows = sorted(g_rows, key=lambda r: [_sort_key(v) for v in r])
        p_rows = sorted(p_rows, key=lambda r: [_sort_key(v) for v in r])
    ok = all(all(_equal(a, b, tol) for a, b in zip(gr, pr)) for gr, pr in zip(g_rows, p_rows))
    return ok, recall


_CHART_FAMILY = {"bar": "bar", "horizontal_bar": "bar", "stacked_bar": "bar", "line": "line", "area": "line"}


def score(question: dict, answer: ChatAnswer, gold: QueryResult | None) -> dict:
    expected = question["expected_type"]
    out: dict[str, Any] = {"status": answer.status}
    if expected == "clarification":
        out["pass"] = answer.status == "clarify" or (answer.status == "answered" and bool(answer.assumptions))
    elif expected == "refusal":
        out["pass"] = answer.status == "refuse"
    else:
        ok, recall = (False, 0.0)
        if answer.status == "answered" and answer.result is not None and gold is not None:
            ok, recall = execution_match(
                gold, answer.result, bool(question.get("order_matters")), question.get("tolerance", DEFAULT_TOLERANCE)
            )
        out.update({"pass": ok, "column_recall": round(recall, 3)})
        if expected == "chart":
            p = answer.presentation
            accept = question.get("chart_accept") or [question["chart"]["type"]]
            wants = {_CHART_FAMILY.get(t, t) for t in accept}
            out["chart_ok"] = bool(p and p.kind == "chart" and _CHART_FAMILY.get(p.chart_type or "", p.chart_type) in wants)
    return out


# ---------------------------------------------------------------- running


def _answer_record(answer: ChatAnswer) -> dict:
    return {
        "status": answer.status,
        "standalone_question": answer.standalone_question,
        "message": answer.message,
        "sql": answer.sql,
        "assumptions": answer.assumptions,
        "warnings": answer.warnings,
        "notes": answer.notes,
        "presentation": answer.presentation.model_dump() if answer.presentation else None,
        "result_preview": answer.result.preview(10) if answer.result else None,
        "steps": [
            {"index": s.index, "action": s.action, "outcome": s.outcome, "sql": s.sql, "detail": s.detail[:600]}
            for s in answer.steps
        ],
    }


def run_dataset(dataset: str, config: ChatConfig, client) -> list[dict]:
    db_path, profile = profiled_dataset(dataset, client)
    spec = load_questions(dataset)
    executor = QueryExecutor(db_path, max_rows=GOLD_MAX_ROWS, timeout_s=config.timeout_s)
    factory = ChatAgentFactory(client, config)
    answers: dict[str, ChatAnswer] = {}
    by_id = {q["id"]: q for q in spec["questions"]}

    def ask(q: dict) -> dict:
        history = [answers[c].to_turn() for c in q.get("context", []) if c in answers]
        session = factory.create(profile, db_path, executor=executor)
        started = time.perf_counter()
        try:
            answer = session.agent.ask(q["question"], history)
        except Exception as exc:  # keep the run going; record the crash as a failure
            answer = ChatAnswer("failed", q["question"], f"crash: {type(exc).__name__}: {exc}")
        latency = time.perf_counter() - started
        answers[q["id"]] = answer
        gold = executor.run(q["gold_sql"]) if q.get("gold_sql") and q["expected_type"] in SQL_TYPES else None
        return {
            "id": q["id"], "dataset": dataset, "question": q["question"], "category": q["category"],
            "expected_type": q["expected_type"], "gold_sql": q.get("gold_sql"),
            "gold_preview": gold.preview(10) if gold else None,
            "score": score(q, answer, gold),
            "answer": _answer_record(answer),
            "latency_s": round(latency, 2), "llm_calls": len(session.llm.calls),
            "prompt_tokens": session.llm.prompt_tokens, "completion_tokens": session.llm.completion_tokens,
        }

    try:
        first = [q for q in spec["questions"] if not q.get("context")]
        follow = [q for q in spec["questions"] if q.get("context")]
        with ThreadPoolExecutor(PARALLEL_QUESTIONS) as pool:
            records = list(pool.map(ask, first))
        records += [ask(q) for q in follow]
    finally:
        executor.close()
    return sorted(records, key=lambda r: list(by_id).index(r["id"]))


def summarize(records: list[dict]) -> dict:
    def rate(rows: list[dict], key: str = "pass") -> str:
        vals = [r["score"][key] for r in rows if key in r["score"]]
        return f"{sum(vals)}/{len(vals)}" if vals else "-"

    sql_rows = [r for r in records if r["expected_type"] in SQL_TYPES]
    behaviour = [r for r in records if r["expected_type"] not in SQL_TYPES]
    lat = sorted(r["latency_s"] for r in records)
    return {
        "execution_accuracy": rate(sql_rows),
        "behaviour": rate(behaviour),
        "chart_type": rate(sql_rows, "chart_ok"),
        "per_dataset": {d: rate([r for r in sql_rows if r["dataset"] == d]) for d in sorted({r["dataset"] for r in records})},
        "latency_p50_s": lat[len(lat) // 2] if lat else 0,
        "latency_max_s": lat[-1] if lat else 0,
        "llm_calls": sum(r["llm_calls"] for r in records),
        "prompt_tokens": sum(r["prompt_tokens"] for r in records),
        "completion_tokens": sum(r["completion_tokens"] for r in records),
    }


def run_variant(name: str, config: ChatConfig, out_dir: Path, datasets: list[str] | None = None) -> dict:
    settings = load_settings().llm
    client = build_llm_client(settings)
    if client is None:
        raise SystemExit("LLM is not configured (OPENAI_API_KEY / LLM_ENABLED).")
    records: list[dict] = []
    for dataset in datasets or DATASETS:
        print(f"[{name}] {dataset} ...", flush=True)
        records += run_dataset(dataset, config, client)
    summary = summarize(records)
    summary.update({"variant": name, "model": settings.model, "reasoning_effort": settings.reasoning_effort,
                    "config": config.__dict__, "run_at": datetime.now().isoformat(timespec="seconds")})
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps({"summary": summary, "records": records}, indent=2, default=str),
                                          encoding="utf-8")
    (out_dir / "summary.md").write_text(render_markdown(name, summary, records), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("execution_accuracy", "behaviour", "chart_type", "latency_p50_s")}))
    return summary


def render_markdown(name: str, summary: dict, records: list[dict]) -> str:
    lines = [
        f"# {name}", "",
        f"Model {summary['model']} (reasoning {summary['reasoning_effort']}), run {summary['run_at']}.", "",
        f"- Execution accuracy (SQL questions): **{summary['execution_accuracy']}** — {summary['per_dataset']}",
        f"- Clarify/refuse behaviour: **{summary['behaviour']}**",
        f"- Chart type matches: {summary['chart_type']}",
        f"- Latency p50 {summary['latency_p50_s']}s, max {summary['latency_max_s']}s; "
        f"{summary['llm_calls']} LLM calls, {summary['prompt_tokens']:,} prompt / {summary['completion_tokens']:,} completion tokens",
        "", "| id | type | pass | status | steps | latency | question |", "|---|---|---|---|---|---|---|",
    ]
    for r in records:
        s = r["score"]
        mark = "✅" if s["pass"] else "❌"
        if "chart_ok" in s:
            mark += " 📊" if s["chart_ok"] else " (no chart)"
        lines.append(f"| {r['id']} | {r['expected_type']} | {mark} | {s['status']} | {len(r['answer']['steps'])} "
                     f"| {r['latency_s']}s | {r['question']} |")
    return "\n".join(lines) + "\n"
