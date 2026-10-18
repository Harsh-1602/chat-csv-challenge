# Chat Agent Design

Research basis: [chat-agent-research.md](./chat-agent-research.md). Profiling design: [profiling-design.md](./profiling-design.md).

## Decisions

| Topic | Decision | Why |
|---|---|---|
| Orchestration | **Hybrid**: fixed outer pipeline + bounded agentic SQL loop (≤4 tool calls) | Predictable/auditable like a pipeline; execution-feedback repair is the strongest accuracy lever (MAC-SQL, CHESS, ReFoRCE) |
| Output | **Composable**: text always + optional table + optional chart | User may explicitly ask for a table or chart; otherwise chosen from result shape |
| Data cleaning | **User-confirmed** at ingestion: show detected issues, clean only if user accepts | User stays in control; unresolved issues are flagged in answers |
| Delivery | **SSE** step-progress stream | Live feedback for multi-second LLM steps |
| Relative dates | Anchored to the **latest date in the data**, assumption stated in answer | Datasets are historical (e.g. Jaffle ends 2018) |
| Verified-query cache | **Not now** | Keep PoC scope tight; revisit later |
| Schema linking | None — full compact schema context (P8) in prompt | Small profiled schemas; Distillery finding |
| Engine | SQL on DuckDB for all heavy work | Correct, fast, auditable; SQL shown to user |

## Pipeline

```
C0 Cleaning gate (once per session, after profiling)
C1 Context      schema context (P8) + conversation memory + data date range
C2 Understand   (LLM) intent, standalone rewrite, time resolution, output request
                (text / table / chart), answerable? ambiguous? -> clarify
C3 SQL loop     (LLM, bounded agentic) tools: run_sql, describe_table, sample_values
                -> final SQL. Repair on errors / empty / suspicious results
C4 Guard        SQLGlot: single SELECT, known tables/columns, LIMIT, timeout,
                fan-out (many-to-many join) check
C5 Execute      read-only DuckDB connection
C6 Present      table (row cap + CSV download) and/or chart spec
                (rules + LLM hint, validated JSON -> Recharts)
C7 Narrate      (LLM) answer + assumptions + data-quality flags;
                number-grounding check against result
C8 Memory/trace store turn (question, rewrite, SQL, result summary) for follow-ups
```

### C0 Cleaning gate
1. Profiling already detects issues (nulls, type mismatches, whitespace, inconsistent casing, outliers, duplicate keys).
2. UI shows an "Data issues found" card listing each issue with the proposed fix.
3. User chooses **Clean** (all or per-issue) or **Use as-is**.
4. Clean → create `clean_<table>` DuckDB views (trim, cast, standardize nulls; outliers *flagged* via a column, never deleted). Chat queries the views.
5. As-is → chat queries raw tables; narration appends caveats for issues touching the columns used.

### Response shape (SSE)
Events: `step` (`understanding|writing_sql|running|charting|answering`), `clarification`, `final`, `error`.

```json
{
  "answer": "markdown text",
  "table": {"columns": [...], "rows": [...], "truncated": false} | null,
  "chart": {"type": "bar|line|pie|scatter|area", "x": "...", "y": ["..."], "series": null, "title": "..."} | null,
  "sql": "SELECT ...",
  "assumptions": ["'past year' = 2017-04-01..2018-04-01 (latest data date)"],
  "data_quality_flags": ["orders.amount has 3% nulls (not cleaned)"],
  "trace": {"llm_calls": 3, "tool_calls": 2, "timings_s": {...}}
}
```

## Code layout

```
app/chat/
  context.py      P8 schema context builder
  memory.py       conversation store
  cleaning.py     issue listing + clean view creation
  guard.py        SQLGlot validation, fan-out check
  executor.py     read-only execution with limits/timeout
  tools.py        run_sql / describe_table / sample_values for the SQL loop
  presenters.py   table + chart spec (rules + validation)
  strategies/     understand, sql_loop, narrate (Protocols; LLM + fallback)
  agent.py        orchestrates C1–C8, emits events
  factory.py      DI wiring (reuses app/llm)
```

API: `GET /api/sessions` (history), `POST /api/sessions/{id}/cleaning` (cleaning issues are in the session status), `POST /api/sessions/{id}/messages` (SSE), `GET /api/sessions/{id}/messages`, `GET /api/sessions/{id}/messages/{mid}/download.csv`.

## Evaluation plan (`experiments/chat/`)
- **exp01**: one-shot SQL baseline vs hybrid on gold sets (jaffle_shop, chinook, tpch) — execution accuracy.
- Follow-up suite (multi-turn), edge cases (ambiguous, unanswerable, empty results, dirty data), output-type selection (table/chart requests honored), number-grounding rate, latency/cost per turn.
- Large data: TPC-H scale factors.

## Experiment results (exp01)
See `experiments/chat/exp01_hybrid/REPORT.md`. Final run: hybrid 34/35, loop_only 34/35, one-shot 33/35; behaviour 6/6
in all variants. Hybrid is about 2.3x the latency of one-shot. Decision: hybrid is the default, and loop_only is available as a fast mode.
A re-run after the integration fixes below scored hybrid 35/35 (chinook 14/14, jaffle 10/10, tpch 11/11), behaviour 6/6, chart 11/11, with a p50 of 5.9s and 115 LLM calls.

## Implementation status
Integrated end to end: upload → profiling → cleaning gate → chat (SSE stages, SQL, table/chart, assumptions, CSV download).
Fixes found during browser E2E:
- **Grounding false positives:** numbers that appear in the assumptions or data-quality notes count as grounded. The template fallback no longer repeats the assumptions, which the UI shows separately.
- **Stale context after cleaning:** after the fixes are applied, metrics are re-validated against the cleaned tables. A leading caveat lists the applied fixes so the agent does not re-clean them in SQL. This brought the dirty-data query from 3 steps and 5 LLM calls down to 1 step and 3 LLM calls.
- **Duplicate assumptions:** the SQL agent reports only interpretations that the understander has not already listed.

## Session persistence
`app/persistence.py` defines a `SessionRepository` protocol, implemented by `JsonSessionRepository`. It stores `session.json` (the job record) and `conversation.json` (messages plus agent turns) in each session folder, using atomic writes.

On startup, `JobStore` restores every session. Profiling that was still running is marked as failed and is not resumed, to avoid unexpected LLM calls. `ConversationService` rebuilds the agent's turn memory lazily, so follow-up questions keep their context after a restart.

`GET /api/sessions` lists the sessions. The UI uses `#/session/<id>` routing and a sessions drawer.

Limitations: storage is local to one instance, sessions cannot be deleted, the cleaning decision is made once per session, and there is no auth.
