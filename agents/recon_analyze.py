"""Offline deep-dive on harvested recon data.

Extracts scenario-specific signatures that the high-level summary missed:
  - supply_crisis: which supplier(s) fail and on which days
  - tourist_season: covers and revenue per day to reveal the surge curve
  - renovation: when the satisfaction bonus kicks in
  - baseline vs others: per-day cost burn and customer trend
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

RECON = Path("recon_data/games")
SCENARIOS = ["baseline", "supply_crisis", "tourist_season", "renovation"]
SEEDS = [42, 88, 123]


def load_obs(scenario: str, seed: int) -> list[dict]:
    p = RECON / f"{scenario}_seed{seed}" / "observations.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines()]


def load_day_results(scenario: str, seed: int) -> list[dict]:
    p = RECON / f"{scenario}_seed{seed}" / "day_results.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines()]


def supply_crisis_signature():
    print("\n" + "=" * 70)
    print("SUPPLY_CRISIS — when do deliveries fail?")
    print("=" * 70)

    for seed in SEEDS:
        obs_list = load_obs("supply_crisis", seed)
        # delivery_history accumulates, so use the FINAL observation's full log
        if not obs_list:
            continue
        final = obs_list[-1]["observation"]
        history = final.get("delivery_history", [])

        # Group by supplier × order_day
        per_supplier_per_day: dict[str, dict[int, dict]] = defaultdict(dict)
        for dh in history:
            sup = dh["supplier"]
            od = dh.get("order_day", -1)
            per_supplier_per_day[sup][od] = dh

        print(f"\n--- seed={seed} ---")
        for sup in sorted(per_supplier_per_day):
            records = per_supplier_per_day[sup]
            print(f"  {sup}:")
            for od in sorted(records):
                r = records[od]
                fill = r["delivered_kg"] / r["ordered_kg"] if r["ordered_kg"] else 0
                flag = " <-- ZERO" if r["delivered_kg"] <= 0.01 else (" <-- SHORT" if fill < 0.9 else "")
                print(f"    order_day={od:>2} → delivered={r['delivered_kg']:>6.2f}/{r['ordered_kg']:>6.2f} ({fill*100:>3.0f}%){flag}")

        # Also look at all alerts across all days
        seen_alerts: dict[str, list[int]] = defaultdict(list)
        for rec in obs_list:
            for a in rec["observation"].get("alerts", []) or []:
                seen_alerts[a].append(rec["day"])
        if seen_alerts:
            print(f"  alerts seen:")
            for text, days in seen_alerts.items():
                print(f"    days {days}: {text}")


def tourist_season_signature():
    print("\n" + "=" * 70)
    print("TOURIST_SEASON — demand curve")
    print("=" * 70)

    for seed in SEEDS:
        results = load_day_results("tourist_season", seed)
        obs_list = load_obs("tourist_season", seed)
        if not results:
            continue
        print(f"\n--- seed={seed} ---")
        print(f"  {'Day':>3} {'DOW':<10} {'Covers':>6} {'Revenue':>9} {'Walkout':<8} {'Trend':<10} {'Alerts'}")
        for i, r in enumerate(results, start=1):
            res = r.get("result") or {}
            # find observation for this day to get DOW + trend + alerts
            obs_for_day = next((rec for rec in obs_list if rec["day"] == i + 1), None)
            dow = obs_for_day["observation"]["day_of_week"] if obs_for_day else "?"
            trend = obs_for_day["observation"].get("customer_trend") if obs_for_day else "?"
            alerts = obs_for_day["observation"].get("alerts", []) if obs_for_day else []
            alert_str = "; ".join(alerts)[:60]
            print(f"  {i:>3} {dow:<10} {res.get('total_covers', '?'):>6} "
                  f"{res.get('total_revenue', '?'):>9} {res.get('walkout_band', '?'):<8} "
                  f"{trend:<10} {alert_str}")


def renovation_signature():
    print("\n" + "=" * 70)
    print("RENOVATION — capacity, walkouts, and bonus phase")
    print("=" * 70)

    for seed in SEEDS:
        results = load_day_results("renovation", seed)
        obs_list = load_obs("renovation", seed)
        if not results:
            continue
        print(f"\n--- seed={seed} ---")
        print(f"  {'Day':>3} {'DOW':<10} {'Covers':>6} {'Walkouts':<8} {'TableUtil':>9} {'Rep':<11} {'Alerts'}")
        for i, r in enumerate(results, start=1):
            res = r.get("result") or {}
            obs_for_day = next((rec for rec in obs_list if rec["day"] == i + 1), None)
            if obs_for_day:
                ss = obs_for_day["observation"].get("service_summary") or {}
                tu = ss.get("table_utilization_peak", 0)
                dow = obs_for_day["observation"]["day_of_week"]
                rep = obs_for_day["observation"].get("reputation_band", "?")
                alerts = obs_for_day["observation"].get("alerts", []) or []
            else:
                tu = 0
                dow = "?"
                rep = "?"
                alerts = []
            alert_str = "; ".join(alerts)[:50]
            print(f"  {i:>3} {dow:<10} {res.get('total_covers', '?'):>6} "
                  f"{res.get('walkout_band', '?'):<8} {tu:>9.2f} {rep:<11} {alert_str}")


def baseline_dow_pattern():
    print("\n" + "=" * 70)
    print("BASELINE — covers per day-of-week (avg across seeds)")
    print("=" * 70)

    dow_covers: dict[str, list[int]] = defaultdict(list)
    dow_revenue: dict[str, list[float]] = defaultdict(list)
    dow_walkout: dict[str, list[str]] = defaultdict(list)

    for seed in SEEDS:
        results = load_day_results("baseline", seed)
        obs_list = load_obs("baseline", seed)
        for i, r in enumerate(results, start=1):
            res = r.get("result") or {}
            obs_for_day = next((rec for rec in obs_list if rec["day"] == i + 1), None)
            dow = obs_for_day["observation"]["day_of_week"] if obs_for_day else "?"
            if res.get("total_covers") is not None:
                dow_covers[dow].append(res["total_covers"])
                dow_revenue[dow].append(res["total_revenue"])
                dow_walkout[dow].append(res.get("walkout_band", "?"))

    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    print(f"  {'DOW':<10} {'avg covers':>10} {'avg revenue':>12} {'walkouts most common':<30}")
    for dow in order:
        if dow not in dow_covers:
            continue
        avg_c = mean(dow_covers[dow])
        avg_r = mean(dow_revenue[dow])
        # mode of walkout bands
        counts: dict[str, int] = defaultdict(int)
        for w in dow_walkout[dow]:
            counts[w] += 1
        wo_mode = max(counts.items(), key=lambda x: x[1])[0]
        print(f"  {dow:<10} {avg_c:>10.1f} {avg_r:>12.1f} {wo_mode + ' (' + str(counts[wo_mode]) + '/' + str(len(dow_walkout[dow])) + ')':<30}")


def cross_scenario_comparison():
    print("\n" + "=" * 70)
    print("CROSS-SCENARIO — total covers and walkouts per scenario per seed")
    print("=" * 70)
    print(f"  {'Scenario':<16} {'Seed':<5} {'Days':>5} {'Total Covers':>12} {'Walkouts:None|Few|Some|Many':<30}")
    for sc in SCENARIOS:
        for sd in SEEDS:
            results = load_day_results(sc, sd)
            tc = 0
            wo: dict[str, int] = defaultdict(int)
            for r in results:
                res = r.get("result") or {}
                tc += res.get("total_covers", 0) or 0
                wo[res.get("walkout_band", "?")] += 1
            wo_str = f"{wo['None']}|{wo['Few']}|{wo['Some']}|{wo['Many']}"
            print(f"  {sc:<16} {sd:<5} {len(results):>5} {tc:>12} {wo_str:<30}")


def supplier_reliability_by_scenario():
    print("\n" + "=" * 70)
    print("SUPPLIER RELIABILITY — split by scenario")
    print("=" * 70)

    per_sc_sup: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for sc in SCENARIOS:
        for sd in SEEDS:
            obs_list = load_obs(sc, sd)
            if not obs_list:
                continue
            final = obs_list[-1]["observation"]
            seen = set()
            for dh in final.get("delivery_history", []):
                key = (sc, dh["supplier"])
                fingerprint = (dh.get("order_day"), dh["ordered_kg"], dh["delivered_kg"], sd)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                per_sc_sup[key].append((dh["ordered_kg"], dh["delivered_kg"]))

    print(f"  {'Scenario':<16} {'Supplier':<22} {'N':>4} {'Fill rate':>10} {'Zero-fill':>10}")
    for (sc, sup), records in sorted(per_sc_sup.items()):
        n = len(records)
        avg_fill = mean(d / o for o, d in records if o > 0)
        zero = sum(1 for o, d in records if d <= 0.01 and o > 0) / n
        print(f"  {sc:<16} {sup:<22} {n:>4} {avg_fill*100:>9.0f}% {zero*100:>9.0f}%")


if __name__ == "__main__":
    cross_scenario_comparison()
    baseline_dow_pattern()
    supplier_reliability_by_scenario()
    supply_crisis_signature()
    tourist_season_signature()
    renovation_signature()
