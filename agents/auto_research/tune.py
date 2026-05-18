"""Coordinate-descent auto-tuner over the MPC hyperparameter search space.

Loop:
  1. Establish a baseline by running the unmodified agent.
  2. For each iteration:
       a. Pick a random knob from SEARCH_SPACE that we haven't recently tried.
       b. For each candidate value of that knob:
          - SCREEN: run a 1-seed, 4-scenario smoke (~60-90s).
          - If smoke-robust > current_smoke_best by SCREEN_MARGIN:
              PROMOTE: run the full 3-seed eval (~3-5 min).
              If full-robust > current_best: keep the overlay; commit best.
       c. Move to the next knob (coordinate descent).
  3. Stop after N iterations OR after K consecutive non-improving knobs.

Outputs:
  auto_research_runs/runs.jsonl                 — every game from every run
  auto_research_runs/<run_id>/manifest.json     — per-run summary
  auto_research_runs/best.json                  — current best overlay + score
  auto_research_runs/tune_<sessionid>.log       — human-readable trajectory

Usage:
  # Calibrate baseline + try 6 knobs (1 screen-seed). ~10-15 min total.
  python -m agents.auto_research.tune --iters 6

  # Aggressive: 20 iterations, also re-evaluate held-out seeds on every win.
  python -m agents.auto_research.tune --iters 20 --holdout

  # Restart from a saved best.json (continues coordinate descent from there).
  python -m agents.auto_research.tune --iters 10 --resume

The tuner is *deterministic given the random seed* of its own RNG. A fresh
session reuses a fresh seed; pass --rng-seed to reproduce a session exactly.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any

from .benchmark import (
    RUNS_DIR, run_benchmark, print_summary,
    DEFAULT_SCENARIOS, DEFAULT_SEEDS,
    DEFAULT_SMOKE_SCENARIOS, DEFAULT_SMOKE_SEEDS,
)
from .search_space import SEARCH_SPACE, candidates_for, all_keys


BEST_PATH = RUNS_DIR / "best.json"

# A candidate must beat the current best smoke score by this margin to be
# promoted to a full evaluation. Higher = fewer false promotions but slower
# discovery.
SCREEN_MARGIN = 200.0
# A full evaluation must beat the current full best by this margin to commit.
ACCEPT_MARGIN = 200.0
# Stop after this many consecutive non-improving knobs.
PATIENCE = 6


def _now() -> str:
    return time.strftime("%H:%M:%S")


class TuneLogger:
    def __init__(self, session_id: str):
        self.path = RUNS_DIR / f"tune_{session_id}.log"
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a")
        self._echo(f"[{_now()}] SESSION {session_id} START")

    def _echo(self, msg: str):
        print(msg)
        self.f.write(msg + "\n")
        self.f.flush()

    def info(self, msg: str):
        self._echo(f"[{_now()}] {msg}")

    def close(self):
        self.f.close()


def _load_best() -> dict | None:
    if not BEST_PATH.exists():
        return None
    try:
        with open(BEST_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def _save_best(best: dict) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    with open(BEST_PATH, "w") as f:
        json.dump(best, f, indent=2, default=str)


def _build_overlay(base: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
    """Return a copy of `base` with `key → value` overlaid (or removed if value is None
    AND the path supports None — only CASH_RESERVE_OVERRIDE allows None, others
    treat None as 'leave default')."""
    out = dict(base)
    # None means "use default" → drop the key from the overlay (except for
    # CASH_RESERVE_OVERRIDE where None IS the literal default).
    if value is None and key != "rules_supply.CASH_RESERVE_OVERRIDE":
        out.pop(key, None)
    else:
        out[key] = value
    return out


def tune(
    *,
    iters: int,
    holdout: bool,
    rng_seed: int | None,
    parallel: int,
    team_name: str | None,
    resume: bool,
    base_url: str | None,
    smoke_seeds: list[int],
    full_seeds: list[int],
    scenarios: list[str],
    skip_baseline: bool = False,
) -> None:
    session_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
    log = TuneLogger(session_id)
    log.info(f"iters={iters} holdout={holdout} resume={resume} parallel={parallel}")
    log.info(f"smoke_seeds={smoke_seeds} full_seeds={full_seeds} scenarios={scenarios}")

    rng = random.Random(rng_seed if rng_seed is not None else int(time.time()))

    # ── 1. Baseline / resume ─
    if resume:
        prior = _load_best()
        if prior is None:
            log.info("--resume specified but no best.json yet; starting fresh.")
            current_overlay = {}
        else:
            current_overlay = dict(prior.get("overlay") or {})
            log.info(f"Resuming from best.json — overlay has {len(current_overlay)} knob(s)")
            for k, v in current_overlay.items():
                log.info(f"  resume {k} = {v!r}")
    else:
        current_overlay = {}

    if skip_baseline and _load_best() is not None:
        best_full = _load_best()
        log.info(f"Skipping baseline; using stored best_full robust={best_full['robust_score']:.0f}")
    else:
        log.info(">>> Baseline FULL evaluation <<<")
        baseline = run_benchmark(
            overlay=current_overlay,
            label="auto-tuner BASELINE",
            scenarios=scenarios,
            seeds=full_seeds,
            base_url=base_url,
            team_name=team_name,
            parallel=parallel,
            quiet=True,
        )
        print_summary(baseline)
        best_full = {
            "run_id": baseline.run_id,
            "overlay": baseline.overlay,
            "robust_score": baseline.robust_score,
            "mean_score": baseline.mean_score,
            "bankruptcies": baseline.bankruptcies,
            "per_scenario_avg": baseline.per_scenario_avg,
        }
        _save_best(best_full)
        log.info(
            f"BASELINE: robust={baseline.robust_score:.0f} mean={baseline.mean_score:.0f} "
            f"bankruptcies={baseline.bankruptcies} run_id={baseline.run_id}"
        )

    # Per-knob smoke baseline cache: when we screen, we screen against this.
    # On any committed full improvement we refresh this so screening keeps up.
    log.info(">>> Calibrating smoke baseline (1-seed) <<<")
    smoke_base = run_benchmark(
        overlay=current_overlay,
        label="auto-tuner SMOKE-BASELINE",
        scenarios=scenarios,
        seeds=smoke_seeds,
        base_url=base_url,
        team_name=team_name,
        parallel=parallel,
        quiet=True,
    )
    print_summary(smoke_base)
    smoke_best_robust = smoke_base.robust_score
    log.info(f"smoke_baseline robust={smoke_best_robust:.0f}")

    # ── 2. Coordinate descent ─
    knobs = list(all_keys())
    rng.shuffle(knobs)
    knob_idx = 0
    consecutive_no_improve = 0
    iter_count = 0

    while iter_count < iters and consecutive_no_improve < PATIENCE:
        if knob_idx >= len(knobs):
            rng.shuffle(knobs)
            knob_idx = 0
        key = knobs[knob_idx]
        knob_idx += 1
        iter_count += 1

        candidates = candidates_for(key)
        # Drop the value we currently use to avoid wasted screens
        current_val = current_overlay.get(key, "__SENTINEL__")

        log.info("")
        log.info("─" * 70)
        log.info(f"ITER {iter_count}/{iters}  knob={key}")
        log.info(f"  current value: {current_val if current_val != '__SENTINEL__' else '(default)'}")
        log.info(f"  candidate values: {candidates}")

        knob_improved = False
        for value in candidates:
            if value == current_val:
                continue
            cand_overlay = _build_overlay(current_overlay, key, value)
            log.info(f"  SCREEN {key} = {value!r}  (smoke seeds {smoke_seeds})")
            try:
                screen = run_benchmark(
                    overlay=cand_overlay,
                    label=f"screen {key}={value!r}",
                    scenarios=scenarios,
                    seeds=smoke_seeds,
                    base_url=base_url,
                    team_name=team_name,
                    parallel=parallel,
                    quiet=True,
                )
            except Exception as e:
                log.info(f"    SCREEN ERROR: {e!r} — skipping")
                continue
            log.info(
                f"    smoke robust={screen.robust_score:.0f} mean={screen.mean_score:.0f} "
                f"bankruptcies={screen.bankruptcies} run_id={screen.run_id}"
            )
            if screen.robust_score < smoke_best_robust + SCREEN_MARGIN:
                log.info(f"    REJECT @ screen (need +{SCREEN_MARGIN:.0f})")
                continue

            # Promote to full evaluation
            log.info(f"  PROMOTE → FULL eval (seeds {full_seeds})")
            try:
                full = run_benchmark(
                    overlay=cand_overlay,
                    label=f"full {key}={value!r}",
                    scenarios=scenarios,
                    seeds=full_seeds,
                    base_url=base_url,
                    team_name=team_name,
                    parallel=parallel,
                    quiet=True,
                )
            except Exception as e:
                log.info(f"    FULL ERROR: {e!r} — skipping")
                continue
            log.info(
                f"    full robust={full.robust_score:.0f} mean={full.mean_score:.0f} "
                f"bankruptcies={full.bankruptcies} run_id={full.run_id}"
            )

            if full.robust_score > best_full["robust_score"] + ACCEPT_MARGIN \
                    and full.bankruptcies <= best_full.get("bankruptcies", 0):
                log.info(
                    f"  ✓ COMMIT  Δrobust={full.robust_score - best_full['robust_score']:+.0f}  "
                    f"Δmean={full.mean_score - best_full['mean_score']:+.0f}"
                )
                current_overlay = cand_overlay
                best_full = {
                    "run_id": full.run_id,
                    "overlay": full.overlay,
                    "robust_score": full.robust_score,
                    "mean_score": full.mean_score,
                    "bankruptcies": full.bankruptcies,
                    "per_scenario_avg": full.per_scenario_avg,
                }
                _save_best(best_full)
                # Refresh smoke baseline to track the new optimum
                log.info("  Re-calibrating smoke baseline against new optimum")
                smoke_base = run_benchmark(
                    overlay=current_overlay,
                    label="post-commit smoke baseline",
                    scenarios=scenarios,
                    seeds=smoke_seeds,
                    base_url=base_url,
                    team_name=team_name,
                    parallel=parallel,
                    quiet=True,
                )
                smoke_best_robust = smoke_base.robust_score
                knob_improved = True

                # Optional: held-out verification
                if holdout:
                    log.info("  Held-out verification (seeds 7,99,314,1000)")
                    ho = run_benchmark(
                        overlay=current_overlay,
                        label="held-out verify",
                        scenarios=scenarios,
                        seeds=[7, 99, 314, 1000],
                        base_url=base_url,
                        team_name=team_name,
                        parallel=parallel,
                        quiet=True,
                    )
                    log.info(
                        f"    held-out robust={ho.robust_score:.0f} mean={ho.mean_score:.0f} "
                        f"bankruptcies={ho.bankruptcies}"
                    )
                break  # move on to the next knob
            else:
                reason = (
                    f"need Δ>+{ACCEPT_MARGIN:.0f}"
                    if full.bankruptcies <= best_full.get("bankruptcies", 0)
                    else f"bankruptcies {full.bankruptcies} > best {best_full['bankruptcies']}"
                )
                log.info(f"  ✗ REJECT @ full ({reason})")

        if not knob_improved:
            consecutive_no_improve += 1
            log.info(f"  knob did not improve  (streak {consecutive_no_improve}/{PATIENCE})")
        else:
            consecutive_no_improve = 0

    log.info("")
    log.info("═" * 70)
    log.info(f"SESSION DONE — iterations={iter_count}, no_improve_streak={consecutive_no_improve}")
    log.info(f"Best robust : {best_full['robust_score']:.0f}")
    log.info(f"Best mean   : {best_full['mean_score']:.0f}")
    log.info(f"Best overlay knobs ({len(best_full['overlay'])}):")
    for k, v in best_full["overlay"].items():
        log.info(f"  {k} = {v!r}")
    log.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--iters", type=int, default=6, help="Max knobs to explore.")
    p.add_argument("--holdout", action="store_true", help="After every commit, verify on held-out seeds.")
    p.add_argument("--rng-seed", type=int, default=None)
    p.add_argument("--parallel", type=int, default=5)
    p.add_argument("--team-name", default=None)
    p.add_argument("--url", default=None)
    p.add_argument("--resume", action="store_true", help="Continue from auto_research_runs/best.json")
    p.add_argument("--skip-baseline", action="store_true",
                   help="Use stored best.json as starting point (when resuming).")
    p.add_argument("--scenarios", default=",".join(DEFAULT_SCENARIOS))
    p.add_argument("--smoke-seeds", default=",".join(str(s) for s in DEFAULT_SMOKE_SEEDS))
    p.add_argument("--full-seeds", default=",".join(str(s) for s in DEFAULT_SEEDS))
    args = p.parse_args()

    tune(
        iters=args.iters,
        holdout=args.holdout,
        rng_seed=args.rng_seed,
        parallel=args.parallel,
        team_name=args.team_name,
        resume=args.resume,
        skip_baseline=args.skip_baseline,
        base_url=args.url,
        scenarios=args.scenarios.split(","),
        smoke_seeds=[int(s) for s in args.smoke_seeds.split(",")],
        full_seeds=[int(s) for s in args.full_seeds.split(",")],
    )


if __name__ == "__main__":
    main()
