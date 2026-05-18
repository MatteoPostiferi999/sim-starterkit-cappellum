"""Play one MPC game with maximum verbosity — every day fully explained.

Usage:
    RESTBENCH_URL=http://52.48.183.209:8001 \
      python -m agents.mpc.play_verbose <scenario> <seed>

Shows for every day:
  - Date/DOW, weather (today + 3-day forecast)
  - Alerts that fired today
  - Signal detector state (which flags are on)
  - Mode (NORMAL/SURGE/RENOVATION/etc.)
  - Top 3 candidate actions the MPC evaluated, with projected scores
  - Chosen action (staff, prices, marketing, happy hour, daily special)
  - Orders placed today + supplier delivery schedule
  - Inventory snapshot (top ingredients)
  - Actual outcome (covers, walkouts, revenue, cash delta)
  - SIMULATOR ACCURACY: predicted vs actual (a.k.a. how trustworthy is the twin)
  - Reputation transition
"""

from __future__ import annotations

import json
import os
import sys

import httpx

from agents.mpc.mpc_agent import strategy, _build_sim_state, _pick_best_action
from agents.mpc.candidates import enumerate_candidates
from agents.mpc.simulator import predict_day, simulate_horizon
from agents.mpc.candidates import default_future_action
from agents.glittery import state as state_mod, memory as memory_mod, signals as signals_mod
from agents.glittery import policies


GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"; BLUE = "\033[94m"
BOLD = "\033[1m"; DIM = "\033[2m"; RESET = "\033[0m"


def color(v: float, good_pos: bool = True) -> str:
    if v > 0:
        return GREEN if good_pos else RED
    elif v < 0:
        return RED if good_pos else GREEN
    return DIM


def main() -> None:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 42
    base_url = os.getenv("RESTBENCH_URL", "http://localhost:8001")
    team = os.getenv("TEAM_NAME", "zzz_mpc_verbose")

    with httpx.Client(base_url=base_url, timeout=60.0) as client:
        r = client.post("/games", json={"team_name": team, "scenario": scenario, "seed": seed})
        r.raise_for_status()
        data = r.json()
        game_id = data["game_id"]
        observation = data["observation"]
        day = data["day"]

        print(f"\n{BOLD}{'═' * 90}{RESET}")
        print(f"{BOLD}  GAME: {scenario} (seed {seed})  ·  game_id {game_id[:8]}…{RESET}")
        print(f"{BOLD}{'═' * 90}{RESET}")

        prev_cash = observation["cash"]
        prev_rep = observation.get("reputation_band", "Very Good")
        total_walkouts_est = 0
        prediction_errors = []

        for turn in range(30):
            print()
            print(f"{BOLD}{'━' * 90}{RESET}")
            print(f"{BOLD}  DAY {day}  ·  {observation['day_of_week']}  ·  "
                  f"weather: {observation.get('weather_today', '?')}"
                  f"  (forecast: {' → '.join(observation.get('weather_forecast', [])[:3])}){RESET}")
            print(f"{BOLD}{'━' * 90}{RESET}")

            # ── State summary ──
            cash = observation["cash"]
            rep = observation.get("reputation_band", "?")
            staff = observation.get("staff_level", 8)
            trend = observation.get("customer_trend", "?")
            print(f"  cash €{cash:,.0f}  ·  reputation: {rep}  ·  yesterday staff: {staff}  ·  trend: {trend}")

            # ── Alerts ──
            alerts = observation.get("alerts", [])
            if alerts:
                print(f"  {YELLOW}⚠ ALERTS:{RESET}")
                for a in alerts:
                    print(f"      {YELLOW}→ {a}{RESET}")

            # ── Yesterday's service (if day > 1) ──
            if day > 1:
                ss = observation.get("service_summary", {})
                covers = ss.get("total_covers", 0)
                revenue = ss.get("total_revenue", 0)
                walkout_band = ss.get("walkout_band", "None")
                wait = ss.get("peak_wait_minutes", 0)
                util = ss.get("table_utilization_peak", 0)
                kb = ss.get("kitchen_bottleneck_hours", [])
                wb_map = {"None": 0, "Few": 3, "Some": 12, "Many": 35}
                wb_est = wb_map.get(walkout_band, 0)
                total_walkouts_est += wb_est

                wb_color = RED if walkout_band in ("Some", "Many") else GREEN
                print(f"  {DIM}yesterday: covers={covers}  revenue=€{revenue:.0f}  "
                      f"walkouts={wb_color}{walkout_band}{RESET}{DIM} (~{wb_est})  "
                      f"peak_wait={wait:.0f}m  table_util={util:.0%}{RESET}")
                if kb:
                    print(f"  {RED}  kitchen bottleneck hours: {kb}{RESET}")
                stockouts = ss.get("dishes_unavailable_at", {})
                if stockouts:
                    print(f"  {RED}  STOCKOUTS yesterday:{RESET}")
                    for dish, hour in stockouts.items():
                        print(f"      {RED}→ {dish} ran out at hour {hour}{RESET}")

            # ── Compute signals to show to user ──
            state = state_mod.parse(observation, day)
            mem = memory_mod.load(state.notes_raw)
            sig = signals_mod.compute(state, mem)
            mem_after = memory_mod.update(mem, state, sig)
            mode = policies.decide_mode(state, sig, mem_after)

            active_signals = [k for k, v in sig.__dict__.items()
                              if isinstance(v, bool) and v]
            print(f"  signals: {', '.join(active_signals) if active_signals else 'none'}")
            print(f"  mode: {BOLD}{mode}{RESET}")
            if sig.supply_disruption:
                print(f"  {RED}supply disruption flags: {list(sig.supply_disruption.keys())}{RESET}")

            # ── Top 3 MPC candidates ──
            sim_state = _build_sim_state(state, mem_after)
            candidates = enumerate_candidates(state, sig)
            scored = []
            for c in candidates:
                s = simulate_horizon(sim_state, c, default_future_action, horizon=3)
                scored.append((s, c))
            scored.sort(key=lambda x: -x[0])

            print(f"  {BOLD}MPC evaluated {len(candidates)} candidates. Top 3:{RESET}")
            for i, (s, c) in enumerate(scored[:3]):
                marker = f"{GREEN}★{RESET}" if i == 0 else " "
                print(f"   {marker} #{i+1} staff={c.staff:>2}  px×{c.price_mult:.2f}  "
                      f"mkt={c.marketing:>3}  hh={'Y' if c.happy_hour else 'N'}   "
                      f"→ projected_score={s:+.0f}")

            # ── Today's prediction (with chosen action) ──
            best = scored[0][1]
            day1_pred = predict_day(sim_state, best)
            print(f"  {BOLD}TWIN PREDICTION (with chosen action):{RESET}")
            print(f"    demand≈{day1_pred.demand:.0f}  capacity_limited→served≈{day1_pred.served:.0f}  "
                  f"walkouts≈{day1_pred.walkouts:.0f}  "
                  f"revenue≈€{day1_pred.revenue:.0f}  profit≈€{day1_pred.profit:.0f}")

            # ── Run the actual strategy and submit ──
            actions = strategy(observation, day)
            order_actions = [a for a in actions if a["tool"] == "place_order"]
            if order_actions:
                print(f"  {BOLD}ORDERS today ({len(order_actions)}):{RESET}")
                for a in order_actions:
                    print(f"    → {a['args']['quantity_kg']}kg {a['args']['ingredient']} from {a['args']['supplier']}")

            rejected = 0
            for a in actions:
                rr = client.post(f"/games/{game_id}/action", json=a)
                rr.raise_for_status()
                if rr.json().get("status") != "accepted":
                    rejected += 1
                    print(f"    {RED}REJECTED: {a} → {rr.json().get('reason')}{RESET}")

            rr = client.post(f"/games/{game_id}/end-turn")
            rr.raise_for_status()
            td = rr.json()
            dr = td["day_result"]
            observation = td["observation"]
            new_day = td["day"]
            status = td["status"]

            # ── Actual outcome ──
            actual_covers = dr.get("total_covers", 0)
            actual_rev = dr.get("total_revenue", 0)
            actual_wo = dr.get("walkout_band", "None")
            cash_delta = observation["cash"] - prev_cash
            new_rep = observation.get("reputation_band", "?")

            covers_err = actual_covers - day1_pred.served
            rev_err = actual_rev - day1_pred.revenue
            prediction_errors.append((day, day1_pred.served, actual_covers, day1_pred.revenue, actual_rev))

            print(f"  {BOLD}ACTUAL OUTCOME:{RESET}")
            wb_color = RED if actual_wo in ("Some", "Many") else (YELLOW if actual_wo == "Few" else GREEN)
            cd_color = GREEN if cash_delta > 0 else RED
            print(f"    covers={actual_covers}  revenue=€{actual_rev:.0f}  walkouts={wb_color}{actual_wo}{RESET}  "
                  f"cashΔ={cd_color}€{cash_delta:+,.0f}{RESET}")

            # Twin accuracy
            cov_acc = "✓" if abs(covers_err) <= 15 else ("≈" if abs(covers_err) <= 30 else "✗")
            print(f"    {DIM}twin accuracy: covers {cov_acc} (predicted {day1_pred.served:.0f}, off by {covers_err:+.0f})  "
                  f"revenue off by €{rev_err:+.0f}{RESET}")

            # Reputation change
            if new_rep != prev_rep:
                rep_color = RED if "Poor" in new_rep or "Fair" in new_rep else (GREEN if "Excellent" in new_rep else YELLOW)
                print(f"    {rep_color}REPUTATION: {prev_rep} → {new_rep}{RESET}")
            prev_rep = new_rep
            prev_cash = observation["cash"]

            day = new_day
            if status != "in_progress":
                print(f"\n  Game ended: {status}")
                break

        # ── Final score ──
        rr = client.get(f"/games/{game_id}/score")
        rr.raise_for_status()
        s = rr.json()["score"]
        print()
        print(f"{BOLD}{'═' * 90}{RESET}")
        print(f"{BOLD}  FINAL SCORE: {color(s['total_score'])}{s['total_score']:+,.0f}{RESET}{BOLD}{RESET}")
        print(f"{BOLD}{'═' * 90}{RESET}")
        print(f"  net profit:           {color(s['net_profit'])}€{s['net_profit']:+,.0f}{RESET}")
        print(f"  walkout penalty:      {RED}€{s['walkout_penalty']:,.0f}{RESET}")
        print(f"  reputation penalty:   {RED}€{s['reputation_penalty']:,.0f}{RESET}")
        print(f"  satisfaction penalty: {RED}€{s['satisfaction_penalty']:,.0f}{RESET}")
        print(f"  waste penalty:        {RED}€{s['waste_penalty']:,.0f}{RESET}")
        print(f"  estimated walkouts:   ~{total_walkouts_est}")

        # ── Twin accuracy summary ──
        if prediction_errors:
            avg_cov_err = sum(abs(p[1] - p[2]) for p in prediction_errors) / len(prediction_errors)
            avg_rev_err = sum(abs(p[3] - p[4]) for p in prediction_errors) / len(prediction_errors)
            print()
            print(f"{BOLD}  TWIN ACCURACY (across {len(prediction_errors)} days):{RESET}")
            print(f"    avg |covers error|:  {avg_cov_err:.0f}")
            print(f"    avg |revenue error|: €{avg_rev_err:.0f}")


if __name__ == "__main__":
    main()
