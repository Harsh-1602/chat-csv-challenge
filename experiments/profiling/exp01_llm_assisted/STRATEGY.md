# Experiment 01: LLM-assisted profiling

**Status:** done (2026-10-17) · **Model:** `gpt-4.1-mini` at temperature 0 (runs 1–3); re-run on `gpt-5.6-terra` with reasoning effort `medium` (see RESULTS) · **Prompt version:** `exp01-v1`
**Design:** [docs/profiling-design.md](../../../docs/profiling-design.md) · **Code:** [strategy.py](strategy.py) · **Results:** [RESULTS.md](RESULTS.md)

## Hypothesis

The v1 profiler relies on name rules: keys must be called `*id`, and foreign keys must share the parent key's exact name. It misses keys and links whenever names differ: `user_id → id`, `c_custkey`, `ReportsTo`. If SQL gathers cheap evidence and an LLM makes the semantic judgements, with SQL re-checking every judgement, we should get near-perfect keys and relationships for a few cents per dataset.

## Pipeline

| Phase | Who | What |
|---|---|---|
| P1 Stats | SQL | Per column: type, null %, distinct count, `unique` flag, min/max/mean, whether the column has fractional values, average string length, cast rates for text columns (to number/date, ≥ 98%), top values when distinct ≤ 12. Plus 12 sample rows per table (reservoir sample, seed 42), with strings cut to 60 characters. |
| P2 Table understanding | LLM, 1 call per table, in parallel | Input: P1 stats, samples, and the other tables' column names. Output: entity, description, grain, primary key, and per column the role (`identifier/measure/time/dimension/text/boolean`), semantic type, description, unit, and the entity it references. |
| P2 Verify | SQL | The proposed PK must be unique and non-null, and is reduced to a minimal subset. If it fails, fall back to the first unique single column, or search composite keys of up to 3 identifier columns. |
| P3 Candidates | SQL | Child columns must be labelled `identifier` by the LLM or have name similarity ≥ 0.6. Parents are tables with a verified single-column PK. Types must be compatible, and pk→pk links with different names are skipped. Keep a candidate if containment ≥ 0.5, or name similarity ≥ 0.6 and containment ≥ 0.2. Evidence recorded: containment, parent coverage, child distinct count, orphan rows, name similarity, child uniqueness. |
| P4 Relationship decisions | LLM, 1 call per dataset | Accept, reject or mark uncertain for every candidate, with a reason, and propose any missing links. |
| P4 Verify | SQL | Accepted only if the LLM accepts and containment ≥ 0.95; otherwise `needs_review`. Proposed links are re-measured and need a unique parent plus containment ≥ 0.95. A child column accepted for several parents is moved to review. |
| P5 Structure | SQL | Cardinality (one-to-one or many-to-one), fan-out factor, and join warnings. |
| P6 Semantic model | LLM, 1 call per dataset | Summary, 4–10 metrics as DuckDB aggregate expressions, synonyms, caveats, example questions. Each metric expression is executed against its table to check it. |

## Not in scope

The P7 review UI and P8 persistence. CSV dialect and encoding problems are also out of scope: the eval CSVs are clean, so P0 uses the app's `load_csv`.

## Metrics (harness: [common/harness.py](../common/harness.py))

- **Relationships:** precision, recall and F1 of `accepted` links against `eval/questions/<ds>.json`. Links in `needs_review` are reported separately.
- **Primary keys:** exact match of the column set per table.
- **Roles:** accuracy over identifier, measure, time and descriptive. `dimension`, `text` and `boolean` all count as descriptive. Ambiguous columns are excluded.
- **Units:** whether the expected unit appears in the P2 `unit` field (`raw_payments.amount` = cents).
- **Cost:** latency, LLM calls, tokens and estimated USD.
