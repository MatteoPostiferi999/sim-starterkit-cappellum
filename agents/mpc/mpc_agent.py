"""MPC strategy: every turn, simulate dozens of action candidates 3 days
forward, pick the highest-scoring one, submit it.

Reuses glittery's state parsing, memory, supply planner, and safety validator —
swaps the staff/price/marketing/happy-hour decision for simulator-driven search.
"""

from __future__ import annotations

from ..glittery import state as state_mod
from ..glittery import memory as memory_mod
from ..glittery import signals as signals_mod
from ..glittery import rules_supply
from ..glittery import rules_safety
from ..glittery import policies
from ..glittery.constants import (
    ALL_DISHES, RECIPES, MENU_MIN_DISHES, PRICE_MULT_MIN, PRICE_MULT_MAX,
)

from .simulator import SimState, SimAction, predict_day, simulate_horizon
from .candidates import enumerate_candidates, default_future_action


HORIZON_DAYS = 3            # default MPC lookahead
HORIZON_DAYS_LONG = 5       # used in renovation / crisis where carryover dominates


def _build_sim_state(state, memory) -> SimState:
    """Translate glittery WorldState into the simulator's compact SimState."""
    inventory_kg = sum(item.total_kg for item in state.inventory.values())
    scenario_flags = dict(memory.scen_flags) if hasattr(memory, "scen_flags") else {}
    # Also inject renovation/crisis from current alerts (memory may not have caught up day 1)
    alerts_text = " ".join(state.alerts).lower()
    if "renovation" in alerts_text:
        scenario_flags["renovation"] = 1
    if "disruption" in alerts_text or "shipping" in alerts_text or "shortage" in alerts_text:
        scenario_flags["crisis"] = 1
    if "tourist" in alerts_text or "festival" in alerts_text or "surge" in alerts_text:
        scenario_flags["tourist_surge"] = 1
    return SimState(
        day=state.day,
        day_of_week=state.day_of_week,
        weather_today=state.weather_today,
        weather_forecast=list(state.weather_forecast) or [state.weather_today] * 3,
        cash=state.cash,
        reputation_rank=state.reputation_rank,
        customer_trend=state.customer_trend,
        staff_level=state.staff_level,
        inventory_kg_estimate=inventory_kg,
        consecutive_hh_days=max(0, 7 - memory.since_hh) if hasattr(memory, "since_hh") else 0,
        days_since_special=memory.since_special if hasattr(memory, "since_special") else 99,
        scenario_flags=scenario_flags,
        active_dish_count=len(state.active_menu) if state.active_menu else 8,
        is_capacity_bound=state.table_utilization_peak > 0.9,
    )


def _pick_best_action(sim_state: SimState, candidates: list[SimAction]) -> tuple[SimAction, float]:
    """Score every candidate via simulate_horizon, return the best one."""
    # Longer horizon when scenario carryover dominates (renovation, crisis)
    horizon = HORIZON_DAYS
    if sim_state.scenario_flags.get("renovation") or sim_state.scenario_flags.get("crisis"):
        horizon = HORIZON_DAYS_LONG

    best = None
    best_score = float("-inf")
    for cand in candidates:
        score = simulate_horizon(sim_state, cand, default_future_action, horizon=horizon)
        if score > best_score:
            best_score = score
            best = cand
    return best, best_score


def _emit_actions(state, sig, memory, best: SimAction, mode: str) -> list[dict]:
    """Translate the chosen SimAction + deterministic supply/menu into tool calls."""
    plan: list[dict] = []

    # ── Supply orders (delegate to existing planner) ──
    plan += rules_supply.plan_orders(state, sig, memory, mode)

    # ── Staff ──
    plan.append({"tool": "set_staff_level", "args": {"level": int(best.staff)}})

    # ── Menu: keep all available dishes (drop only when ingredient missing) ──
    menu = [d for d in ALL_DISHES if d in state.menu_book]
    # Pepperoni gate
    pep = state.inventory.get("Pepperoni")
    pep_pending = state.pending_by_ingredient.get("Pepperoni", 0)
    if (pep is None or pep.total_kg < 0.5) and pep_pending < 0.5:
        menu = [d for d in menu if d != "Pizza Pepperoni"]
    if len(menu) < MENU_MIN_DISHES:
        menu = [d for d in ALL_DISHES if d in state.menu_book][:max(MENU_MIN_DISHES, 5)]
    plan.append({"tool": "set_menu", "args": {"dishes": menu}})

    # ── Pricing: apply chosen multiplier to every active dish ──
    for dish in menu:
        entry = state.menu_book.get(dish)
        if not entry:
            continue
        base = float(entry.get("base_price", 0))
        if base <= 0:
            continue
        mult = max(PRICE_MULT_MIN, min(PRICE_MULT_MAX, best.price_mult))
        plan.append({"tool": "set_price", "args": {"dish": dish, "price": round(base * mult, 2)}})

    # ── Marketing ──
    if best.marketing > 0:
        plan.append({"tool": "set_marketing_spend", "args": {"amount": int(best.marketing)}})

    # ── Happy hour ──
    if best.happy_hour:
        plan.append({"tool": "run_happy_hour", "args": {}})

    # ── Daily special: pick the dish whose ingredient is most at risk of expiring ──
    special = _pick_daily_special(state, menu)
    if special:
        plan.append({"tool": "offer_daily_special", "args": {"dish": special}})

    return plan


def _pick_daily_special(state, active_menu: list[str]) -> str | None:
    """Pick a daily special: prefer dishes using overstocked / expiring ingredients."""
    if not active_menu:
        return None
    # Find ingredient with the most kg expiring within 2 days
    most_at_risk: tuple[str, float] | None = None
    for ing, item in state.inventory.items():
        soon = item.expiring_soon(2)
        if soon > 0 and (most_at_risk is None or soon > most_at_risk[1]):
            most_at_risk = (ing, soon)
    if most_at_risk:
        for dish in active_menu:
            _, ingredients = RECIPES.get(dish, (0, {}))
            if most_at_risk[0] in ingredients:
                return dish
    # Fallback: highest-margin dish
    by_margin = sorted(active_menu, key=lambda d: -RECIPES[d][0] if d in RECIPES else 0)
    return by_margin[0] if by_margin else None


def strategy(observation: dict, day: int) -> list[dict]:
    # Parse
    state = state_mod.parse(observation, day)
    memory = memory_mod.load(state.notes_raw)
    sig = signals_mod.compute(state, memory)
    memory = memory_mod.update(memory, state, sig)

    mode = policies.decide_mode(state, sig, memory)

    # EMERGENCY: minimal-spend fallback (no MPC, just survive)
    if mode == "EMERGENCY":
        plan = []
        plan.append({"tool": "set_staff_level", "args": {"level": 3}})
        plan += rules_supply.plan_orders(state, sig, memory, mode)
        plan = rules_safety.validate(plan, state, sig)
        plan.append({"tool": "save_notes", "args": {"text": memory_mod.dump(memory)}})
        return plan

    # MPC search
    sim_state = _build_sim_state(state, memory)
    candidates = enumerate_candidates(state, sig)

    if not candidates:
        # Should never happen but be safe
        from ..glittery import rules_staff, rules_menu
        plan = []
        plan += rules_supply.plan_orders(state, sig, memory, mode)
        plan.append({"tool": "set_staff_level", "args": {"level": rules_staff.plan_staff(state, sig, memory, mode)}})
        plan += rules_menu.plan_promo(state, sig, memory, mode)
        plan = rules_safety.validate(plan, state, sig)
        plan.append({"tool": "save_notes", "args": {"text": memory_mod.dump(memory)}})
        return plan

    best, best_score = _pick_best_action(sim_state, candidates)

    # Emit
    plan = _emit_actions(state, sig, memory, best, mode)

    # Track happy hour / marketing usage in memory
    if best.happy_hour:
        memory.since_hh = 0
    else:
        memory.since_hh = min(99, memory.since_hh + 1)
    if best.marketing > 0:
        memory.since_marketing = 0
    else:
        memory.since_marketing = min(99, memory.since_marketing + 1)

    # Safety re-validation
    plan = rules_safety.validate(plan, state, sig)

    # Persist memory
    plan.append({"tool": "save_notes", "args": {"text": memory_mod.dump(memory)}})

    return plan
