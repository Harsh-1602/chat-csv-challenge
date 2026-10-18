# Baseline v1: current app profiler

Scores the profiler that ships in [backend/app/profiling.py](../../../backend/app/profiling.py), using the same harness as the experiments.

**How it works:**
- Roles come from a name regex (`id`, `*_id`, `*Id`) and the DuckDB type.
- The PK is the first unique identifier column. There are no composite keys.
- A relationship is found only when a child column has exactly the parent PK's name and a match rate ≥ 0.5.

Run: `backend\.venv\Scripts\python.exe experiments\profiling\baseline_v1\run.py`. Results are in [results/SUMMARY.md](results/SUMMARY.md).
