"""Reconnaissance — harvest every piece of server-side data for offline analysis.

The simulator source code was stripped from the starter kit. Everything we need
to understand (supplier catalog, recipes, alerts, weather patterns, day_result
schema, supplier reliability under disruptions, scoring breakdown) lives on the
server and is only revealed through observations during play.

This script:
  Phase A — hits free endpoints (no game needed): /health, /scenarios, /leaderboard
  Phase B — runs a minimal-survival agent against each (scenario, seed) and dumps
            every observation, day_result, and final score to disk
  Phase C — aggregates the raw dumps into a structured summary (suppliers,
            recipes, alerts, weather sequences, supplier reliability, finals)
            plus a single SUMMARY.md to read at a glance.

Usage:
    python -m agents.recon                                  # full recon (4 sc × 3 seeds = 12 games)
    python -m agents.recon --scenarios baseline             # one scenario
    python -m agents.recon --seeds 42                       # one seed
    python -m agents.recon --no-games                       # free endpoints only
    python -m agents.recon --force                          # re-run games even if data exists

Output goes to ./recon_data/.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

DEFAULT_URL = os.getenv("RESTBENCH_URL", "http://localhost:8001")
DEFAULT_TEAM = "recon_agent"
DEFAULT_SCENARIOS = ["baseline", "supply_crisis", "tourist_season", "renovation"]
DEFAULT_SEEDS = [42, 88, 123]
DEFAULT_PARALLEL = 5
OUT_DIR = Path("recon_data")


# ─────────────────────────── Phase A: free endpoints ───────────────────────────

def _get(client: httpx.Client, path: str, **kwargs):
    try:
        r = client.get(path, **kwargs)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  ✗ GET {path}: {e}")
        return None


def harvest_free_endpoints(base_url: str, scenarios: list[str]) -> dict | None:
    meta_dir = OUT_DIR / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)

    print("Phase A: free endpoints")
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        health = _get(client, "/health")
        if health is not None:
            (meta_dir / "health.json").write_text(json.dumps(health, indent=2))
            print(f"  ✓ /health → {health}")

        all_scenarios = _get(client, "/scenarios")
        if all_scenarios is not None:
            (meta_dir / "scenarios.json").write_text(json.dumps(all_scenarios, indent=2))
            names = [s.get("name", str(s)) if isinstance(s, dict) else str(s) for s in all_scenarios]
            print(f"  ✓ /scenarios → {names}")

        # Per-scenario leaderboards (calibration: see what other teams score)
        for sc in scenarios:
            lb = _get(client, "/leaderboard", params={"scenario": sc})
            if lb is not None:
                (meta_dir / f"leaderboard_{sc}.json").write_text(json.dumps(lb, indent=2))
                count = len(lb) if isinstance(lb, list) else "?"
                print(f"  ✓ /leaderboard?scenario={sc} ({count} entries)")

        # Global leaderboard too
        lb_all = _get(client, "/leaderboard")
        if lb_all is not None:
            (meta_dir / "leaderboard_all.json").write_text(json.dumps(lb_all, indent=2))
            count = len(lb_all) if isinstance(lb_all, list) else "?"
            print(f"  ✓ /leaderboard ({count} entries)")

    return all_scenarios


# ──────────────────── Phase B: minimal-survival recon agent ────────────────────

def _survival_actions(observation: dict, day: int) -> list[dict]:
    """Cheap heuristic that keeps the restaurant alive 30 days for data collection.

    Not optimized — only intended to harvest a full 30-day arc per scenario.
    """
    actions: list[dict] = []

    if day == 1:
        actions.append({"tool": "set_staff_level", "args": {"level": 6}})

    cheapest: dict[str, tuple[str, float, float]] = {}
    for sup in observation.get("supplier_catalog", []):
        for ingredient, price in sup["ingredients"].items():
            if ingredient not in cheapest or price < cheapest[ingredient][1]:
                cheapest[ingredient] = (sup["name"], price, sup["min_order_kg"])

    pending: dict[str, float] = defaultdict(float)
    for po in observation.get("pending_orders", []):
        pending[po["ingredient"]] += po["quantity_kg"]

    stock: dict[str, float] = {}
    for inv in observation.get("inventory", []):
        long_life = sum(b["quantity_kg"] for b in inv.get("batches", []) if b["expires_in_days"] > 1)
        stock[inv["ingredient"]] = long_life

    cash = observation.get("cash", 0.0)
    budget = max(0.0, cash - 2000.0)

    REORDER_BELOW = 3.0
    BASE_ORDER_QTY = 6.0

    for ing, (sup_name, price, min_qty) in cheapest.items():
        effective = stock.get(ing, 0) + pending[ing]
        if effective < REORDER_BELOW:
            qty = max(BASE_ORDER_QTY, min_qty)
            cost = qty * price
            if cost > budget:
                continue
            actions.append({
                "tool": "place_order",
                "args": {"supplier": sup_name, "ingredient": ing, "quantity_kg": round(qty, 1)},
            })
            budget -= cost

    return actions


def harvest_game(scenario: str, seed: int, base_url: str, team_name: str, force: bool) -> dict:
    out = OUT_DIR / "games" / f"{scenario}_seed{seed}"
    score_path = out / "final_score.json"
    if score_path.exists() and not force:
        try:
            score = json.loads(score_path.read_text())
            return {
                "scenario": scenario, "seed": seed, "status": "cached",
                "days": score.get("days_survived"),
                "score": score.get("score", {}).get("total_score"),
            }
        except Exception:
            pass

    out.mkdir(parents=True, exist_ok=True)

    observations: list[dict] = []
    day_results: list[dict] = []
    rejected: list[dict] = []

    try:
        transport = httpx.HTTPTransport(retries=3)
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
            observations.append({"day": day, "observation": observation})

            for _ in range(30):
                for action in _survival_actions(observation, day):
                    rr = client.post(f"/games/{game_id}/action", json=action)
                    rr.raise_for_status()
                    res = rr.json()
                    if res.get("status") != "accepted":
                        rejected.append({"day": day, "action": action, "reason": res.get("reason")})

                rr = client.post(f"/games/{game_id}/end-turn")
                rr.raise_for_status()
                td = rr.json()

                observation = td["observation"]
                new_day = td["day"]
                status = td["status"]
                day_results.append({"day_completed": new_day - 1, "result": td.get("day_result")})
                observations.append({"day": new_day, "observation": observation})
                day = new_day

                if status != "in_progress":
                    break

            r = client.get(f"/games/{game_id}/score")
            r.raise_for_status()
            score = r.json()

        with (out / "observations.jsonl").open("w") as f:
            for o in observations:
                f.write(json.dumps(o) + "\n")
        with (out / "day_results.jsonl").open("w") as f:
            for d in day_results:
                f.write(json.dumps(d) + "\n")
        (out / "final_score.json").write_text(json.dumps(score, indent=2))
        (out / "rejected_actions.json").write_text(json.dumps(rejected, indent=2))

        return {
            "scenario": scenario, "seed": seed, "status": "ok",
            "days": len(observations) - 1,
            "score": score.get("score", {}).get("total_score"),
        }
    except Exception as e:
        return {"scenario": scenario, "seed": seed, "status": f"error: {e}"}


def harvest_games(scenarios, seeds, base_url, team_name, parallel, force) -> list[dict]:
    (OUT_DIR / "games").mkdir(parents=True, exist_ok=True)

    jobs = [(sc, sd) for sc in scenarios for sd in seeds]
    print(f"\nPhase B: harvesting {len(jobs)} games (parallel={parallel}, force={force})")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {
            pool.submit(harvest_game, sc, sd, base_url, team_name, force): (sc, sd)
            for sc, sd in jobs
        }
        for fut in as_completed(futures):
            r = fut.result()
            score_str = f"{r['score']:.0f}" if isinstance(r.get("score"), (int, float)) else "—"
            print(f"  ✓ {r['scenario']:<16} seed={r['seed']}: {r['status']:<10} days={r.get('days', '?')} score={score_str}")
            results.append(r)
    return results


# ──────────────────────────── Phase C: build summary ───────────────────────────

def build_summary(scenarios: list[str], seeds: list[int]) -> None:
    summary_dir = OUT_DIR / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    print("\nPhase C: building summary")

    suppliers: dict[str, dict] = {}          # name -> snapshot + seen_in
    supplier_price_drift: dict[str, dict] = defaultdict(lambda: defaultdict(set))  # supplier -> ingredient -> {prices}
    recipes: dict[str, dict] = {}
    alerts_by_scenario: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    weather_sequences: dict[str, list] = {}
    delivery_obs: dict[str, list] = defaultdict(list)  # supplier -> list of (ordered, delivered, on_time)
    scenario_finals: dict[str, list] = defaultdict(list)
    day_result_schema_keys: set = set()
    observation_schema_keys: set = set()
    service_summary_keys: set = set()

    for sc in scenarios:
        for sd in seeds:
            game_dir = OUT_DIR / "games" / f"{sc}_seed{sd}"
            obs_path = game_dir / "observations.jsonl"
            if not obs_path.exists():
                continue

            weather_seq = []
            for line in obs_path.read_text().splitlines():
                rec = json.loads(line)
                obs = rec["observation"]
                day = rec["day"]

                observation_schema_keys.update(obs.keys())
                if obs.get("service_summary"):
                    service_summary_keys.update(obs["service_summary"].keys())

                for sup in obs.get("supplier_catalog", []):
                    name = sup["name"]
                    if name not in suppliers:
                        suppliers[name] = {
                            "lead_time_days": sup["lead_time_days"],
                            "delivery_days": sup["delivery_days"],
                            "min_order_kg": sup["min_order_kg"],
                            "ingredients": dict(sup["ingredients"]),
                            "seen_in": [],
                        }
                    tag = f"{sc}_seed{sd}"
                    if tag not in suppliers[name]["seen_in"]:
                        suppliers[name]["seen_in"].append(tag)
                    for ing, price in sup["ingredients"].items():
                        supplier_price_drift[name][ing].add(price)

                for dish in obs.get("menu_book", []):
                    if dish["name"] not in recipes:
                        recipes[dish["name"]] = {
                            "category": dish.get("category"),
                            "base_price": dish["base_price"],
                            "ingredients": dish.get("ingredients"),
                        }

                for alert in obs.get("alerts", []) or []:
                    alerts_by_scenario[sc][alert].add(day)

                wt = obs.get("weather_today")
                if wt:
                    weather_seq.append({"day": day, "weather": wt})

                for dh in obs.get("delivery_history", []) or []:
                    delivery_obs[dh["supplier"]].append({
                        "scenario": sc, "seed": sd,
                        "ordered": dh["ordered_kg"],
                        "delivered": dh["delivered_kg"],
                        "on_time": dh.get("on_time"),
                    })

            weather_sequences[f"{sc}_seed{sd}"] = weather_seq

            dr_path = game_dir / "day_results.jsonl"
            if dr_path.exists():
                for line in dr_path.read_text().splitlines():
                    rec = json.loads(line)
                    if rec.get("result"):
                        day_result_schema_keys.update(rec["result"].keys())

            score_path = game_dir / "final_score.json"
            if score_path.exists():
                sd_data = json.loads(score_path.read_text())
                scenario_finals[sc].append({
                    "seed": sd,
                    "score": sd_data["score"]["total_score"],
                    "profit": sd_data["score"]["net_profit"],
                    "sat_pen": sd_data["score"].get("satisfaction_penalty"),
                    "rep_pen": sd_data["score"].get("reputation_penalty"),
                    "walk_pen": sd_data["score"].get("walkout_penalty"),
                    "waste_pen": sd_data["score"].get("waste_penalty"),
                    "status": sd_data["status"],
                    "days_survived": sd_data["days_survived"],
                    "final_cash": sd_data.get("final_cash"),
                })

    # Dedupe alerts to plain dicts
    alerts_out = {
        sc: [{"text": text, "days": sorted(days)} for text, days in items.items()]
        for sc, items in alerts_by_scenario.items()
    }

    # Supplier price drift summary
    price_drift_out = {}
    for sup, ingredients in supplier_price_drift.items():
        price_drift_out[sup] = {
            ing: {"distinct_prices": sorted(prices), "varied": len(prices) > 1}
            for ing, prices in ingredients.items()
        }

    # Supplier reliability across all observed delivery records
    supplier_reliability = {}
    for sup, records in delivery_obs.items():
        if not records:
            continue
        # Dedupe records (same delivery shows up in many delivery_history snapshots)
        unique = {(r["scenario"], r["seed"], r["ordered"], r["delivered"]): r for r in records}
        urecs = list(unique.values())
        total = len(urecs)
        on_time = sum(1 for r in urecs if r.get("on_time"))
        short = sum(1 for r in urecs if r["delivered"] < r["ordered"] - 1e-6)
        zero = sum(1 for r in urecs if r["delivered"] <= 1e-6 and r["ordered"] > 0)
        avg_fill = sum((r["delivered"] / r["ordered"]) for r in urecs if r["ordered"] > 0) / max(total, 1)
        supplier_reliability[sup] = {
            "samples": total,
            "on_time_rate": round(on_time / total, 3),
            "short_delivery_rate": round(short / total, 3),
            "zero_delivery_rate": round(zero / total, 3),
            "avg_fill_rate": round(avg_fill, 3),
        }

    # ---- write JSON outputs ----
    (summary_dir / "suppliers.json").write_text(json.dumps(suppliers, indent=2))
    (summary_dir / "supplier_price_drift.json").write_text(json.dumps(price_drift_out, indent=2))
    (summary_dir / "recipes.json").write_text(json.dumps(recipes, indent=2))
    (summary_dir / "alerts_by_scenario.json").write_text(json.dumps(alerts_out, indent=2))
    (summary_dir / "weather_sequences.json").write_text(json.dumps(weather_sequences, indent=2))
    (summary_dir / "supplier_reliability.json").write_text(json.dumps(supplier_reliability, indent=2))
    (summary_dir / "scenario_finals.json").write_text(json.dumps(dict(scenario_finals), indent=2))
    (summary_dir / "schema_keys.json").write_text(json.dumps({
        "observation_top_level": sorted(observation_schema_keys),
        "service_summary": sorted(service_summary_keys),
        "day_result": sorted(day_result_schema_keys),
    }, indent=2))

    # ---- write human-readable SUMMARY.md ----
    L: list[str] = ["# RestBench Recon Summary\n"]

    L.append(f"\n## Suppliers ({len(suppliers)})\n")
    for name, sup in sorted(suppliers.items()):
        L.append(f"- **{name}** — lead {sup['lead_time_days']}d, delivers {sup['delivery_days']}, min {sup['min_order_kg']}kg")
        L.append(f"  - ingredients: " + ", ".join(f"{k} (€{v}/kg)" for k, v in sup["ingredients"].items()))
        L.append(f"  - seen in: {', '.join(sup['seen_in'])}")

    drifted = {sup: ings for sup, ings in price_drift_out.items() if any(d["varied"] for d in ings.values())}
    if drifted:
        L.append(f"\n## Supplier price drift (price changed across observations)\n")
        for sup, ings in drifted.items():
            for ing, d in ings.items():
                if d["varied"]:
                    L.append(f"- **{sup}** / {ing}: {d['distinct_prices']}")

    L.append(f"\n## Recipes ({len(recipes)})\n")
    by_cat: dict[str, list] = defaultdict(list)
    for name, r in recipes.items():
        by_cat[r.get("category") or "?"].append((name, r))
    for cat in sorted(by_cat):
        L.append(f"\n### {cat}")
        for name, r in sorted(by_cat[cat]):
            ings = r.get("ingredients") or []
            ing_str = ", ".join(f"{i['ingredient']} {i['quantity_kg']}kg" for i in ings)
            L.append(f"- **{name}** — base €{r['base_price']} — uses: {ing_str}")

    L.append(f"\n## Alerts by scenario\n")
    for sc in scenarios:
        L.append(f"\n### `{sc}`")
        alerts = alerts_out.get(sc, [])
        if not alerts:
            L.append("  (none observed in survival recon)")
        for a in alerts:
            L.append(f"  - days {a['days']}: {a['text']}")

    L.append(f"\n## Supplier reliability (under survival recon)\n")
    for sup, rel in sorted(supplier_reliability.items(), key=lambda x: -x[1].get("on_time_rate", 0)):
        L.append(
            f"- **{sup}**: on-time {rel['on_time_rate']*100:.0f}%, "
            f"short {rel['short_delivery_rate']*100:.0f}%, "
            f"zero-fill {rel['zero_delivery_rate']*100:.0f}%, "
            f"avg fill {rel['avg_fill_rate']*100:.0f}% ({rel['samples']} unique deliveries)"
        )

    L.append(f"\n## Weather distribution (per scenario+seed)\n")
    for tag, seq in weather_sequences.items():
        if not seq:
            continue
        counts: dict[str, int] = defaultdict(int)
        for d in seq:
            counts[d["weather"]] += 1
        total = sum(counts.values())
        dist = ", ".join(f"{w}={c}/{total}" for w, c in sorted(counts.items(), key=lambda x: -x[1]))
        L.append(f"- **{tag}**: {dist}")

    L.append(f"\n## Scenario finals (survival recon agent)\n")
    for sc in scenarios:
        L.append(f"\n### `{sc}`")
        for r in sorted(scenario_finals.get(sc, []), key=lambda x: x["seed"]):
            L.append(
                f"  - seed={r['seed']}: total={r['score']:.0f}, profit={r['profit']:.0f}, "
                f"sat_pen={r['sat_pen']}, rep_pen={r['rep_pen']}, walk_pen={r['walk_pen']}, "
                f"waste_pen={r['waste_pen']}, days={r['days_survived']}, status={r['status']}, "
                f"final_cash={r['final_cash']}"
            )

    L.append(f"\n## Observation schema (top-level keys seen)\n")
    L.append("  " + ", ".join(sorted(observation_schema_keys)))
    L.append(f"\n## service_summary keys seen\n")
    L.append("  " + ", ".join(sorted(service_summary_keys)))
    L.append(f"\n## day_result keys seen\n")
    L.append("  " + ", ".join(sorted(day_result_schema_keys)))

    (OUT_DIR / "SUMMARY.md").write_text("\n".join(L))
    print(f"  ✓ wrote {OUT_DIR / 'SUMMARY.md'}")


# ────────────────────────────────── entrypoint ─────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Harvest server data for offline analysis")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Server URL (default: {DEFAULT_URL})")
    parser.add_argument("--scenarios", default=",".join(DEFAULT_SCENARIOS))
    parser.add_argument("--seeds", default=",".join(str(s) for s in DEFAULT_SEEDS))
    parser.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL)
    parser.add_argument("--team-name", default=DEFAULT_TEAM)
    parser.add_argument("--no-games", action="store_true", help="Skip Phase B; only free endpoints")
    parser.add_argument("--summary-only", action="store_true", help="Skip A+B; just rebuild summary from cache")
    parser.add_argument("--force", action="store_true", help="Re-run games even if cached data exists")
    args = parser.parse_args()

    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    OUT_DIR.mkdir(exist_ok=True)

    print(f"Server:    {args.url}")
    print(f"Scenarios: {scenarios}")
    print(f"Seeds:     {seeds}")
    print(f"Output:    {OUT_DIR.resolve()}\n")

    if not args.summary_only:
        harvest_free_endpoints(args.url, scenarios)
        if not args.no_games:
            harvest_games(scenarios, seeds, args.url, args.team_name, args.parallel, args.force)

    if not args.no_games:
        build_summary(scenarios, seeds)

    print(f"\nDone. Open {OUT_DIR / 'SUMMARY.md'}")


if __name__ == "__main__":
    main()
