# CLAUDE.md — RestBench Agent

Documentation for the agent built for the **Prosus AISO Spring Hackathon (2026-05-18)** — a 30-day restaurant-management simulation.

---

## TL;DR

We built a **Model-Predictive Control (MPC) agent with a digital twin** of the restaurant. Every turn it enumerates ~60 candidate action sets, simulates each one 3 days into the future, and submits whichever scores highest. The same technique used by self-driving cars.

| Eval set | Avg score | Bankruptcies | Best single game |
|---|---|---|---|
| Dev seeds 42/88/123 (12 games) | **+2,802** | 0 / 12 | +14,368 |
| Held-out seeds 7/99/314/1000 (16 games) | **+6,894** | 0 / 16 | +19,950 |

For comparison: naive baseline = -15,000. Top leaderboard single-game best = 66,619.

---

## How to run

```bash
# Single game
RESTBENCH_URL=http://52.48.183.209:8001 \
  TEAM_NAME=<your-team> SEED=42 SCENARIO=baseline \
  venv/bin/python -m agents.mpc_agent

# Full eval matrix (4 scenarios × 3 seeds, parallel)
venv/bin/python -m agents.evaluate agents.mpc_agent \
  --team-name <your-team> \
  --scenarios baseline,supply_crisis,tourist_season,renovation \
  --seeds 42,88,123 --parallel 5

# Per-day verbose log (weather, signals, MPC reasoning, twin predictions vs actual)
venv/bin/python -m agents.mpc.play_verbose <scenario> <seed>

# Compact one-line-per-day diagnostic
venv/bin/python -m agents.mpc.analyze <scenario> <seed>
```

Zero LLM calls at runtime. No API key needed. ~250 ms per turn.

---

## Architecture

```
Daily observation from server
       │
       ▼
[1] Parse observation → WorldState  (agents/glittery/state.py)
[2] Load memory from save_notes      (agents/glittery/memory.py)
[3] Compute 21 signal detectors      (agents/glittery/signals.py)
[4] Decide mode                      (agents/glittery/policies.py)
       │   NORMAL / DEFENSIVE / END_GAME / SURGE / RENOVATION / EMERGENCY
       ▼
[5] Build SimState (compact for simulator)
[6] Enumerate ~60 candidate action sets:
       staff ∈ {anchor-2 … anchor+2}
       price_mult ∈ {0.95, 1.0, 1.05, 1.10, 1.15, 1.20}
       marketing ∈ {0, 200, 500}
       happy_hour ∈ {yes, no} on slow weekdays
       │
       ▼
[7] For each candidate: simulate 3 days forward via digital twin
       day 0: candidate action
       days 1-2: DOW-anchored default action
       sum discounted projected score (γ = 0.92)
       │
       ▼
[8] Pick candidate with highest 3-day projected score
       │
       ▼
[9] Emit actions:
       - place_order × N (from deterministic supply planner)
       - set_staff_level
       - set_menu (full 8 dishes unless ingredient broken)
       - set_price × N (chosen multiplier × base_price)
       - set_marketing_spend (if > 0)
       - run_happy_hour (if chosen)
       - offer_daily_special (always — targeting expiring inventory)
       │
       ▼
[10] Safety re-validate (rules_safety.py)
[11] Persist memory to save_notes (compact JSON, ≤3,800 chars)
```

---

## The digital twin (`agents/mpc/simulator.py`)

A pure-Python function `predict_day(state, action) → result`. Given current state and chosen action, predicts:

- **Demand**: `DOW_baseline × weather_mult × rep_mult × trend_mult × price_elasticity(mult) × marketing_lift × happy_hour_lift × variety × scenario_factor`
- **Capacity**: `min(table_capacity, kitchen_throughput, inventory_cap)` — renovation halves table cap automatically when alert keyword fires
- **Served**: `min(demand, capacity)`
- **Walkouts**: `max(0, demand − served)`
- **Revenue**: `served × avg_price × price_mult × (1 − happy_hour_discount)`
- **Costs**: 300 fixed + staff × 120 + ingredient_cost × served + marketing
- **Reputation transition**: walkout ratio drives band changes (asymmetric: bad weighs more than good)
- **Score**: `profit − walkout_penalty − rep_penalty − sat_penalty`

Every constant in the simulator was **calibrated empirically from 12 recon games**:
- `AVG_BASE_PRICE_PER_COVER = 18.30` ← measured directly
- `KITCHEN_THROUGHPUT_PER_STAFF = 11.0` ← from staff=6 serving ~75 covers
- `TABLE_CAPACITY_NORMAL = 115`, `RENOVATION = 60`
- `WALKOUT_PENALTY_PER_WALKOUT = 3.0` ← back-fit from observed walkout penalties

---

## Files

### MPC package (`agents/mpc/`)

| File | Purpose |
|---|---|
| `mpc_agent.py` | Turn orchestrator: parse → signals → enumerate → simulate → pick → emit |
| `simulator.py` | Digital twin: `predict_day()`, `simulate_horizon()` |
| `candidates.py` | Action candidate enumerator (DOW-anchored, signal-aware) |
| `play_verbose.py` | Per-day verbose log — top 3 MPC candidates, twin prediction vs actual |
| `analyze.py` | Compact one-line-per-day diagnostic |
| `README.md` | Architecture writeup |

### Internal utility library (`agents/glittery/`)

NOT an agent — shared utilities the MPC imports.

| File | Purpose |
|---|---|
| `state.py` | `parse(observation, day) → WorldState` |
| `memory.py` | Compact JSON in `save_notes` (≤3,800 chars) |
| `signals.py` | 21 signal detectors (pure functions of state + memory) |
| `policies.py` | Mode decision (NORMAL/DEFENSIVE/SURGE/etc.) |
| `constants.py` | Supplier topology, recipes, DOW priors — hand-curated from recon |
| `rules_supply.py` | 8-step deterministic supply planner (MPC delegates ordering to this) |
| `rules_safety.py` | Final action validator (drops/fixes anything unsafe) |
| `rules_staff.py`, `rules_menu.py` | Fallback defaults (rarely used) |

### Infrastructure (`agents/`)

| File | Purpose |
|---|---|
| `runner.py` | HTTP game loop — calls `strategy(observation, day)` 30 times per game |
| `evaluate.py` | Multi-scenario × multi-seed parallel evaluator |
| `mpc_agent.py` (top-level) | Thin entry for `python -m agents.mpc_agent` |

### Diagnostics

| File | Purpose |
|---|---|
| `agents/recon.py` | Data harvester — runs minimal-survival games and dumps every observation |
| `agents/recon_analyze.py` | Offline deep-dive analysis of harvested data |
| `agents/intel.py` | Leaderboard intelligence — clusters top teams by scenario |

---

## What we tried and rejected

These approaches were tested empirically and **discarded** because they hurt scores. Documented here so we don't repeat them.

### LLM-based agent
- **Tried**: GPT-4.1-mini as advisor, free to modify staff / pricing / promo / menu
- **Result**: -10,930 avg (vs -8,002 deterministic), 1 bankruptcy
- **Why it failed**: Action space is small and discrete — LLM judgment doesn't add value where exhaustive search works. LLM also over-deviated from safe defaults.
- **Outcome**: LLM client kept in repo for Stage-2 pitch demo (`AGENT_ENABLE_LLM=1`), but **default off**.

### Genetic algorithm policy evolution
- **Tried**: 15-dim parametric policy (DOW staffing, mode price multipliers, buffers, marketing dial), GA evolved against offline simulator
- **Result**: GA converged on "max prices, zero marketing, low staff." Tested on real server → -2,990 avg (worse than MPC)
- **Why it failed**: Offline simulator's price-elasticity model is too soft. GA exploited the simulator's bias, but real demand crashes harder at 1.20× prices.
- **Lesson**: Model-based optimization needs a calibrated model. Ours isn't accurate enough to optimize over — only good enough to *rank* short-horizon decisions (which is what MPC does).

### Multi-agent expert system (with propose-critic-judge loops)
- **Considered**: 5 experts × propose/critic/judge × 2 rounds = 30 LLM calls per turn
- **Why rejected before building**: 27,000 LLM calls per full eval = $27. Plus echo-chamber failure modes in critic loops. Plus our LLM tests already showed LLM hurts on this game.

---

## Key game mechanics (encoded in the agent)

- **Day 1 = Monday.** Weekends fall on days 6, 7, 13, 14, 20, 21, 27, 28.
- **Service 11:00-22:00.** Lunch peak ~12:00, dinner peak 17-19.
- **Penalties**: walkout = linear (~3 EUR each), reputation/satisfaction = quadratic (catastrophic if you fall below threshold).
- **Reputation has asymmetric momentum**: bad weighs more than good. Final reputation > average reputation.
- **Final reputation matters more than average** → end-game (day ≥ 25) increases price multiplier to 1.08 instead of cutting quality.
- **Supplier fill rates**: 73-84% even at baseline → must over-order ~1.40× to compensate (1.70× in early game).
- **Italian Imports = Wed-only delivery + 3-day lead** → orders must be placed Sun/Mon/Tue for next Wednesday. Hardcoded in supply planner.
- **Walkouts generate ghost negative reviews** → reputation drop compounds → demand from regulars dries up → death spiral. Avoid at all costs.

---

## Scenarios

### Known (used in dev)
- `baseline` — no events
- `supply_crisis` — day 1 alert "Industry analysts warn of potential disruptions in Mediterranean shipping lanes"; supplier fill rates drop over time
- `tourist_season` — massive demand surge days 1-3, then collapse
- `renovation` — half tables unavailable days 1-14; +2,000 starting cash bonus

### Hidden (6 scenarios revealed only at evaluation phase ~16:00)
Likely include some mix of: inflation, health_scare, staff_shortage, ingredient_ban, demand_crash, festival_extension. Our agent handles them *generically* via:
- **Keyword-based alert parsing** — substrings `disruption | shortage | renovation | scare | outbreak | festival | inflation | ban` each trigger behavioral responses, not labels.
- **No scenario-name conditionals anywhere** — every behavior is driven by observable signals.
- **Simulator reads current state** — if a hidden scenario causes covers to drop or capacity to shrink, the twin's predictions adjust automatically.

---

## Reconnaissance data (`recon_data/`)

All simulator constants were calibrated from 12 games of reconnaissance (4 scenarios × 3 seeds) played early with a minimal-survival agent.

| Path | Contents |
|---|---|
| `recon_data/SUMMARY.md` | Human-readable summary of suppliers, recipes, alerts, reliability, weather |
| `recon_data/summary/suppliers.json` | Topology: 5 suppliers, delivery days, lead times, prices |
| `recon_data/summary/recipes.json` | All 8 dishes with base prices and ingredient quantities |
| `recon_data/summary/alerts_by_scenario.json` | Alert text observed per scenario |
| `recon_data/summary/supplier_reliability.json` | Empirical fill rates |
| `recon_data/games/<scenario>_seed<N>/` | Raw observation dumps (gitignored — large) |

Re-harvest anytime via `python -m agents.recon`. Re-analyze via `python -m agents.recon_analyze`.

---

## Pitch one-liner

> "We didn't build an agent that talks. We built a *digital twin* of the restaurant — a tiny Python simulator calibrated from 360 observed game-days — and run Model-Predictive Control over it. Every day, we simulate 60 possible actions 3 days into the future and pick whichever scores highest. It's the same technology that lands SpaceX rockets and drives Waymo cars."

---

## Submission checklist (per cheat sheet)

- [x] Pick unique team name and use consistently
- [ ] Run agent under that team name before 17:00
- [x] Public GitHub repo
- [ ] Last commit before 17:00
- [ ] Submission form filled in
- [x] 0 bankruptcies in dev / held-out evals (survival is the #1 invariant)
- [x] Agent generalizes — held-out seeds score *better* than dev seeds, confirming we're not overfit
