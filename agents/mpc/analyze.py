"""Run one MPC game with full per-day diagnostics to find where score leaks.

Usage:
    RESTBENCH_URL=http://52.48.183.209:8001 \
      python -m agents.mpc.analyze <scenario> <seed>
"""

from __future__ import annotations

import json
import os
import sys

import httpx

from agents.mpc import strategy
from agents.glittery import state as state_mod, memory as memory_mod, signals as signals_mod
from agents.mpc.mpc_agent import _build_sim_state, _pick_best_action
from agents.mpc.candidates import enumerate_candidates


def main() -> None:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 42
    base_url = os.getenv("RESTBENCH_URL", "http://localhost:8001")
    team = os.getenv("TEAM_NAME", "zzz_mpc_diag")

    with httpx.Client(base_url=base_url, timeout=60.0) as client:
        r = client.post("/games", json={"team_name": team, "scenario": scenario, "seed": seed})
        r.raise_for_status()
        data = r.json()
        game_id = data["game_id"]
        observation = data["observation"]
        day = data["day"]

        print(f"=== Game {game_id[:8]} ({scenario}, seed={seed}) ===")
        print(f"{'D':>3} {'DOW':<9} {'Wx':<7} {'Cash':>6} {'Rep':<10} {'Staff':>5} {'PxM':>5} {'Mkt':>4} {'HH':>3} "
              f"{'ProjScore':>10} {'Covers':>7} {'Walk':<6} {'Rev':>6} {'Prof':>7} {'CashΔ':>7}")
        print("-" * 130)

        cum_walkouts = 0
        cum_profit = 0.0
        prev_cash = observation["cash"]

        for turn in range(30):
            # Run strategy to get plan
            actions = strategy(observation, day)

            # Also peek at what MPC picked (for diagnostics)
            state = state_mod.parse(observation, day)
            mem = memory_mod.load(state.notes_raw)
            sig = signals_mod.compute(state, mem)
            mem = memory_mod.update(mem, state, sig)
            sim_state = _build_sim_state(state, mem)
            candidates = enumerate_candidates(state, sig)
            best, best_score = _pick_best_action(sim_state, candidates) if candidates else (None, 0)

            # Submit actions
            for a in actions:
                rr = client.post(f"/games/{game_id}/action", json=a)
                rr.raise_for_status()

            rr = client.post(f"/games/{game_id}/end-turn")
            rr.raise_for_status()
            td = rr.json()
            dr = td["day_result"]
            observation = td["observation"]
            new_day = td["day"]
            status = td["status"]

            cash_now = observation["cash"]
            cash_delta = cash_now - prev_cash
            cum_profit += cash_delta
            walkout_band = dr.get("walkout_band", "?")
            wb_to_num = {"None": 0, "Few": 3, "Some": 12, "Many": 35}
            cum_walkouts += wb_to_num.get(walkout_band, 0)

            wx = state.weather_today[:6]
            best_str = f"s{best.staff} p{best.price_mult:.2f} m{best.marketing} {'H' if best.happy_hour else '-'}" if best else "—"
            print(f"{day:>3} {state.day_of_week:<9} {wx:<7} {state.cash:>6.0f} {state.reputation_band:<10} "
                  f"{best.staff:>5} {best.price_mult:>5.2f} {best.marketing:>4} {'Y' if best.happy_hour else 'N':>3} "
                  f"{best_score:>10.0f} {dr.get('total_covers', 0):>7} {walkout_band:<6} "
                  f"{dr.get('total_revenue', 0):>6.0f} {cash_delta:>7.0f} {cash_now-prev_cash:>7.0f}")
            prev_cash = cash_now
            day = new_day
            if status != "in_progress":
                break

        rr = client.get(f"/games/{game_id}/score")
        rr.raise_for_status()
        s = rr.json()["score"]
        print()
        print(f"FINAL: total={s['total_score']:.0f}  profit={s['net_profit']:.0f}  "
              f"walkout_pen={s['walkout_penalty']:.0f}  rep_pen={s['reputation_penalty']:.0f}  "
              f"sat_pen={s['satisfaction_penalty']:.0f}  waste_pen={s['waste_penalty']:.0f}")
        print(f"Estimated walkouts: {cum_walkouts}")


if __name__ == "__main__":
    main()
