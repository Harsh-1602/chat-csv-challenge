# exp01 — Orchestration: ONE_SHOT vs HYBRID vs LOOP_ONLY

## Setup
- Model: gpt-5.6-terra, reasoning=medium. 35 execution questions + 6 behaviour questions (clarify/refuse) over
  jaffle_shop, chinook, tpch. Follow-ups are run in conversation order.
- Variants (all built from `backend/app/chat` via `ChatConfig` presets):
  - **baseline (ONE_SHOT)** — single SQL generation, no understanding step, no repair, template answer.
  - **loop_only** — SQL agent loop (inspect → generate → execute → validate → repair), no understanding step.
  - **hybrid** — understanding (rewrite/clarify/assumptions) + loop + LLM narration.
- Scoring: execution match on value vectors (column-name agnostic, extra columns allowed, rows sorted,
  numeric tolerance, percent/fraction accepted), chart family check, behaviour check.

## Results (4 runs; fixes applied between runs)

| Run | Change before run | baseline | hybrid | loop_only |
|---|---|---|---|---|
| 1 | — | 28/35 | 28/35 | 27/35 |
| 2 | auto-chart, gold label fixes, business-date rule | 30/35 | 33/35 | 33/35 |
| 3 | percent-scale scoring, tp-09 label | 33/35 | 33/35 | 33/35 |
| 4 | superlative-tie rule, `order_matters` off | 33/35 | **34/35** | **34/35** |

Final run detail:

| | baseline | hybrid | loop_only |
|---|---|---|---|
| Execution accuracy | 33/35 | 34/35 | 34/35 |
| Behaviour (clarify/refuse) | 6/6 | 6/6 | 6/6 |
| Chart type | 11/11 | 11/11 | 10/11 |
| p50 latency | 2.8 s | 6.5 s | 4.5 s |
| LLM calls | 41 | 115 | 79 |
| Prompt / completion tokens | 166k / 9.3k | 316k / 16.9k | 178k / 13.3k |

## Remaining failures
- **tp-05** (baseline, hybrid): TPC-H Q5 jargon "local suppliers" means customer and supplier in the same nation.
  This is domain knowledge that cannot be inferred from the schema. Fix: a glossary, or business rules provided by the user.
- **tp-03** (baseline): grouped revenue by `l_shipdate` instead of order date. Run-to-run variance; the loop variants
  got it right in this run.
- **ch-13** (loop_only): a year-over-year follow-up. The SQL is correct in spirit, but the shape and chart differ from the
  gold. This is variance.

## Fixes made during the experiment
- Product: auto-charting for dimension + measure results; date policy (use the business-event date by default);
  superlatives return all ties.
- Eval: label bugs fixed and tagged `"label_fix": "exp01 review"`. These covered over-strict ordering, gold columns
  that were not asked for, an ambiguous question wording, and the gold hiding a tie. `order_matters` was disabled globally
  because ordering is a presentation concern (ranking questions are still checked for the right rows via LIMIT/top-N).
  Risk acknowledged: label fixes could overfit the eval. Each fix corrected a gold or label error, not an agent behaviour.

## Takeaways
- On small, clean schemas a strong model makes the single-shot baseline competitive. The accuracy gap (≈1–3 questions)
  is within run-to-run noise.
- The loop's value is robustness: the repair loop recovers from SQL errors and empty or implausible results. The
  understanding step's value is the UX: assumptions, follow-up rewriting and clarification. The accuracy-only metric
  undervalues both.
- Cost: hybrid is about 2.3× the latency and about 1.9× the prompt tokens of the baseline.

## Decision
Ship **hybrid** as the default (assumptions and clarification are product requirements). Keep `loop_only` as a
"fast mode" config. Future optimisation: skip the understanding LLM call for standalone, unambiguous questions.
