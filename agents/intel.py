"""Leaderboard intelligence — find patterns in what top teams do.

Pulls every public leaderboard entry per scenario, groups by team, and reports:
  - Which teams dominate which scenarios
  - Which seeds are 'easy mode' (everyone scores high)
  - Whether top teams are consistent or one-trick ponies
  - Top score distribution per scenario
"""

from __future__ import annotations

import os
import statistics
from collections import defaultdict

import httpx


BASE_URL = os.getenv("RESTBENCH_URL", "http://localhost:8001")
SCENARIOS = ["baseline", "supply_crisis", "tourist_season", "renovation"]


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        all_entries: list[dict] = []
        for sc in SCENARIOS:
            r = client.get("/leaderboard", params={"scenario": sc})
            r.raise_for_status()
            all_entries.extend(r.json())

    print(f"Total entries: {len(all_entries)}")

    # Group by scenario, find top
    by_scenario: dict[str, list[dict]] = defaultdict(list)
    for e in all_entries:
        by_scenario[e["scenario"]].append(e)

    print("\n" + "=" * 90)
    print("TOP 10 PER SCENARIO")
    print("=" * 90)
    for sc in SCENARIOS:
        entries = sorted(by_scenario[sc], key=lambda x: -x["score"])[:10]
        print(f"\n--- {sc} ---")
        print(f"  {'Team':<28} {'Score':>10} {'Seed':>5}")
        for e in entries:
            print(f"  {e['team_name']:<28} {e['score']:>10.0f} {e['seed']:>5}")

    # Find teams that show up in top 10 of MULTIPLE scenarios — robust teams
    print("\n" + "=" * 90)
    print("ROBUST TEAMS — appear in top 10 of MULTIPLE scenarios")
    print("=" * 90)
    team_scenarios: dict[str, set] = defaultdict(set)
    team_top_scores: dict[str, list[tuple]] = defaultdict(list)
    for sc in SCENARIOS:
        top10 = sorted(by_scenario[sc], key=lambda x: -x["score"])[:10]
        for e in top10:
            team_scenarios[e["team_name"]].add(sc)
            team_top_scores[e["team_name"]].append((sc, e["score"], e["seed"]))

    multi_scenario_teams = sorted(
        [(t, scs, team_top_scores[t]) for t, scs in team_scenarios.items() if len(scs) >= 2],
        key=lambda x: -len(x[1]),
    )
    for team, scs, scores in multi_scenario_teams[:15]:
        print(f"  {team:<28}  in {len(scs)} scenarios:")
        for sc, score, seed in sorted(scores, key=lambda x: -x[1]):
            print(f"      {sc:<18} score={score:>10.0f}  seed={seed}")

    # Seed difficulty analysis — which seeds yield the highest scores?
    print("\n" + "=" * 90)
    print("SEED DIFFICULTY (median of top-5 scores per (scenario, seed))")
    print("=" * 90)
    by_sc_seed: dict[tuple, list[float]] = defaultdict(list)
    for e in all_entries:
        by_sc_seed[(e["scenario"], e["seed"])].append(e["score"])

    print(f"  {'Scenario':<18} {'Seed':>5} {'N':>3} {'Top':>10} {'Median(top5)':>13} {'Max':>10}")
    for (sc, sd), scores in sorted(by_sc_seed.items()):
        scores_sorted = sorted(scores, reverse=True)
        top = scores_sorted[0]
        median_top5 = statistics.median(scores_sorted[:5]) if len(scores_sorted) >= 1 else 0
        print(f"  {sc:<18} {sd:>5} {len(scores):>3} {top:>10.0f} {median_top5:>13.0f} {max(scores):>10.0f}")

    # What's the highest BASELINE score? And tourist_season?
    print("\n" + "=" * 90)
    print("CEILING SCORES PER SCENARIO")
    print("=" * 90)
    for sc in SCENARIOS:
        top_score = max(by_scenario[sc], key=lambda x: x["score"])
        print(f"  {sc:<18} top = {top_score['score']:>10.0f}  ({top_score['team_name']} seed {top_score['seed']})")

    # Our team entries
    print("\n" + "=" * 90)
    print("OUR ENTRIES (zzz_*)")
    print("=" * 90)
    ours = [e for e in all_entries if "zzz" in e["team_name"].lower() or "mpc" in e["team_name"].lower() or "glittery" in e["team_name"].lower()]
    print(f"  {'Team':<30} {'Score':>10} {'Scenario':<18} {'Seed':>5}")
    for e in sorted(ours, key=lambda x: -x["score"])[:20]:
        print(f"  {e['team_name']:<30} {e['score']:>10.0f} {e['scenario']:<18} {e['seed']:>5}")


if __name__ == "__main__":
    main()
