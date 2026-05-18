"""Parallel evaluation runner for det_agent.

Assigns a unique invented team name per (scenario, seed) game so 12 games can
hit the server concurrently without leaderboard collisions. Captures per-day
day_result snapshots for post-hoc diagnosis.

Usage:
  venv/bin/python -m agents.det_agent.eval_parallel
  venv/bin/python -m agents.det_agent.eval_parallel --scenarios baseline --seeds 42

The output JSON dump includes per-day metrics so the iteration loop can find
which days/scenarios bled the most score.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import random
import string
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx


DEFAULT_URL = os.getenv("RESTBENCH_URL", "http://52.48.183.209:8001")
DEFAULT_SCENARIOS = ["baseline", "supply_crisis", "tourist_season", "renovation"]
DEFAULT_SEEDS = [42, 88, 123]
RUNS_DIR = Path("eval_runs")


def _team_suffix() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=5))


def _run_one(
    strategy,
    scenario: str,
    seed: int,
    base_url: str,
    team_name: str,
    capture_days: bool,
) -> dict:
    """Run a single game, capturing per-day snapshots."""
    days_log: list[dict] = []
    transport = httpx.HTTPTransport(retries=3)
    started = time.time()
    try:
        with httpx.Client(base_url=base_url, timeout=60.0, transport=transport) as client:
            r = client.post("/games", json={
                "team_name": team_name,
                "scenario": scenario,
                "seed": seed,
            })
            r.raise_for_status()
            data = r.json()
            game_id = data["game_id"]
            observation = data["observation"]
            day = data["day"]

            for turn in range(30):
                tool_calls = strategy(observation, day)
                accepted, rejected = 0, 0
                rejection_msgs = []
                for tc in tool_calls:
                    rr = client.post(f"/games/{game_id}/action", json=tc)
                    rr.raise_for_status()
                    res = rr.json()
                    if res["status"] == "accepted":
                        accepted += 1
                    else:
                        rejected += 1
                        rejection_msgs.append(f"{tc.get('tool')}: {res.get('reason', '?')}")

                rr = client.post(f"/games/{game_id}/end-turn")
                rr.raise_for_status()
                turn_data = rr.json()
                observation = turn_data["observation"]
                day = turn_data["day"]
                status = turn_data["status"]
                dr = turn_data["day_result"]

                if capture_days:
                    days_log.append({
                        "d": day - 1,
                        "cov": dr.get("total_covers"),
                        "rev": dr.get("total_revenue"),
                        "cash": observation.get("cash"),
                        "rep": observation.get("reputation_band"),
                        "walk": (observation.get("service_summary") or {}).get("walkout_band"),
                        "util": (observation.get("service_summary") or {}).get("table_utilization_peak"),
                        "wx": observation.get("weather_today"),
                        "alerts": observation.get("alerts"),
                        "ok": accepted, "rej": rejected,
                        "rej_msgs": rejection_msgs[:3] if rejection_msgs else [],
                    })

                if status != "in_progress":
                    break

            rr = client.get(f"/games/{game_id}/score")
            rr.raise_for_status()
            score_data = rr.json()
            s = score_data["score"]
            elapsed = time.time() - started
            return {
                "scenario": scenario,
                "seed": seed,
                "team": team_name,
                "score": s["total_score"],
                "profit": s["net_profit"],
                "sat_pen": s["satisfaction_penalty"],
                "rep_pen": s["reputation_penalty"],
                "walk_pen": s["walkout_penalty"],
                "waste_pen": s["waste_penalty"],
                "days": score_data["days_survived"],
                "cash": score_data["final_cash"],
                "status": score_data["status"],
                "elapsed_s": round(elapsed, 1),
                "days_log": days_log,
            }
    except Exception as e:
        elapsed = time.time() - started
        return {
            "scenario": scenario,
            "seed": seed,
            "team": team_name,
            "score": -100_000,
            "status": "error",
            "error": str(e),
            "elapsed_s": round(elapsed, 1),
            "days_log": days_log,
        }


def evaluate(
    strategy,
    *,
    scenarios: list[str],
    seeds: list[int],
    base_url: str,
    team_prefix: str,
    parallel: int = 10,
    capture_days: bool = True,
) -> dict:
    jobs = [(sc, sd) for sc in scenarios for sd in seeds]
    results: list[dict] = []
    started_at = time.time()
    print(f"Running {len(jobs)} games in parallel (max_workers={parallel})...")
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        future_map = {}
        for sc, sd in jobs:
            team = f"{team_prefix}-{sc[:3]}{sd}-{_team_suffix()}"
            fut = pool.submit(_run_one, strategy, sc, sd, base_url, team, capture_days)
            future_map[fut] = (sc, sd, team)
        for fut in as_completed(future_map):
            r = fut.result()
            results.append(r)
            score = r.get("score", 0)
            status = r.get("status", "?")
            elapsed = r.get("elapsed_s", 0)
            print(f"  done [{r['scenario']:<14} s={r['seed']}] score={score:>+8.0f} status={status} ({elapsed:.0f}s)")
    elapsed_total = time.time() - started_at
    results.sort(key=lambda r: (r["scenario"], r["seed"]))
    return {"results": results, "elapsed_s": round(elapsed_total, 1)}


def summarize(data: dict) -> None:
    results = data["results"]
    print("\n" + "=" * 80)
    print(f"  EVAL SUMMARY  ({data['elapsed_s']:.0f}s total)")
    print("=" * 80)
    print(f"{'Scenario':<16} {'Seed':>6} {'Score':>10} {'Profit':>10} {'Walk':>7} {'Rep':>8} {'Sat':>8} {'Waste':>7} {'Status':<10}")
    print("-" * 90)
    by_scen: dict[str, list[float]] = {}
    bankruptcies = 0
    for r in results:
        s = r.get("score", 0)
        print(f"{r['scenario']:<16} {r['seed']:>6} {s:>10.0f} "
              f"{r.get('profit',0):>10.0f} {r.get('walk_pen',0):>7.0f} "
              f"{r.get('rep_pen',0):>8.0f} {r.get('sat_pen',0):>8.0f} "
              f"{r.get('waste_pen',0):>7.0f} {r.get('status','?'):<10}")
        by_scen.setdefault(r["scenario"], []).append(s)
        if r.get("status") == "bankrupt":
            bankruptcies += 1

    print("-" * 90)
    print(f"\n{'Scenario':<16} {'Avg':>10} {'Min':>10} {'Max':>10}  Games")
    for sc, arr in by_scen.items():
        print(f"{sc:<16} {sum(arr)/len(arr):>10.0f} {min(arr):>10.0f} {max(arr):>10.0f}  {len(arr)}")
    all_scores = [r.get("score", 0) for r in results]
    avg = sum(all_scores) / len(all_scores) if all_scores else 0
    print(f"\n  AVERAGE: {avg:+,.0f}   Bankruptcies: {bankruptcies}/{len(results)}")
    print("=" * 80)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scenarios", default=",".join(DEFAULT_SCENARIOS))
    p.add_argument("--seeds", default=",".join(str(s) for s in DEFAULT_SEEDS))
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--team-prefix", default="det")
    p.add_argument("--parallel", type=int, default=10)
    p.add_argument("--agent", default="agents.det_agent",
                   help="Dotted module exposing strategy()")
    p.add_argument("--no-capture", action="store_true",
                   help="Skip per-day logging (faster)")
    p.add_argument("--no-llm", action="store_true",
                   help="Disable LLM advisor for this run")
    p.add_argument("--label", default="",
                   help="Label appended to the saved JSON filename")
    args = p.parse_args()

    if args.no_llm:
        os.environ["DET_AGENT_LLM"] = "0"

    mod = importlib.import_module(args.agent)
    strategy = mod.strategy

    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    seeds = [int(s) for s in args.seeds.split(",")]

    print(f"Agent:     {args.agent}")
    print(f"Server:    {args.url}")
    print(f"Scenarios: {scenarios}")
    print(f"Seeds:     {seeds}")
    print(f"LLM:       {'OFF' if args.no_llm else 'ON'}")

    data = evaluate(
        strategy,
        scenarios=scenarios, seeds=seeds, base_url=args.url,
        team_prefix=args.team_prefix, parallel=args.parallel,
        capture_days=not args.no_capture,
    )
    summarize(data)

    # Persist
    RUNS_DIR.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    label = f"-{args.label}" if args.label else ""
    out_path = RUNS_DIR / f"run-{ts}{label}.json"
    with out_path.open("w") as f:
        json.dump(data, f, indent=2)
    print(f"\nSaved {out_path}")


if __name__ == "__main__":
    main()
