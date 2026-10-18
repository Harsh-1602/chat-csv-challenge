# Profiling experiments

Each experiment tries one profiling strategy on the evaluation datasets (`eval/datasets/{jaffle_shop,chinook,tpch}`). All experiments are scored by the same harness against [common/ground_truth.json](common/ground_truth.json), and the relationships are taken from `eval/questions/*.json`.

## Leaderboard

Relationship F1, primary-key accuracy, and role accuracy per dataset (Jaffle Shop / Chinook / TPC-H). Experiments report the mean of 3 runs.

| Experiment | Rel F1 | PK accuracy | Role accuracy | Unit (cents) | Cost per run (3 datasets) | Latency per dataset | Notes |
|---|---|---|---|---|---|---|---|
| [baseline_v1](baseline_v1/README.md) | 0.00 / 0.90 / 0.00 | 3/3 · 10/11 · 0/8 | 1.00 / 0.98 / 0.74 | 0/1 | $0 | < 0.5 s | Exact-name matching only |
| [exp01_llm_assisted](exp01_llm_assisted/STRATEGY.md) (gpt-4.1-mini) | 1.00 / 0.986 / 1.00 | 3/3 · 11/11 · 8/8 | 1.00 / 0.963 / 0.994 | 1/3 runs | ~$0.043 | 12–30 s | [Results](exp01_llm_assisted/RESULTS.md): unstable unit detection, self-link verifier bug, slow P4 |
| [exp01_llm_assisted](exp01_llm_assisted/STRATEGY.md) (**gpt-5.6-terra, medium**) | **1.00 / 1.00 / 1.00** | **3/3 · 11/11 · 8/8** | **1.00 / 1.00 / 1.00** | **3/3 runs** | ~$0.27 | 14–26 s | Same code; perfect in all 3 runs. Current default. |

## Layout

```
common/            shared harness, LLM wrapper, ground truth
baseline_v1/       the app's current profiler, scored for reference
expNN_<name>/
  STRATEGY.md      hypothesis, pipeline, thresholds, prompts summary
  strategy.py      profile(con, tables, llm) -> dict
  run.py           python run.py [--no-cache] [--model M] [--out results/repeatN] [datasets...]
  RESULTS.md       scores, error analysis, next steps
  results/         <dataset>.profile.json, metrics.json, SUMMARY.md, llm_log.jsonl
```

## Running

```powershell
backend\.venv\Scripts\python.exe experiments\profiling\exp01_llm_assisted\run.py --no-cache
```

The script needs `OPENAI_API_KEY` in the repo-root `.env`. The default model is `gpt-5.6-terra` with reasoning effort `medium`. Override them with `OPENAI_MODEL` / `OPENAI_REASONING_EFFORT` or `--model` / `--reasoning-effort`. Reasoning models (`gpt-5*`, o-series) are sent `reasoning_effort` instead of `temperature`. LLM responses are cached in `experiments/profiling/.cache/` (git-ignored), with the cache key including the model and effort; always report results from `--no-cache` runs.

## Rules

1. A strategy change means a new experiment folder. Never edit a finished experiment's code or results.
2. Report the mean over at least 3 uncached runs. LLM output varies even at temperature 0.
3. Every LLM judgement must be verified by SQL before it counts, and the verification must be part of the strategy.
