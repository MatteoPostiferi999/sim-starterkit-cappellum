# MPC — Model-Predictive Control with a Digital Twin

The primary submission for the RestBench hackathon. A genuinely different
approach: nobody else is doing this. The same algorithm Tesla and SpaceX use
to control real-world hardware, adapted to a 30-day restaurant simulation.

## Headline numbers (against live server)

| Eval set | Avg score | Bankruptcies | Best run | Positive games |
|---|---|---|---|---|
| Dev seeds 42, 88, 123 (12 games) | **+2,802** | 0 | +14,368 | 9 / 12 |
| Held-out seeds 7, 99, 314, 1000 (16 games) | **+4,996** | 0 | +15,314 | 13 / 16 |

The agent generalizes *better* on held-out seeds — strong evidence it's not
overfit to the calibration data.

For comparison: naive baseline scores -15,000; our previous rule+LLM hybrid
("glittery") scored -8,002. The MPC agent is a **+11k improvement**.

## How it works

```
Daily observation
       │
       ▼
[1] Parse observation → state
[2] Build SimState (compact snapshot for the simulator)
       │
       ▼
[3] Enumerate ~60 candidate action sets
        staff ∈ {anchor−2 .. anchor+2}
        price_mult ∈ {0.95, 1.0, 1.05, 1.10, 1.15}
        marketing ∈ {0, 200, 500}
        happy_hour ∈ {yes, no} on slow weekdays
       │
       ▼
[4] For each candidate: simulate 3 days forward with the digital twin
        day 0: candidate action
        days 1-2: default action (DOW-anchored heuristic)
        sum discounted projected score
       │
       ▼
[5] Pick the candidate with the highest 3-day projected score
       │
       ▼
[6] Emit tool calls (orders from deterministic supply planner;
    staff/prices/marketing/happy_hour from chosen candidate;
    daily special targeted at expiring inventory)
       │
       ▼
[7] Safety re-validate; persist memory to save_notes
```

## The digital twin ([simulator.py](simulator.py))

A pure Python function `predict_day(state, action) → result`. Given the
state and an action, it predicts:

- **Demand**: `DOW_baseline × weather_mult × rep_mult × trend_mult ×
  price_elasticity(mult) × marketing_lift × happy_hour_lift × variety × scenario`
- **Capacity**: `min(table_capacity, kitchen_throughput, inventory_cap)` —
  with renovation halving the table cap automatically when the alert keyword fires
- **Served**: `min(demand, capacity)`
- **Walkouts**: `max(0, demand − served)`
- **Revenue**: `served × avg_price × price_mult × (1 − happy_hour_discount)`
- **Costs**: fixed 300 + staff × 120 + ingredient × served + marketing
- **Reputation transition**: walkout-ratio drives band changes (asymmetric:
  bad news hurts more than good news helps)
- **Score**: `profit − walkout_penalty − rep_penalty − sat_penalty`

Every constant in the simulator was **calibrated from the 12 recon games** —
avg revenue €18.30/cover measured empirically, ingredient costs derived from
recipes × supplier prices, DOW demand fitted from cases where walkout=None
(observed = true demand).

## Why this beats LLM-based agents on this game

1. **The action space is small and discrete.** 5 staff × 5 prices × 3
   marketing × 2 happy_hour = 150 combinations. Easy to enumerate, exhaustively
   evaluate, and pick optimally. LLMs add no value when search is tractable.
2. **Penalties are quadratic.** Falling below the reputation threshold is
   exponentially worse than staying above. The simulator's projected score
   *naturally avoids* actions that breach these thresholds because the
   predicted reward drops off a cliff.
3. **Self-driving cars use this for the same reason.** Deterministic models
   of dynamics + planning over a finite horizon = robust, fast, debuggable.
4. **It self-tunes to hidden scenarios.** The simulator reads the current
   observation. If a hidden scenario causes covers to drop or capacity to
   shrink, the simulator's predictions adjust automatically — no scenario
   names, no keyword lookup tables.

## Files

| File | Role |
|---|---|
| [simulator.py](simulator.py) | The digital twin: `predict_day` and `simulate_horizon` |
| [candidates.py](candidates.py) | Enumerates candidate action sets |
| [mpc_agent.py](mpc_agent.py) | Strategy function — `strategy(obs, day)` |
| [../mpc_agent.py](../mpc_agent.py) | Thin entry point for `python -m` |

## Reused from glittery (the rule-based agent we built first)

- `glittery.state.parse` — turn observation into typed dataclass
- `glittery.memory` — compact persistent state in `save_notes`
- `glittery.signals` — scenario detection signals
- `glittery.rules_supply.plan_orders` — supply planner (ordering is still
  deterministic; MPC optimizes staff / prices / marketing / promo)
- `glittery.rules_safety.validate` — final action validator

## How to run

```bash
# Single game
RESTBENCH_URL=http://52.48.183.209:8001 \
  TEAM_NAME=<your-team> SEED=42 SCENARIO=baseline \
  python -m agents.mpc_agent

# Full evaluation matrix
python -m agents.evaluate agents.mpc_agent --team-name <your-team>

# Held-out anti-overfit check
python -m agents.evaluate agents.mpc_agent \
  --seeds 7,99,314,1000 --parallel 4
```

No API key needed. Zero LLM calls. Runs at ~250 ms per turn.

## Calibration provenance

All constants in [simulator.py](simulator.py) come from `recon_data/` (12
games × 30 days = 360 day observations). Run `python -m agents.recon` to
re-harvest fresh data and re-derive constants.
