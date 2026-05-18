"""Menu, pricing, and promo defaults.

Mode-driven price multipliers; per-dish overrides come from LLM (clamped to grid).
Promos: pulse-based — happy hour cooldown of 2 days, marketing weighted toward
Fri/Sat unless capacity-bound.
"""

from __future__ import annotations

from .constants import (
    ALL_DISHES, RECIPES, MENU_MIN_DISHES, PRICE_MULT_MIN, PRICE_MULT_MAX,
    MARKETING_MAX,
)


MODE_PRICE_MULT = {
    "NORMAL":     1.03,    # small premium — captures pricing gain w/o scaring customers
    "DEFENSIVE":  0.97,
    "END_GAME":   1.08,
    "SURGE":      1.12,    # demand inelastic during surge
    "RENOVATION": 1.08,    # constrained capacity → charge what you can
    "EMERGENCY":  0.92,
}


def plan_menu(state, sig, memory, mode: str) -> list[str]:
    """Keep all 8 dishes unless ingredient supply is broken."""
    keep = list(ALL_DISHES)

    # If Pepperoni supply is broken, drop Pizza Pepperoni
    if sig.supply_disruption.get("Pepperoni") or sig.pepperoni_stockout_risk:
        pep = state.inventory.get("Pepperoni")
        if not pep or pep.total_kg + state.pending_by_ingredient.get("Pepperoni", 0) < 1.0:
            keep = [d for d in keep if d != "Pizza Pepperoni"]

    # If Salmon depleted, drop Grilled Salmon (single-source so no alternative)
    if sig.supply_disruption.get("Salmon"):
        salm = state.inventory.get("Salmon")
        if not salm or salm.total_kg + state.pending_by_ingredient.get("Salmon", 0) < 1.0:
            keep = [d for d in keep if d != "Grilled Salmon"]

    # Filter to dishes that exist in current menu_book (server-side truth)
    keep = [d for d in keep if d in state.menu_book]

    # Hard minimum
    if len(keep) < MENU_MIN_DISHES:
        # Add back highest-margin dishes until we have 5
        all_present = [d for d in ALL_DISHES if d in state.menu_book]
        for d in sorted(all_present, key=lambda x: -RECIPES[x][0]):
            if d not in keep:
                keep.append(d)
            if len(keep) >= MENU_MIN_DISHES:
                break

    return keep


def plan_prices(state, sig, memory, mode: str) -> dict[str, float]:
    """Return {dish: absolute_price} for each active-menu dish."""
    mult = MODE_PRICE_MULT.get(mode, 1.0)

    # Inflation: push prices up proportionally to cost drift, capped at 1.20
    if sig.inflation_active:
        mult = min(PRICE_MULT_MAX, mult + 0.05)

    # Capacity-bound and not collapsing → raise prices (demand absorbed)
    if sig.capacity_bound and not sig.demand_collapse:
        mult = min(PRICE_MULT_MAX, mult + 0.02)

    out: dict[str, float] = {}
    for dish in state.active_menu:
        entry = state.menu_book.get(dish)
        if not entry:
            continue
        base = float(entry.get("base_price", 0.0))
        if base <= 0:
            continue
        # Clamp to API range
        m = max(PRICE_MULT_MIN, min(PRICE_MULT_MAX, mult))
        out[dish] = round(base * m, 2)
    return out


def plan_promo(state, sig, memory, mode: str) -> list[dict]:
    """Return a list of promo actions: happy_hour, daily_special, marketing_spend."""
    actions: list[dict] = []
    dow = state.day_of_week
    active = state.active_menu or []

    # ── Happy hour ──
    if mode in ("NORMAL", "END_GAME"):
        if dow in ("Tuesday", "Wednesday") and not sig.capacity_bound and memory.since_hh >= 2:
            actions.append({"tool": "run_happy_hour", "args": {}})
            memory.since_hh = 0
        else:
            memory.since_hh = min(99, memory.since_hh + 1)
    else:
        memory.since_hh = min(99, memory.since_hh + 1)

    if mode == "DEFENSIVE" and sig.demand_collapse and not sig.capacity_bound:
        if memory.since_hh >= 1:
            actions.append({"tool": "run_happy_hour", "args": {}})
            memory.since_hh = 0

    # ── Daily special ──
    special = None
    if sig.salmon_waste_risk and "Grilled Salmon" in active:
        special = "Grilled Salmon"
    elif sig.inventory_overstocked:
        # Pick the dish using an overstocked ingredient
        overstocked = set(sig.inventory_overstocked.keys())
        for d in active:
            ings = set(RECIPES.get(d, (0, {}))[1].keys())
            if ings & overstocked:
                special = d
                break
    if special is None and (mode == "END_GAME" or sig.reputation_decline or sig.recovery_hysteresis_active):
        # Cycle through high-margin dishes (rotates daily by state.day)
        candidates = sorted([d for d in active if d in RECIPES], key=lambda x: -RECIPES[x][0])
        if candidates:
            special = candidates[state.day % len(candidates)]
    if special is None and mode == "NORMAL" and state.day % 3 == 0:
        # Light rotation on normal days
        candidates = sorted([d for d in active if d in RECIPES], key=lambda x: -RECIPES[x][0])
        if candidates:
            special = candidates[state.day % len(candidates)]

    if special and special in active:
        actions.append({"tool": "offer_daily_special", "args": {"dish": special}})
        memory.since_special = 0
    else:
        memory.since_special = min(99, memory.since_special + 1)

    # ── Marketing spend ──
    mkt = 0
    if mode == "EMERGENCY":
        mkt = 0
    elif mode == "RENOVATION":
        mkt = 0
    elif mode == "DEFENSIVE":
        # Only spend marketing in defensive if demand is collapsing
        mkt = 200 if (sig.demand_collapse and not sig.capacity_bound) else 0
    elif mode == "SURGE":
        mkt = 0  # already at capacity
    elif mode == "END_GAME":
        mkt = 100
    else:  # NORMAL
        if sig.capacity_bound:
            mkt = 0
        elif dow == "Saturday":
            mkt = 200
        elif dow == "Friday":
            mkt = 150
        elif dow == "Thursday":
            mkt = 100
        else:
            mkt = 0

    mkt = max(0, min(MARKETING_MAX, int(mkt)))
    if mkt != 0 or memory.since_marketing > 5:
        actions.append({"tool": "set_marketing_spend", "args": {"amount": mkt}})
        memory.since_marketing = 0 if mkt > 0 else min(99, memory.since_marketing + 1)
    else:
        memory.since_marketing = min(99, memory.since_marketing + 1)

    return actions


def apply_llm_price_overrides(default_prices: dict[str, float], state,
                               overrides: dict[str, float]) -> dict[str, float]:
    """LLM may multiply individual dishes by {0.85..1.15}. Clamp absolute price to base × [0.8, 1.2]."""
    out = dict(default_prices)
    for dish, mult in overrides.items():
        if dish not in out:
            continue
        if not isinstance(mult, (int, float)):
            continue
        m = max(0.85, min(1.15, float(mult)))
        entry = state.menu_book.get(dish)
        if not entry:
            continue
        base = float(entry.get("base_price", 0.0))
        if base <= 0:
            continue
        # The default already applied a mode-level mult. We replace with absolute mult on base.
        new_price = round(base * m, 2)
        # Clamp to API valid range
        new_price = max(round(base * PRICE_MULT_MIN, 2), min(round(base * PRICE_MULT_MAX, 2), new_price))
        out[dish] = new_price
    return out
