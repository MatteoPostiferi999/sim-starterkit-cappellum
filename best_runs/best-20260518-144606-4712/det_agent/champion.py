"""Champion tracker — snapshot the agent code + run when a new best is found.

Best by avg score across the 12-game eval. Snapshots live in best_runs/.
The pointer file best_runs/CURRENT_BEST.json holds the leader's metadata.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path


BEST_DIR = Path("best_runs")
POINTER = BEST_DIR / "CURRENT_BEST.json"


def current_best_score() -> float:
    if not POINTER.exists():
        return float("-inf")
    try:
        return json.loads(POINTER.read_text()).get("avg_score", float("-inf"))
    except Exception:
        return float("-inf")


def maybe_snapshot(run_path: Path, run_data: dict, agent_dir: Path) -> dict | None:
    """If this run is better than current best, snapshot it. Return metadata dict or None."""
    BEST_DIR.mkdir(exist_ok=True)
    scores = [r.get("score", 0) for r in run_data["results"]]
    avg = sum(scores) / len(scores) if scores else 0
    bankruptcies = sum(1 for r in run_data["results"] if r.get("status") == "bankrupt")

    cur_best = current_best_score()
    if avg <= cur_best:
        return None

    ts = time.strftime("%Y%m%d-%H%M%S")
    snap_dir = BEST_DIR / f"best-{ts}-{int(avg)}"
    snap_dir.mkdir(parents=True, exist_ok=True)

    # Copy agent code
    target_agent = snap_dir / "det_agent"
    shutil.copytree(agent_dir, target_agent, ignore=shutil.ignore_patterns("__pycache__"))

    # Copy the run JSON
    if run_path and run_path.exists():
        shutil.copy(run_path, snap_dir / "run.json")

    meta = {
        "avg_score": avg,
        "bankruptcies": bankruptcies,
        "n_games": len(scores),
        "scores_by_scenario": _by_scenario(run_data["results"]),
        "snapshot_dir": str(snap_dir),
        "run_file": str(run_path),
        "ts": ts,
        "label": run_data.get("label", ""),
    }
    POINTER.write_text(json.dumps(meta, indent=2))
    return meta


def _by_scenario(results: list[dict]) -> dict[str, float]:
    by: dict[str, list[float]] = {}
    for r in results:
        by.setdefault(r["scenario"], []).append(r.get("score", 0))
    return {k: round(sum(v)/len(v), 1) for k, v in by.items()}
