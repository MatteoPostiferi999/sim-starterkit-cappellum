"""Run the MPC agent across (scenario × seed) combinations with an overlay applied.

Reuses `agents.evaluate.evaluate` — does NOT re-implement the eval loop. Just:

  1. apply_overlay(overlay)
  2. evaluate.evaluate(strategy, scenarios=..., seeds=..., parallel=...)
  3. restore overlay
  4. compute robust_score
  5. append one JSONL line per game + one manifest per run

Outputs:
  auto_research_runs/runs.jsonl          (append-only, one line per game)
  auto_research_runs/<run_id>/manifest.json  (overlay + summary)
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import statistics
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import random as _random

import httpx as _httpx

from agents import evaluate as _evaluate_mod
from agents import runner as _runner_mod
from agents.evaluate import evaluate
from agents.mpc import strategy as mpc_strategy

from .tuning import apply_overlay


# ── Patch agents.runner.run_game with a 429-aware retry wrapper ──────────────
# The hackathon server enforces a per-team concurrency cap that surfaces as
# HTTP 429. We retry the entire game (idempotent on the server side: it just
# allocates a new game_id) with exponential backoff + jitter. This is applied
# *globally* once the auto_research package is imported, so all benchmarks
# benefit. The wrapping is idempotent.
if not getattr(_runner_mod, "_AR_RETRY_WRAPPED", False):
    _original_run_game = _runner_mod.run_game

    def _run_game_with_retry(strategy, *args, **kwargs):
        max_attempts = int(os.getenv("AR_RETRY_ATTEMPTS", "5"))
        for attempt in range(max_attempts):
            try:
                return _original_run_game(strategy, *args, **kwargs)
            except _httpx.HTTPStatusError as e:
                code = e.response.status_code if e.response is not None else 0
                if code == 429 and attempt < max_attempts - 1:
                    delay = min(20.0, (2 ** attempt) + _random.uniform(0, 1.5))
                    time.sleep(delay)
                    continue
                raise

    _runner_mod.run_game = _run_game_with_retry
    # Also patch the symbol re-imported into agents.evaluate at module-load time
    _evaluate_mod.run_game = _run_game_with_retry
    _runner_mod._AR_RETRY_WRAPPED = True


ROOT = Path(__file__).resolve().parent.parent.parent
RUNS_DIR = ROOT / "auto_research_runs"
JSONL_PATH = RUNS_DIR / "runs.jsonl"

DEFAULT_SCENARIOS = ["baseline", "supply_crisis", "tourist_season", "renovation"]
DEFAULT_SEEDS = [42, 88, 123]
DEFAULT_SMOKE_SCENARIOS = ["baseline", "supply_crisis", "tourist_season", "renovation"]
DEFAULT_SMOKE_SEEDS = [42]

BANKRUPT_PENALTY = 50_000.0  # added to the absolute -100k score per bankruptcy
ERROR_PENALTY = 200.0
STDDEV_WEIGHT = 0.5


@dataclass
class RunResult:
    run_id: str
    label: str
    overlay: dict[str, Any]
    scenarios: list[str]
    seeds: list[int]
    games: list[dict]
    mean_score: float
    min_score: float
    max_score: float
    stddev_score: float
    bankruptcies: int
    errors: int
    robust_score: float
    per_scenario_avg: dict[str, float]
    wall_seconds: float
    git_marker: str
    timestamp: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _git_marker() -> str:
    """Return short HEAD sha + dirty marker, or 'no-git'."""
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL
        ).decode().strip()
        dirty = subprocess.run(
            ["git", "diff", "--quiet"], cwd=ROOT, stderr=subprocess.DEVNULL
        ).returncode != 0
        return f"{sha}{'+dirty' if dirty else ''}"
    except Exception:
        return "no-git"


def _ensure_dirs() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)


def _robust_score(games: list[dict]) -> tuple[float, float, float, float, float, int, int]:
    """Return (mean, min, max, stddev, robust, bankrupt_count, error_count).

    robust_score = mean − STDDEV_WEIGHT * stddev
                   − BANKRUPT_PENALTY * bankruptcies
                   − ERROR_PENALTY * errors
    """
    scores = [g["score"] for g in games]
    bankrupt = sum(1 for g in games if g.get("status") == "bankrupt")
    errors = sum(1 for g in games if g.get("status") == "error")
    if not scores:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0
    mean = statistics.fmean(scores)
    mn = min(scores)
    mx = max(scores)
    sd = statistics.pstdev(scores) if len(scores) > 1 else 0.0
    robust = mean - STDDEV_WEIGHT * sd - BANKRUPT_PENALTY * bankrupt - ERROR_PENALTY * errors
    return mean, mn, mx, sd, robust, bankrupt, errors


def _append_jsonl(records: list[dict]) -> None:
    _ensure_dirs()
    with open(JSONL_PATH, "a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _write_manifest(result: RunResult) -> None:
    run_dir = RUNS_DIR / result.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "manifest.json", "w") as f:
        json.dump(result.to_dict(), f, indent=2, default=str)


# ── Public API ──────────────────────────────────────────────────────────────

def run_benchmark(
    overlay: dict[str, Any] | None = None,
    *,
    label: str = "",
    scenarios: list[str] | None = None,
    seeds: list[int] | None = None,
    base_url: str | None = None,
    team_name: str | None = None,
    parallel: int = 5,
    quiet: bool = True,
    run_id: str | None = None,
) -> RunResult:
    """Apply `overlay`, run the eval matrix, log results, return RunResult.

    `overlay` is a flat dict of `module.attr` → value (see tuning.py). Pass
    `overlay=None` or `{}` for the baseline (no mutations).

    `team_name` defaults to the AR_TEAM_NAME env var or "mpc_autoresearch".
    `base_url` defaults to RESTBENCH_URL or http://52.48.183.209:8001.
    """
    overlay = overlay or {}
    scenarios = scenarios or DEFAULT_SCENARIOS
    seeds = seeds or DEFAULT_SEEDS
    base_url = base_url or os.getenv("RESTBENCH_URL", "http://52.48.183.209:8001")
    team_name = team_name or os.getenv("AR_TEAM_NAME", "mpc_autoresearch")
    run_id = run_id or f"run_{int(time.time())}_{uuid.uuid4().hex[:6]}"

    t0 = time.time()
    restore_fn = apply_overlay(overlay)
    try:
        data = evaluate(
            mpc_strategy,
            scenarios=scenarios,
            seeds=seeds,
            base_url=base_url,
            team_name=team_name,
            verbose=not quiet,
            parallel=parallel,
        )
    finally:
        restore_fn()
    wall = time.time() - t0

    games = data["results"]
    scenario_totals = data["scenario_totals"]

    mean, mn, mx, sd, robust, bankrupt, errors = _robust_score(games)
    per_scenario_avg = {s: sum(v) / len(v) for s, v in scenario_totals.items()}

    result = RunResult(
        run_id=run_id,
        label=label,
        overlay=overlay,
        scenarios=scenarios,
        seeds=seeds,
        games=games,
        mean_score=mean,
        min_score=mn,
        max_score=mx,
        stddev_score=sd,
        bankruptcies=bankrupt,
        errors=errors,
        robust_score=robust,
        per_scenario_avg=per_scenario_avg,
        wall_seconds=wall,
        git_marker=_git_marker(),
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )

    # Persist
    _write_manifest(result)
    jsonl_records = []
    for g in games:
        jsonl_records.append({
            "ts": result.timestamp,
            "run_id": run_id,
            "label": label,
            "git": result.git_marker,
            "scenario": g["scenario"],
            "seed": g["seed"],
            "score": g["score"],
            "status": g["status"],
            "days_survived": g["days"],
            "final_cash": g["cash"],
            "net_profit": g["profit"],
            "walk_pen": g["walk_pen"],
            "rep_pen": g["rep_pen"],
            "sat_pen": g["sat_pen"],
            "waste_pen": g["waste_pen"],
        })
    _append_jsonl(jsonl_records)

    return result


def print_summary(result: RunResult, *, full: bool = False) -> None:
    """Print a compact summary of a RunResult. Use full=True to include per-game rows."""
    print("\n" + "═" * 70)
    print(f"  RUN {result.run_id}{'  — ' + result.label if result.label else ''}")
    print("═" * 70)
    print(f"  Scenarios     : {', '.join(result.scenarios)}")
    print(f"  Seeds         : {', '.join(map(str, result.seeds))}")
    print(f"  Games         : {len(result.games)}")
    print(f"  Wall          : {result.wall_seconds:.1f}s")
    print(f"  Git           : {result.git_marker}")
    if result.overlay:
        print(f"  Overlay       : {len(result.overlay)} knob(s)")
        for k, v in result.overlay.items():
            print(f"      {k} = {v!r}")
    else:
        print("  Overlay       : (none — baseline)")
    print()
    if full:
        print(f"  {'scenario':<18} {'seed':>5} {'score':>10} {'status':<10} {'days':>5}")
        print("  " + "-" * 60)
        for g in result.games:
            print(f"  {g['scenario']:<18} {g['seed']:>5} {g['score']:>10.0f} {g['status']:<10} {g['days']:>5}")
        print()
    print("  Per-scenario avg:")
    for s, v in result.per_scenario_avg.items():
        print(f"      {s:<18} {v:>10.0f}")
    print()
    print(f"  mean          : {result.mean_score:>10.0f}")
    print(f"  min           : {result.min_score:>10.0f}")
    print(f"  max           : {result.max_score:>10.0f}")
    print(f"  stddev        : {result.stddev_score:>10.0f}")
    print(f"  bankruptcies  : {result.bankruptcies}")
    if result.errors:
        print(f"  errors        : {result.errors}")
    print()
    print(f"  ROBUST SCORE  : {result.robust_score:>10.0f}")
    print("═" * 70)


def load_manifest(run_id: str) -> dict:
    path = RUNS_DIR / run_id / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(path)
    with open(path) as f:
        return json.load(f)


def list_runs() -> list[str]:
    if not RUNS_DIR.exists():
        return []
    return sorted([p.name for p in RUNS_DIR.iterdir() if p.is_dir() and p.name.startswith("run_")])


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run an MPC benchmark with an optional overlay.")
    parser.add_argument("--label", default="", help="Free-text label for this run (logged).")
    parser.add_argument("--overlay", default="", help='JSON overlay, e.g. \'{"simulator.GAMMA": 0.95}\'')
    parser.add_argument("--overlay-file", default="", help="Path to a JSON file with the overlay.")
    parser.add_argument("--scenarios", default="", help="Comma-separated. Default 4 known scenarios.")
    parser.add_argument("--seeds", default="", help="Comma-separated. Default 42,88,123.")
    parser.add_argument("--smoke", action="store_true", help="Quick: 4 scenarios × 1 seed (smoke).")
    parser.add_argument("--single", action="store_true", help="Tiny: 1 scenario × 1 seed (~30s).")
    parser.add_argument("--parallel", type=int, default=5)
    parser.add_argument("--team-name", default=None)
    parser.add_argument("--url", default=None)
    parser.add_argument("--full", action="store_true", help="Print per-game rows.")
    args = parser.parse_args()

    overlay: dict = {}
    if args.overlay:
        overlay = json.loads(args.overlay)
    elif args.overlay_file:
        with open(args.overlay_file) as f:
            overlay = json.load(f)

    if args.single:
        scenarios = ["baseline"]
        seeds = [42]
    elif args.smoke:
        scenarios = DEFAULT_SMOKE_SCENARIOS
        seeds = DEFAULT_SMOKE_SEEDS
    else:
        scenarios = args.scenarios.split(",") if args.scenarios else DEFAULT_SCENARIOS
        seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else DEFAULT_SEEDS

    result = run_benchmark(
        overlay=overlay,
        label=args.label,
        scenarios=scenarios,
        seeds=seeds,
        base_url=args.url,
        team_name=args.team_name,
        parallel=args.parallel,
        quiet=True,
    )
    print_summary(result, full=args.full)


if __name__ == "__main__":
    main()
