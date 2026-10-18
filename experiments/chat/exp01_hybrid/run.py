"""exp01: one-shot baseline vs hybrid agent vs ablation without the understanding step.

Usage (from repo root):
    backend\\.venv\\Scripts\\python experiments\\chat\\exp01_hybrid\\run.py [--variants baseline hybrid loop_only]
                                                                            [--datasets jaffle_shop chinook tpch]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.harness import DATASETS, run_variant  # noqa: E402

from app.chat.factory import HYBRID, LOOP_ONLY, ONE_SHOT  # noqa: E402

VARIANTS = {"baseline": ONE_SHOT, "hybrid": HYBRID, "loop_only": LOOP_ONLY}
RESULTS = Path(__file__).parent / "results"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=list(VARIANTS))
    parser.add_argument("--datasets", nargs="+", default=DATASETS, choices=DATASETS)
    args = parser.parse_args()
    summaries = {name: run_variant(name, VARIANTS[name], RESULTS / name, args.datasets) for name in args.variants}
    keys = ("execution_accuracy", "per_dataset", "behaviour", "chart_type", "latency_p50_s", "llm_calls",
            "prompt_tokens", "completion_tokens")
    print(json.dumps({n: {k: s[k] for k in keys} for n, s in summaries.items()}, indent=2))


if __name__ == "__main__":
    main()
