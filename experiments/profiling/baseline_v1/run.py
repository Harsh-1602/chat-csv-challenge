"""Score the current app profiler (backend/app/profiling.py, v1) with the experiment harness."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.harness import baseline_v1, run_experiment  # noqa: E402

if __name__ == "__main__":
    run_experiment("baseline_v1", baseline_v1, Path(__file__).parent / "results")
