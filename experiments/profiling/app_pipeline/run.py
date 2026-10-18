"""Score the profiling pipeline exactly as the app runs it (factory + settings from the environment).

Usage (from repo root):  backend\\.venv\\Scripts\\python experiments\\profiling\\app_pipeline\\run.py [--heuristic]
"""

import argparse
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.harness import DATASETS_DIR, run_experiment  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.llm import build_llm_client  # noqa: E402
from app.profiling import NullSink, ProfilingPipelineFactory, SourceFile  # noqa: E402


def _dataset_dir(file_names: set[str]) -> Path:
    for folder in DATASETS_DIR.iterdir():
        if folder.is_dir() and {p.name for p in folder.glob("*.csv")} == file_names:
            return folder
    raise LookupError("dataset folder not found")


def make_strategy(factory: ProfilingPipelineFactory):
    def strategy(_con, tables: dict[str, str], _llm) -> dict:
        folder = _dataset_dir(set(tables.values()))
        sources = [SourceFile(f, t, folder / f, (folder / f).stat().st_size) for t, f in tables.items()]
        with tempfile.TemporaryDirectory() as tmp:
            profile = factory.create().run(Path(tmp) / "profile.duckdb", sources, NullSink())
        return {
            "tables": {
                t.table_name: {
                    "primary_key": t.primary_key or None,
                    "primary_key_source": t.primary_key_source,
                    "roles": {c.name: c.role for c in t.columns},
                    "units": {c.name: c.unit for c in t.columns},
                }
                for t in profile.tables
            },
            "relationships": [
                {"from": f"{r.from_table}.{r.from_column}", "to": f"{r.to_table}.{r.to_column}",
                 "status": r.status, "source": r.source, "reason": r.reason}
                for r in profile.relationships
            ],
            "semantic_model": profile.semantic_model.model_dump() if profile.semantic_model else None,
            "analysis": profile.analysis.model_dump(),
        }
    return strategy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--heuristic", action="store_true", help="disable the LLM (rule-based fallback only)")
    args = parser.parse_args()
    llm_settings = load_settings().llm
    if args.heuristic:
        llm_settings = replace(llm_settings, enabled=False)
    factory = ProfilingPipelineFactory(llm_settings, build_llm_client(llm_settings))
    name = f"app_pipeline_{llm_settings.model}" if factory.llm_enabled else "app_pipeline_heuristic"
    run_experiment(name, make_strategy(factory), Path(__file__).parent / "results" / name)


if __name__ == "__main__":
    main()
