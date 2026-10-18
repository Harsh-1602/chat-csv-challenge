"""Shared harness for profiling experiments: load datasets, run a strategy, score it, write results."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "experiments" / "profiling"))

from baseline_v1.profiler_v1 import detect_relationships, load_csv, profile_table, safe_table_name  # noqa: E402

DATASETS_DIR = ROOT / "eval" / "datasets"
QUESTIONS_DIR = ROOT / "eval" / "questions"
GROUND_TRUTH = json.loads((Path(__file__).parent / "ground_truth.json").read_text(encoding="utf-8"))
DATASETS = ["jaffle_shop", "chinook", "tpch"]

# A strategy receives (connection, {table_name: file_name}, llm-or-None) and returns:
# {"tables": {table: {"primary_key": [cols] | None, "roles": {col: role}, "units": {col: unit | None}}},
#  "relationships": [{"from": "t.c", "to": "t.c", "status": "accepted" | "needs_review", ...}], ...extra}
Strategy = Callable[[duckdb.DuckDBPyConnection, dict[str, str], Any], dict]

DESCRIPTIVE_ROLES = {"dimension", "text", "categorical", "boolean", "descriptive", "category", "other"}


def load_dataset(dataset: str) -> tuple[duckdb.DuckDBPyConnection, dict[str, str]]:
    con = duckdb.connect()
    tables: dict[str, str] = {}
    existing: set[str] = set()
    for path in sorted((DATASETS_DIR / dataset).glob("*.csv")):
        name = safe_table_name(path.name, existing)
        load_csv(con, name, path)
        tables[name] = path.name
    return con, tables


def baseline_v1(con: duckdb.DuckDBPyConnection, tables: dict[str, str], llm: Any = None) -> dict:
    profiles = [profile_table(con, t, f, 0) for t, f in tables.items()]
    role_map = {"identifier": "identifier", "numeric": "measure", "datetime": "time"}
    return {
        "tables": {
            p.table_name: {
                "primary_key": [p.primary_key] if p.primary_key else None,
                "roles": {c.name: role_map.get(c.role, "descriptive") for c in p.columns},
                "units": {},
            }
            for p in profiles
        },
        "relationships": [
            {"from": f"{r.from_table}.{r.from_column}", "to": f"{r.to_table}.{r.to_column}",
             "cardinality": r.cardinality, "status": "accepted"}
            for r in detect_relationships(con, profiles)
        ],
    }


def _norm_role(role: str | None) -> str:
    role = (role or "").lower()
    if role in ("identifier", "measure", "time"):
        return role
    if role in ("datetime", "date", "timestamp"):
        return "time"
    return "descriptive"


def _rel_key(rel: dict) -> tuple[str, str]:
    return rel["from"].lower(), rel["to"].lower()


def score(dataset: str, output: dict) -> dict:
    gt = GROUND_TRUTH[dataset]
    questions = json.loads((QUESTIONS_DIR / f"{dataset}.json").read_text(encoding="utf-8"))
    true_rels = {_rel_key(r) for r in questions["relationships"]}

    accepted = {_rel_key(r) for r in output["relationships"] if r.get("status") == "accepted"}
    review = {_rel_key(r) for r in output["relationships"] if r.get("status") == "needs_review"}
    tp = accepted & true_rels
    precision = len(tp) / len(accepted) if accepted else 0.0
    recall = len(tp) / len(true_rels) if true_rels else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    pk_hits, pk_errors = 0, []
    for table, expected in gt["primary_keys"].items():
        got = output["tables"].get(table, {}).get("primary_key") or []
        if sorted(c.lower() for c in got) == sorted(c.lower() for c in expected):
            pk_hits += 1
        else:
            pk_errors.append({"table": table, "expected": expected, "got": got})

    labelled = {c: role for role, cols in gt["roles"].items() for c in cols}
    ambiguous = set(gt["ambiguous"])
    role_total, role_hits, role_errors = 0, 0, []
    for table, info in output["tables"].items():
        for column, role in info["roles"].items():
            ref = f"{table}.{column}"
            if ref in ambiguous:
                continue
            expected = labelled.get(ref, "descriptive")
            got = _norm_role(role)
            role_total += 1
            if got == expected:
                role_hits += 1
            else:
                role_errors.append({"column": ref, "expected": expected, "got": role})

    unit_hits, unit_errors = 0, []
    for ref, unit in gt["units"].items():
        table, column = ref.split(".", 1)
        got = (output["tables"].get(table, {}).get("units") or {}).get(column)
        if got and unit.lower() in got.lower():
            unit_hits += 1
        else:
            unit_errors.append({"column": ref, "expected": unit, "got": got})

    return {
        "relationships": {
            "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "true_positives": len(tp), "accepted": len(accepted), "expected": len(true_rels),
            "false_positives": sorted("->".join(k) for k in accepted - true_rels),
            "missed": sorted("->".join(k) for k in true_rels - accepted),
            "missed_but_in_review": sorted("->".join(k) for k in (true_rels - accepted) & review),
            "needs_review": len(review),
        },
        "primary_keys": {"accuracy": round(pk_hits / len(gt["primary_keys"]), 3), "hits": pk_hits,
                         "total": len(gt["primary_keys"]), "errors": pk_errors},
        "roles": {"accuracy": round(role_hits / role_total, 3) if role_total else 0.0, "hits": role_hits,
                  "total": role_total, "errors": role_errors},
        "units": {"hits": unit_hits, "total": len(gt["units"]), "errors": unit_errors},
    }


def run_experiment(name: str, strategy: Strategy, results_dir: Path, llm: Any = None,
                   datasets: list[str] | None = None) -> dict:
    results_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"experiment": name, "datasets": {}}
    for dataset in datasets or DATASETS:
        con, tables = load_dataset(dataset)
        calls_before = len(llm.calls) if llm else 0
        started = time.perf_counter()
        output = strategy(con, tables, llm)
        elapsed = round(time.perf_counter() - started, 2)
        con.close()
        metrics = score(dataset, output)
        metrics["latency_s"] = elapsed
        if llm:
            calls = llm.calls[calls_before:]
            metrics["llm"] = {
                "calls": len(calls),
                "prompt_tokens": sum(c.prompt_tokens for c in calls),
                "completion_tokens": sum(c.completion_tokens for c in calls),
                "cached_calls": sum(c.cached for c in calls),
            }
        summary["datasets"][dataset] = metrics
        (results_dir / f"{dataset}.profile.json").write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
        print(f"[{name}] {dataset}: rel F1={metrics['relationships']['f1']} "
              f"PK={metrics['primary_keys']['accuracy']} roles={metrics['roles']['accuracy']} "
              f"units={metrics['units']['hits']}/{metrics['units']['total']} t={elapsed}s")

    if llm:
        summary["llm_usage"] = llm.usage()
        with (results_dir / "llm_log.jsonl").open("w", encoding="utf-8") as fh:
            for entry in llm.log:
                fh.write(json.dumps(entry, default=str) + "\n")
    (results_dir / "metrics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (results_dir / "SUMMARY.md").write_text(render_summary(summary), encoding="utf-8")
    return summary


def render_summary(summary: dict) -> str:
    lines = [f"# {summary['experiment']}: metrics", "",
             "| Dataset | Rel P | Rel R | Rel F1 | Accepted | Needs review | PK acc | Role acc | Units | Latency (s) | LLM calls | Tokens in/out |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for ds, m in summary["datasets"].items():
        r, llm = m["relationships"], m.get("llm", {})
        lines.append(
            f"| {ds} | {r['precision']} | {r['recall']} | {r['f1']} | {r['accepted']}/{r['expected']} | {r['needs_review']} "
            f"| {m['primary_keys']['hits']}/{m['primary_keys']['total']} | {m['roles']['accuracy']} "
            f"| {m['units']['hits']}/{m['units']['total']} | {m['latency_s']} | {llm.get('calls', 0)} "
            f"| {llm.get('prompt_tokens', 0)}/{llm.get('completion_tokens', 0)} |"
        )
    if "llm_usage" in summary:
        u = summary["llm_usage"]
        cost = "unknown" if u["est_cost_usd"] is None else f"${u['est_cost_usd']}"
        effort = f" (reasoning effort {u['reasoning_effort']})" if u.get("reasoning_effort") else ""
        lines += ["", f"Model `{u['model']}`{effort}: {u['calls']} calls ({u['cached_calls']} cached, {u['failed_calls']} failed), "
                      f"{u['prompt_tokens']:,} input and {u['completion_tokens']:,} output tokens, estimated cost {cost}."]
    lines += ["", "## Errors", ""]
    for ds, m in summary["datasets"].items():
        r = m["relationships"]
        lines.append(f"### {ds}")
        lines.append(f"- False-positive links: {', '.join(r['false_positives']) or 'none'}")
        lines.append(f"- Missed links: {', '.join(r['missed']) or 'none'}"
                     + (f" (in review queue: {', '.join(r['missed_but_in_review'])})" if r["missed_but_in_review"] else ""))
        lines.append("- Primary-key errors: " + (", ".join(f"{e['table']} expected {e['expected']} got {e['got']}"
                                                         for e in m["primary_keys"]["errors"]) or "none"))
        lines.append("- Role errors: " + (", ".join(f"{e['column']} ({e['expected']}→{e['got']})"
                                                   for e in m["roles"]["errors"]) or "none"))
        lines.append("- Unit errors: " + (", ".join(f"{e['column']} expected {e['expected']} got {e['got']}"
                                                   for e in m["units"]["errors"]) or "none"))
        lines.append("")
    return "\n".join(lines)
