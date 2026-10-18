# Profiling v2 design: LLM-assisted ingestion

Status: **draft for discussion**. Background research: [profiling-research.md](./profiling-research.md).

## Principle: SQL gathers evidence, the LLM judges, SQL verifies

The LLM is used at several profiling stages, but it never computes a number and never has the last word on a fact the data can check:

1. **Evidence (deterministic):** DuckDB computes statistics, sample values and candidate keys and relationships.
2. **Judgement (LLM):** the LLM interprets the evidence: what a column means, which key is the real one, which candidate link is a real relationship, what a table's grain is. It returns **structured output** (JSON schema / Pydantic), never free text.
3. **Verification (deterministic):** every LLM claim that the data can test is re-checked in DuckDB (uniqueness, containment, cast success rate). Claims that fail are dropped, or marked `needs_review`.

So the LLM adds what heuristics can't do (`SupportRepId → EmployeeId`, "amount is in cents", "one row per order line"), and SQL guards against hallucinated keys, joins and casts. If the LLM is unavailable, the deterministic fallback still produces a usable profile.

## Pipeline

```
P0 Load ─► P1 Column stats ─► P2 Table understanding (LLM, one call per table, in parallel)
                                   │ verify: casts, null tokens, key candidates
                                   ▼
        P3 Candidate keys & links (SQL) ─► P4 Relationship decisions (LLM, one call per dataset)
                                   │ verify: containment, key on the parent side, cardinality
                                   ▼
        P5 Grain, fan-out & quality (SQL) ─► P6 Semantic model (LLM, one call per dataset)
                                   ▼
        P7 Review (UI: only low-confidence items) ─► P8 Persist schema context for chat
```

| Stage | Type | Input | Output | Verified by |
|---|---|---|---|---|
| **P0 Load** | SQL | CSV files | Raw tables (DuckDB auto-detect, all columns kept as loaded) | Parse errors → failed-file list |
| **P1 Column stats** | SQL | Raw tables | Null %, distinct count, min/max/quartiles, top values, 5 sample values, regex pattern hits (currency, %, thousands separators, date-like strings, null tokens) | — |
| **P2 Table understanding** | **LLM** | Per table: name, columns + types + P1 stats, ~15 sample rows | Table description, entity, column descriptions, semantic types, roles, units, proposed cleaning casts, null tokens, key guesses | P2 verification (below) |
| **P3 Candidates** | SQL | P1 + P2 | Unique column sets (1–3 columns) among id-like columns, plus candidate links: containment ratio, name similarity, type compatibility, distinct counts | — |
| **P4 Relationship decisions** | **LLM** | Compact schema + P3 candidates with evidence | Chosen primary key per table; accepted links with role names (e.g. `SupportRepId` = "support rep") and reasoning; rejected candidates; extra links proposed by the LLM | Containment ≥ 0.95 and parent side unique, otherwise `needs_review` |
| **P5 Grain, fan-out & quality** | SQL | Accepted keys and links | Grain check, relationship cardinality, orphan counts, fan-out flags, duplicate rows, outliers, cast failures | — |
| **P6 Semantic model** | **LLM** | Everything above | Measures (default aggregation, unit, additivity), dimensions, time dimensions, synonyms, join paths, caveats, dataset summary, suggested starter questions | Every referenced column and table must exist; aggregations must suit the type |
| **P7 Review** | UI | Items with confidence below threshold | User confirms or edits keys, relationships and casts | — |
| **P8 Persist** | — | Final model | `semantic_model.json` and a compact schema-context string for the chat LLM | Schema check |

### P2 Table understanding (LLM, one call per table, in parallel)

Output schema (Pydantic, abridged):

```python
class ColumnUnderstanding(BaseModel):
    name: str
    description: str                      # one line, business meaning
    semantic_type: str                    # e.g. person_name, email, country, currency_amount, percentage, category, free_text, surrogate_id, natural_key, timestamp
    role: Literal["identifier", "measure", "dimension", "time", "flag", "text"]
    unit: str | None                      # "USD", "cents", "percent", "kg", ...
    cleaning: Literal["none", "parse_number", "parse_percent", "parse_currency", "parse_date", "parse_bool", "nullify_tokens"]
    date_format: str | None               # strptime format when cleaning == parse_date
    null_tokens: list[str]                # values that mean missing, e.g. ["N/A", "-"]
    confidence: float

class TableUnderstanding(BaseModel):
    description: str
    entity: str                           # "order", "order line", "customer", ...
    grain_guess: str                      # "one row per order line"
    key_guess: list[str]                  # proposed primary-key columns
    columns: list[ColumnUnderstanding]
```

Verification:
- **Cleaning casts:** compute the `TRY_CAST` / `TRY_STRPTIME` success rate on the non-null values. Apply the cast only if at least 98% succeed; otherwise flag the column for review. Casts are applied as a **view over the raw table**, so the raw data is never modified.
- **Null tokens:** apply only tokens that actually occur in the column.
- **Key guesses:** test uniqueness and non-nulls in SQL, and pass the result to P3.
- **Roles:** if the LLM's role conflicts with the type, the type wins (for example, a "measure" stored as text that can't be cast).

### P3 Candidate generation (SQL)

- **Key candidates:**
  - Every single column that is non-null and unique.
  - Pairs, then triples, of id-like or low-cardinality columns, but only for tables with no single-column key. The search stops at 3 columns.
- **Link candidates:** for each column C, and each key column K in another table (or the same table, for self-references) with a compatible type:
  - `containment = |distinct(C) ∩ distinct(K)| / |distinct(C)|`
  - `name_sim`: compare the names after removing prefixes and suffixes (`c_`, `o_`, `Id`, `_id`, `key`), using token overlap and the table name.
  - Keep a candidate if containment ≥ 0.5, **or** if `name_sim` ≥ 0.6 and containment ≥ 0.2.
  - Drop pairs where both columns are their own table's primary key and the names differ (the "id → id" false positive), unless the LLM marks the link as one-to-one.

### P4 Relationship decisions (LLM, one call per dataset)

- **Input:** a compact `CREATE TABLE`-style schema with descriptions, plus a table of candidates with their evidence.
- **Output:** a chosen primary key per table; accepted links with `{from, to, role_name, cardinality_guess, reasoning, confidence}`; rejected candidates with a reason; links the LLM proposes that weren't candidates.
- **Verification:** a link is **accepted** only if containment is at least 0.95 and the target columns are unique. A link with containment from 0.5 up to 0.95 is shown as `needs_review` with the orphan count. Anything else is dropped.

### P5 Grain, fan-out and quality (SQL)

- **Grain:** check that the chosen key is unique, then record the grain statement ("one row per `order_id`").
- **Cardinality:** one-to-one or many-to-one, based on whether the child column is unique.
- **Fan-out:** a parent with two or more one-to-many children gets a warning. These are joins where aggregating a parent measure would double-count.
- **Quality:** orphaned rows, duplicate rows, rows that failed a cast, outliers, near-duplicate categories ("USA" / "United States", found by matching case-folded values with whitespace and punctuation removed).

### P6 Semantic model (LLM, one call per dataset)

The output resembles a Snowflake Cortex Analyst / dbt MetricFlow model:

```json
{
  "summary": "...",
  "tables": [{"name": "orders", "grain": "one row per order", "primary_key": ["order_id"], "description": "..."}],
  "measures": [{"name": "revenue", "table": "invoiceline", "expr": "unit_price * quantity", "agg": "sum", "unit": "USD", "additive": true}],
  "dimensions": [{"table": "customer", "column": "Country", "synonyms": ["nation"], "sample_values": ["USA", "Canada"]}],
  "time_dimensions": [{"table": "invoice", "column": "InvoiceDate", "min": "2009-01-01", "max": "2013-12-22"}],
  "relationships": [{"from": "invoice.CustomerId", "to": "customer.CustomerId", "cardinality": "many_to_one", "role": "customer"}],
  "caveats": ["payments.amount is in cents"],
  "starter_questions": ["Which country generates the most revenue?"]
}
```

Verification: parse every `expr` with SQLGlot, resolve its columns against the schema, and run `EXPLAIN` in DuckDB. Items that are invalid are dropped.

### P8 Schema context for the chat step

A compact text representation, following DAIL-SQL and M-Schema:
- `CREATE TABLE` statements with key annotations.
- A description comment on each column, plus up to 5 sample values for dimensions.
- The relationship graph with cardinality.
- Measures and caveats.

The chat step (B4 schema linking) trims this down to the tables relevant to each question.

## LLM integration

- **Provider:** OpenAI via `OPENAI_API_KEY` from `.env`, which is gitignored. The model is configurable through `OPENAI_MODEL`, and the call goes through a small `llm.py` wrapper:
  - structured outputs (`response_format` = JSON schema from Pydantic)
  - temperature 0
  - timeout and retry
  - token and latency logging
- **Calls per upload:** N parallel table calls in P2, plus 1 for P4 and 1 for P6. For Chinook that is 11 + 2 = 13 calls, with a target of under 20 s in total.
- **Caching:** results are keyed on a file content hash plus the prompt version, so re-uploading the same file doesn't call the LLM again.
- **Privacy:** only column names, statistics and at most about 15 sample rows per table are sent, never whole files. This is documented, and can be turned off with `PROFILING_LLM=off`.
- **Fallback:** if a call fails, its stage uses the deterministic heuristics: name/type roles, the P3 candidate scoring alone, and no descriptions. The job still completes and flags `llm_degraded`.
- **Progress stages in the UI:** Loading → Column statistics → Understanding tables → Finding keys & relationships → Checking quality → Building semantic model → Ready (or Review).

## Implementation status (integrated into the app)

The design is implemented in `backend/app` (P1–P6; P8 is pending, with the chat step):

| Concern | Where | Principle |
|---|---|---|
| LLM transport, retries, JSON output, usage tracking | `app/llm/` (`LLMClient` protocol, `OpenAIResponsesClient`, `TrackedLLM`, `build_llm_client`) | Dependency inversion: profiling never imports the OpenAI SDK |
| Table understanding / relationship judge / semantic model | `app/profiling/` strategy protocols with `LLM*` and `Heuristic*` implementations | Open/closed and Liskov: either implementation can be swapped in |
| SQL evidence and verification | statistics, candidates, `LinkVerifier`, `MetricValidator`, `PrimaryKeyResolver` | Single responsibility |
| Wiring | `app/profiling/factory.py` | Composition root (DI) |
| Orchestration and fallback | `app/profiling/pipeline.py` | If any LLM step fails, the heuristic for that step runs and the profile is marked `degraded` with a note |

Results from `experiments/profiling/app_pipeline/run.py`:

| Run | Relationship F1 (jaffle / chinook / tpch) | PK accuracy | Role accuracy |
|---|---|---|---|
| baseline v1 | 0 / 0.90 / 0 | 1.0 / 0.91 / 0 | n/a |
| app, heuristic only | 1.0 / 0.90 / 1.0 | 1.0 all | 1.0 / 0.98 / 0.74 |
| app, gpt-5.6-terra (medium) | 1.0 all | 1.0 all | 1.0 all |

LLM cost: jaffle 5 calls (~15 s), chinook 13 calls (~26 s), tpch 10 calls (~29 s).
## Evaluation

Run this on `eval/` (Jaffle Shop, Chinook, TPC-H), with ground truth taken from `eval/questions/*.json`:

| Metric | Baseline (v1) | Target |
|---|---|---|
| Relationship precision / recall | Jaffle Shop 0/2 found + 6 false links; Chinook 9/11; TPC-H 0/9 | ≥ 0.95 / ≥ 0.95 overall |
| Primary key accuracy (including composite keys) | Not measured | 100% on the 22 tables |
| Role accuracy (labels to be added to the question files) | Not measured | ≥ 90% |
| Unit and caveat detection (Jaffle Shop cents) | No | Yes |
| Latency (Chinook, LLM on) | ~1 s | < 20 s |

Each stage's output is saved separately in `profile_debug.json` so failures can be traced to a stage.

## Decisions (2026-10-17)

1. **LLM-first profiling:** the LLM is used in P2, P4 and P6, as designed above.
2. **Review (P7) is a blocking step** between profiling and chat. It lists only the low-confidence items. The user can review and edit each one, or skip the step, which accepts the system's choices.
3. **Default model:** `gpt-5.6-terra` with reasoning effort `medium`, configurable through `OPENAI_MODEL` and `OPENAI_REASONING_EFFORT`. Changed from `gpt-4.1-mini` on 2026-10-17: Terra scored perfectly in 3 of 3 runs and fixed the unit and verifier errors, at about 6x the cost. See [exp01 results](../experiments/profiling/exp01_llm_assisted/RESULTS.md).
4. **Strategy changes are tracked as experiments** in [experiments/profiling](../experiments/profiling/README.md). Each experiment records its strategy, code and results, so we can compare them on the same harness and metrics.

Still open: the accept thresholds (link containment ≥ 0.95; cast success ≥ 98%). Experiments will tune them.
