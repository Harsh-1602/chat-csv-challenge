"""Experiment 01: LLM-assisted profiling. SQL gathers evidence, the LLM judges, SQL verifies.

Phases: P1 stats + samples (SQL) -> P2 table understanding (LLM, per table, parallel) + PK verification (SQL)
-> P3 relationship candidates (SQL) -> P4 relationship decisions (LLM, one call) + verification (SQL)
-> P5 cardinality / fan-out / quality (SQL) -> P6 semantic model (LLM) + expression validation (SQL).
"""

from __future__ import annotations

import itertools
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from typing import Any, Literal

import duckdb
from pydantic import BaseModel

PROMPT_VERSION = "exp01-v1"
SAMPLE_ROWS = 12
MAX_STR = 60
ACCEPT_CONTAINMENT = 0.95
CANDIDATE_CONTAINMENT = 0.5
NAME_SIM_STRONG = 0.6
NAME_SIM_CONTAINMENT = 0.2
MAX_COMPOSITE = 3

NUMERIC = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER",
           "UBIGINT", "FLOAT", "DOUBLE", "REAL", "DECIMAL"}
TEMPORAL = {"DATE", "TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIME"}


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def base_type(t: str) -> str:
    return t.upper().split("(")[0].strip()


def family(t: str) -> str:
    b = base_type(t)
    return "number" if b in NUMERIC else "time" if b in TEMPORAL else "bool" if b == "BOOLEAN" else "string"


def short(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, bool)):
        return v
    s = str(v)
    return s if len(s) <= MAX_STR else s[:MAX_STR] + "…"


# ---------------------------------------------------------------- P1: statistics + samples (SQL)

def p1_stats(con: duckdb.DuckDBPyConnection, table: str) -> dict:
    cols = [(r[0], r[1]) for r in con.execute(f"DESCRIBE {q(table)}").fetchall()]
    rows = con.execute(f"SELECT count(*) FROM {q(table)}").fetchone()[0]
    parts = []
    for i, (c, t) in enumerate(cols):
        parts += [f"count({q(c)}) AS nn{i}", f"count(DISTINCT {q(c)}) AS d{i}"]
        if family(t) in ("number", "time"):
            parts += [f"min({q(c)}) AS mn{i}", f"max({q(c)}) AS mx{i}"]
        if family(t) == "number":
            parts += [f"avg({q(c)}::DOUBLE) AS av{i}",
                      f"count(*) FILTER (WHERE {q(c)} <> round({q(c)}::DOUBLE)) AS frac{i}"]
        if family(t) == "string":
            parts += [f"avg(length({q(c)})) AS len{i}",
                      f"count(TRY_CAST({q(c)} AS DOUBLE)) AS cnum{i}",
                      f"count(TRY_CAST({q(c)} AS DATE)) AS cdate{i}"]
    cur = con.execute(f"SELECT {', '.join(parts)} FROM {q(table)}")
    s = dict(zip([d[0] for d in cur.description], cur.fetchone()))

    columns = []
    for i, (c, t) in enumerate(cols):
        nn, d = s[f"nn{i}"], s[f"d{i}"]
        col: dict[str, Any] = {
            "name": c, "type": t, "null_pct": round((rows - nn) / rows * 100, 2) if rows else 0,
            "distinct": d, "unique": rows > 0 and nn == rows and d == rows,
        }
        if f"mn{i}" in s:
            col["min"], col["max"] = short(s[f"mn{i}"]), short(s[f"mx{i}"])
        if f"av{i}" in s and s[f"av{i}"] is not None:
            col["mean"] = round(float(s[f"av{i}"]), 3)
            col["has_fractional_values"] = bool(s[f"frac{i}"])
        if f"len{i}" in s and s[f"len{i}"] is not None:
            col["avg_length"] = round(float(s[f"len{i}"]), 1)
            if nn and s[f"cnum{i}"] / nn >= 0.98:
                col["castable_to"] = "number"
            elif nn and s[f"cdate{i}"] / nn >= 0.98:
                col["castable_to"] = "date"
        if d <= 12 and d < rows:
            top = con.execute(
                f"SELECT {q(c)}, count(*) n FROM {q(table)} WHERE {q(c)} IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 6"
            ).fetchall()
            col["top_values"] = [[short(v), n] for v, n in top]
        columns.append(col)

    sample = con.execute(f"SELECT * FROM {q(table)} USING SAMPLE {SAMPLE_ROWS} ROWS (reservoir, 42)").fetchall()
    return {"table": table, "rows": rows, "columns": columns,
            "sample_rows": [[short(v) for v in r] for r in sample]}


# ---------------------------------------------------------------- P2: table understanding (LLM)

Role = Literal["identifier", "measure", "time", "dimension", "text", "boolean"]


class ColumnUnderstanding(BaseModel):
    name: str
    role: Role
    semantic_type: str
    description: str
    unit: str | None
    references_entity: str | None


class TableUnderstanding(BaseModel):
    entity: str
    description: str
    grain: str
    primary_key: list[str]
    columns: list[ColumnUnderstanding]


P2_SYSTEM = """You are a meticulous data profiler. You receive statistics and sample rows for ONE table from a set of CSV files a user uploaded together, plus the names and columns of the other tables in the set.

Return:
- entity: the business entity one row represents (singular noun).
- description: one sentence describing the table.
- grain: "one row per ..." statement.
- primary_key: the minimal column set that uniquely identifies a row. Prefer a single surrogate id column when one is unique. If no single column is unique (see the `unique` flag), choose the smallest combination of columns that should be unique (e.g. a bridge table keyed by two foreign keys, or an order id plus a line number).
- columns: one entry per column, same names, same order, with:
  - role:
    - identifier: a key or code that identifies this row or references a row of another table (ids, *_key, foreign keys, self references such as manager id). Never aggregated.
    - measure: a numeric quantity it makes sense to sum or average (money, prices, quantities, durations, sizes, balances, rates).
    - time: a date or timestamp.
    - dimension: a low-cardinality attribute used for grouping or filtering (status, type, country, category, flags encoded as codes, small ordinal codes such as priority).
    - text: free text or high-cardinality descriptive values (names, titles, addresses, phone numbers, emails, comments).
    - boolean: true/false values.
  - semantic_type: a short specific type such as "email", "country", "currency amount", "duration", "foreign key".
  - description: what the column means.
  - unit: unit of a measure when it can be inferred (e.g. "USD", "cents", "milliseconds", "bytes", "fraction 0-1", "count"); null otherwise. Check whether money is stored in minor units: integer-only money values that are about 100x larger than plausible prices for the domain suggest cents.
  - references_entity: for identifiers that point to another table, the entity they reference (e.g. "customer"); null otherwise.
Base every judgement on the statistics and samples; do not invent columns."""


def p2_prompt(stats: dict, others: dict[str, list[str]]) -> str:
    return json.dumps({
        "table": stats["table"], "row_count": stats["rows"], "columns": stats["columns"],
        "sample_rows": {"columns": [c["name"] for c in stats["columns"]], "rows": stats["sample_rows"]},
        "other_tables": others,
    }, default=str)


def is_unique(con: duckdb.DuckDBPyConnection, table: str, cols: list[str], rows: int) -> bool:
    if not cols:
        return False
    cl = ", ".join(q(c) for c in cols)
    nulls = " OR ".join(f"{q(c)} IS NULL" for c in cols)
    distinct, null_rows = con.execute(
        f"SELECT (SELECT count(*) FROM (SELECT DISTINCT {cl} FROM {q(table)})), "
        f"(SELECT count(*) FROM {q(table)} WHERE {nulls})"
    ).fetchone()
    return distinct == rows and null_rows == 0


def verify_pk(con, stats: dict, proposed: list[str], roles: dict[str, str]) -> tuple[list[str] | None, str]:
    names = [c["name"] for c in stats["columns"]]
    lookup = {n.lower(): n for n in names}
    proposed = [lookup[p.lower()] for p in proposed if p.lower() in lookup]
    rows = stats["rows"]
    if proposed and is_unique(con, stats["table"], proposed, rows):
        # Reduce to a minimal subset if the model over-specified the key.
        for size in range(1, len(proposed)):
            for combo in itertools.combinations(proposed, size):
                if is_unique(con, stats["table"], list(combo), rows):
                    return list(combo), "llm_minimised"
        return proposed, "llm_verified"
    singles = [c["name"] for c in stats["columns"] if c["unique"] and family(c["type"]) != "time"]
    singles.sort(key=lambda n: (roles.get(n) != "identifier", n))
    if singles:
        return [singles[0]], "sql_fallback"
    pool = [n for n in names if roles.get(n) == "identifier"] or names
    for size in range(2, MAX_COMPOSITE + 1):
        for combo in itertools.combinations(pool, size):
            if is_unique(con, stats["table"], list(combo), rows):
                return list(combo), "sql_fallback"
    return None, "none"


# ---------------------------------------------------------------- P3: relationship candidates (SQL)

def _core(name: str) -> str:
    n = name.lower()
    n = re.sub(r"^[a-z]{1,2}_", "", n)  # table prefixes such as c_, ps_
    n = re.sub(r"[^a-z0-9]", "", n)
    n = re.sub(r"(id|key|code|no|num)$", "", n)
    return n


def _singular(name: str) -> str:
    n = re.sub(r"^(raw|stg|dim|fct|fact)_", "", name.lower())
    return n[:-3] + "y" if n.endswith("ies") else n[:-1] if n.endswith("s") and not n.endswith("ss") else n


def name_sim(child_col: str, parent_table: str, parent_col: str) -> float:
    c = _core(child_col)
    if not c:
        return 0.0
    targets = [t for t in (_core(parent_col), re.sub(r"[^a-z0-9]", "", _singular(parent_table))) if t]
    return round(max((SequenceMatcher(None, c, t).ratio() for t in targets), default=0.0), 3)


def containment(con, child: str, ccol: str, parent: str, pcol: str) -> dict:
    distinct, matched, rows, orphan_rows = con.execute(
        f"SELECT count(DISTINCT c.v), count(DISTINCT c.v) FILTER (WHERE p.k IS NOT NULL), count(c.v), "
        f"count(c.v) FILTER (WHERE p.k IS NULL) "
        f"FROM (SELECT {q(ccol)} AS v FROM {q(child)}) c "
        f"LEFT JOIN (SELECT DISTINCT {q(pcol)} AS k FROM {q(parent)}) p ON c.v = p.k"
    ).fetchone()
    parent_distinct = con.execute(f"SELECT count(DISTINCT {q(pcol)}) FROM {q(parent)}").fetchone()[0]
    return {
        "containment": round(matched / distinct, 4) if distinct else 0.0,
        "child_distinct": distinct, "child_non_null_rows": rows, "orphan_rows": orphan_rows,
        "parent_coverage": round(matched / parent_distinct, 4) if parent_distinct else 0.0,
    }


def p3_candidates(con, stats: dict[str, dict], tables_info: dict[str, dict]) -> list[dict]:
    candidates = []
    for parent, pinfo in tables_info.items():
        pk = pinfo["primary_key"]
        if not pk or len(pk) != 1:
            continue
        pcol = pk[0]
        ptype = next(c["type"] for c in stats[parent]["columns"] if c["name"] == pcol)
        for child, cinfo in tables_info.items():
            for col in stats[child]["columns"]:
                ccol = col["name"]
                if child == parent and ccol == pcol:
                    continue
                if family(col["type"]) != family(ptype) or family(ptype) not in ("number", "string"):
                    continue
                sim = name_sim(ccol, parent, pcol)
                if cinfo["roles"].get(ccol) != "identifier" and sim < NAME_SIM_STRONG:
                    continue
                child_pk = cinfo["primary_key"] or []
                if child_pk == [ccol] and ccol.lower() != pcol.lower():
                    continue  # pk -> pk links with different names are almost always coincidental
                ev = containment(con, child, ccol, parent, pcol)
                if ev["containment"] >= CANDIDATE_CONTAINMENT or (sim >= NAME_SIM_STRONG and ev["containment"] >= NAME_SIM_CONTAINMENT):
                    candidates.append({
                        "id": len(candidates), "from": f"{child}.{ccol}", "to": f"{parent}.{pcol}",
                        "name_similarity": sim, "child_column_unique": col["unique"], **ev,
                    })
    return candidates


# ---------------------------------------------------------------- P4: relationship decisions (LLM)

class LinkDecision(BaseModel):
    candidate_id: int
    decision: Literal["accept", "reject", "uncertain"]
    reason: str


class ProposedLink(BaseModel):
    from_column: str
    to_column: str
    reason: str


class RelationshipReview(BaseModel):
    decisions: list[LinkDecision]
    missing_links: list[ProposedLink]


P4_SYSTEM = """You decide which foreign-key relationships exist between tables a user uploaded together.

You receive each table's entity, grain, primary key and column roles/descriptions, and a list of candidate links found by SQL. Each candidate links a child column to a parent primary key with evidence:
- containment: share of distinct child values found in the parent key (1.0 = every value exists).
- parent_coverage: share of parent keys that the child references.
- name_similarity: 0-1 similarity of the child column name to the parent key / table name.
- child_column_unique: whether the child column is unique (one-to-one).

Containment alone is weak evidence: small integer ranges (1..N) are contained in many unrelated keys. Accept a candidate only if the child column semantically refers to the parent entity (by name, description, or the entity it references, e.g. user_id -> customers, ReportsTo -> employee). Each child column normally references at most one parent: when one column has several candidates, accept only the semantically right one. Reject links between measures, line numbers, counts, or codes that only coincidentally overlap. Use "uncertain" when evidence conflicts.

For every candidate return a decision with a short reason. In missing_links, list any relationship you believe exists but is absent from the candidates, as "table.column" -> "table.column" (parent must be a key)."""


def p4_prompt(tables_info: dict[str, dict], candidates: list[dict]) -> str:
    tables = {
        t: {"entity": i["entity"], "grain": i["grain"], "primary_key": i["primary_key"],
            "columns": {c: f"{i['roles'][c]}: {i['descriptions'].get(c, '')}" for c in i["roles"]}}
        for t, i in tables_info.items()
    }
    return json.dumps({"tables": tables, "candidates": candidates}, default=str)


def _resolve(ref: str, stats: dict[str, dict]) -> tuple[str, str] | None:
    if "." not in ref:
        return None
    t, c = ref.split(".", 1)
    tmap = {k.lower(): k for k in stats}
    if t.lower() not in tmap:
        return None
    t = tmap[t.lower()]
    cmap = {x["name"].lower(): x["name"] for x in stats[t]["columns"]}
    return (t, cmap[c.lower()]) if c.lower() in cmap else None


def p4_verify(con, review: RelationshipReview, candidates: list[dict], stats: dict, tables_info: dict) -> list[dict]:
    by_id = {c["id"]: c for c in candidates}
    links = []
    for d in review.decisions:
        cand = by_id.get(d.candidate_id)
        if not cand or d.decision == "reject":
            continue
        status = "accepted" if d.decision == "accept" and cand["containment"] >= ACCEPT_CONTAINMENT else "needs_review"
        links.append({**cand, "status": status, "source": "candidate", "llm_decision": d.decision, "reason": d.reason})

    for m in review.missing_links:
        src, dst = _resolve(m.from_column, stats), _resolve(m.to_column, stats)
        if not src or not dst or f"{src[0]}.{src[1]}" in {x["from"] for x in links}:
            continue
        parent_unique = is_unique(con, dst[0], [dst[1]], stats[dst[0]]["rows"])
        try:
            ev = containment(con, src[0], src[1], dst[0], dst[1])
        except duckdb.Error:
            continue
        status = "accepted" if parent_unique and ev["containment"] >= ACCEPT_CONTAINMENT else "needs_review"
        links.append({"from": f"{src[0]}.{src[1]}", "to": f"{dst[0]}.{dst[1]}", **ev, "status": status,
                      "source": "llm_proposed", "llm_decision": "accept", "reason": m.reason,
                      "name_similarity": name_sim(src[1], dst[0], dst[1])})

    # One child column should reference one parent; ambiguity goes to review.
    accepted_from: dict[str, int] = {}
    for link in links:
        if link["status"] == "accepted":
            accepted_from[link["from"]] = accepted_from.get(link["from"], 0) + 1
    for link in links:
        if link["status"] == "accepted" and accepted_from[link["from"]] > 1:
            link["status"] = "needs_review"
            link["reason"] += " [demoted: column accepted for several parents]"
    return links


# ---------------------------------------------------------------- P5: cardinality, fan-out, quality (SQL)

def p5_structure(con, links: list[dict], stats: dict) -> None:
    for link in links:
        child, ccol = link["from"].split(".", 1)
        rows, distinct = con.execute(
            f"SELECT count({q(ccol)}), count(DISTINCT {q(ccol)}) FROM {q(child)}"
        ).fetchone()
        link["cardinality"] = "one-to-one" if rows == distinct else "many-to-one"
        link["fan_out"] = round(rows / distinct, 3) if distinct else None
        if link["fan_out"] and link["fan_out"] > 1:
            link["join_warning"] = (f"Joining {child} to {link['to'].split('.')[0]} repeats each "
                                    f"{link['to'].split('.')[0]} row ~{link['fan_out']}x; aggregate "
                                    f"{link['to'].split('.')[0]} measures before joining.")


# ---------------------------------------------------------------- P6: semantic model (LLM)

class Metric(BaseModel):
    name: str
    table: str
    expression: str
    description: str


class Synonym(BaseModel):
    term: str
    maps_to: str


class SemanticModel(BaseModel):
    dataset_summary: str
    metrics: list[Metric]
    synonyms: list[Synonym]
    caveats: list[str]
    example_questions: list[str]


P6_SYSTEM = """You write a compact semantic model that a text-to-SQL agent (DuckDB dialect) will use to answer questions about this dataset.

Return:
- dataset_summary: 2-3 sentences on what the data covers and how tables connect.
- metrics: 4-10 key business metrics. expression must be a DuckDB aggregate expression over columns of exactly one table (e.g. "SUM(amount) / 100.0", "COUNT(DISTINCT customer_id)"), using quoted identifiers if names have capitals. Apply unit conversions (e.g. cents to dollars) in the expression.
- synonyms: business terms users may say and the table.column or metric they map to.
- caveats: pitfalls an analyst must know (units, join fan-out / double counting, nullable keys, self references, derived totals, date ranges).
- example_questions: 5 questions this dataset can answer."""


def p6_prompt(tables_info: dict, links: list[dict], stats: dict) -> str:
    tables = {}
    for t, i in tables_info.items():
        cols = {}
        for c in stats[t]["columns"]:
            cols[c["name"]] = {k: v for k, v in {
                "type": c["type"], "role": i["roles"][c["name"]], "unit": i["units"].get(c["name"]),
                "description": i["descriptions"].get(c["name"]), "min": c.get("min"), "max": c.get("max"),
                "null_pct": c["null_pct"] or None}.items() if v is not None}
        tables[t] = {"entity": i["entity"], "grain": i["grain"], "rows": stats[t]["rows"],
                     "primary_key": i["primary_key"], "columns": cols}
    rels = [{k: l[k] for k in ("from", "to", "cardinality", "fan_out", "status")} for l in links]
    return json.dumps({"tables": tables, "relationships": rels}, default=str)


def p6_validate(con, model: SemanticModel) -> list[dict]:
    out = []
    for m in model.metrics:
        try:
            con.execute(f"SELECT {m.expression} FROM {q(m.table)}").fetchone()
            out.append({**m.model_dump(), "valid": True})
        except duckdb.Error as exc:
            out.append({**m.model_dump(), "valid": False, "error": str(exc).splitlines()[0][:200]})
    return out


# ---------------------------------------------------------------- orchestration

def profile(con: duckdb.DuckDBPyConnection, tables: dict[str, str], llm) -> dict:
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    stats = {t: p1_stats(con, t) for t in tables}
    timings["p1_stats"] = round(time.perf_counter() - t0, 2)

    t0 = time.perf_counter()
    others = {t: [c["name"] for c in s["columns"]] for t, s in stats.items()}

    def understand(t: str) -> TableUnderstanding:
        ctx = {k: v for k, v in others.items() if k != t}
        return llm.parse(f"p2:{t}", P2_SYSTEM, p2_prompt(stats[t], ctx), TableUnderstanding, PROMPT_VERSION)

    with ThreadPoolExecutor(max_workers=8) as pool:
        understood = dict(zip(stats, pool.map(understand, stats)))
    timings["p2_llm"] = round(time.perf_counter() - t0, 2)

    t0 = time.perf_counter()
    tables_info: dict[str, dict] = {}
    for t, u in understood.items():
        by_name = {c.name.lower(): c for c in u.columns}
        roles, units, descriptions, refs = {}, {}, {}, {}
        for c in stats[t]["columns"]:
            cu = by_name.get(c["name"].lower())
            roles[c["name"]] = cu.role if cu else ("measure" if family(c["type"]) == "number" else "text")
            units[c["name"]] = cu.unit if cu else None
            descriptions[c["name"]] = cu.description if cu else ""
            refs[c["name"]] = cu.references_entity if cu else None
        pk, pk_source = verify_pk(con, stats[t], u.primary_key, roles)
        tables_info[t] = {"entity": u.entity, "description": u.description, "grain": u.grain,
                          "llm_primary_key": u.primary_key, "primary_key": pk, "pk_source": pk_source,
                          "roles": roles, "units": units, "descriptions": descriptions, "references": refs}
    timings["p2_verify"] = round(time.perf_counter() - t0, 2)

    t0 = time.perf_counter()
    candidates = p3_candidates(con, stats, tables_info)
    timings["p3_candidates"] = round(time.perf_counter() - t0, 2)

    t0 = time.perf_counter()
    review = llm.parse("p4:relationships", P4_SYSTEM, p4_prompt(tables_info, candidates), RelationshipReview, PROMPT_VERSION)
    links = p4_verify(con, review, candidates, stats, tables_info)
    timings["p4_llm_verify"] = round(time.perf_counter() - t0, 2)

    t0 = time.perf_counter()
    p5_structure(con, links, stats)
    timings["p5_structure"] = round(time.perf_counter() - t0, 2)

    t0 = time.perf_counter()
    model = llm.parse("p6:semantic", P6_SYSTEM, p6_prompt(tables_info, links, stats), SemanticModel, PROMPT_VERSION)
    metrics = p6_validate(con, model)
    timings["p6_llm_validate"] = round(time.perf_counter() - t0, 2)

    return {
        "tables": {t: {k: v for k, v in i.items()} for t, i in tables_info.items()},
        "relationships": links,
        "rejected_candidates": [c for c in candidates if c["id"] not in {l.get("id") for l in links}],
        "semantic_model": {**model.model_dump(), "metrics": metrics},
        "timings_s": timings,
    }
