"""Run experiment 01 on the evaluation datasets. Usage: python run.py [--no-cache] [--model M] [datasets...]"""

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from common.harness import run_experiment  # noqa: E402
from common.llm import DEFAULT_MODEL, DEFAULT_REASONING_EFFORT, LLM  # noqa: E402
from strategy import profile  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("datasets", nargs="*")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--reasoning-effort", default=DEFAULT_REASONING_EFFORT)
    parser.add_argument("--out", default="results", help="results sub-directory, e.g. results/repeat2")
    args = parser.parse_args()
    llm = LLM(model=args.model, use_cache=not args.no_cache, reasoning_effort=args.reasoning_effort)
    summary = run_experiment("exp01_llm_assisted", profile, HERE / args.out, llm, args.datasets or None)
    print(summary["llm_usage"])
