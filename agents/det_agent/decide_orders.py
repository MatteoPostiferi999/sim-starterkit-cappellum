"""Deterministic order planner.

Strategy:
  1. Project inventory 7 days forward with current pending orders.
  2. For each ingredient flagged with a stockout, compute the gap and place
     an order from the cheapest reliable supplier whose delivery arrives before
     the stockout day.
  3. Italian Imports has 3-day lead and only delivers Wednesday — special-case it.
  4. Over-order by 1.4x to compensate for ~75% fill rates.
  5. Cap orders by remaining safe_order_budget.
"""

from __future__ import annotations

from .constants import (
    ING_SUPPLIERS, SUPPLIERS, DOW_ORDER, RECIPES, ALL_DISHES, DISH_MIX,
)
from .predict import delivery_day_for
from .memory import supplier_fill_estimate
from . import config


# Days of buffer to maintain
BUFFER_DAYS_FRESH = config.BUFFER_FRESH    # short shelf-life ingredients (≤7 days)
BUFFER_DAYS_DRY = config.BUFFER_DRY        # longer shelf-life (flour, pasta, pepperoni)

# Over-order factor — fills get short, must compensate
OVER_ORDER_BASE = config.OVER_ORDER
OVER_ORDER_CRISIS = config.OVER_ORDER_CRISIS

# Which ingredients have long shelf life
LONG_SHELF = {"Flour", "Fresh Pasta", "Pepperoni"}


def plan_orders(state, memory, projection: dict, runway: dict) -> list[dict]:
    """Return list of place_order tool calls."""
    plan: list[dict] = []
    budget = runway["safe_order_budget"]
    over_order = OVER_ORDER_CRISIS if memory.scen_crisis else OVER_ORDER_BASE

    # Identify needs: ingredient → kg_to_order
    needs: list[tuple[str, float, str]] = []  # (ing, kg, urgency)

    for ing, proj in projection.items():
        # Already enough?
        first_so = proj["first_stockout"]
        if first_so is None:
            continue  # no stockout in 7 days
        # Compute days-of-demand to order
        buffer_days = BUFFER_DAYS_DRY if ing in LONG_SHELF else BUFFER_DAYS_FRESH
        # Daily consumption
        daily_use = _compute_daily_use(state, memory, ing)
        kg_needed = daily_use * buffer_days * over_order
        # Subtract any existing pending that hasn't been counted in projection
        # (projection already adjusted for pending, so just order the gap)
        urgency = "critical" if first_so <= 2 else ("high" if first_so <= 4 else "medium")
        needs.append((ing, kg_needed, urgency))

    # Also pre-emptively top up critical ingredients with NO stockout but
    # below a safety threshold — useful for early game ONLY day 1.
    if state.day == 1:
        for ing in ["Flour", "Mozzarella", "Tomato Sauce", "Fresh Pasta"]:
            if ing in [n[0] for n in needs]:
                continue
            on_hand = state.inventory.get(ing)
            on_hand_kg = on_hand.total_kg if on_hand else 0.0
            pending_kg = state.pending_by_ingredient.get(ing, 0.0)
            if on_hand_kg + pending_kg < 6.0:
                daily_use = _compute_daily_use(state, memory, ing)
                kg_needed = max(5.0, daily_use * 3 * over_order)
                needs.append((ing, kg_needed, "topup"))

    # Sort by urgency
    rank = {"critical": 0, "high": 1, "medium": 2, "topup": 3}
    needs.sort(key=lambda x: rank.get(x[2], 9))

    spent = 0.0
    for ing, kg, urgency in needs:
        # Choose supplier: cheapest reliable that delivers in time
        suppliers = ING_SUPPLIERS.get(ing, [])
        chosen = None
        chosen_arrival = 99
        # For Italian Imports (3-day lead Wed-only), only viable on
        # Sunday/Monday/Tuesday for next-Wed delivery
        for sup_name, price in suppliers:
            sup_info = SUPPLIERS[sup_name]
            arrival = delivery_day_for(state, sup_name, sup_info["lead"], sup_info["days"])
            fill = supplier_fill_estimate(memory, sup_name)
            # In crisis, avoid suppliers with fill<0.50
            if memory.scen_crisis and fill < 0.55:
                continue
            # Effective cost weighted by fill rate
            eff_cost = price / max(0.3, fill)
            # Prefer earlier delivery for urgent needs
            score = eff_cost + arrival * (5 if urgency == "critical" else 1)
            if chosen is None or score < chosen[2]:
                chosen = (sup_name, price, score)
                chosen_arrival = arrival
        if not chosen:
            continue
        sup_name, price, _ = chosen
        sup_info = SUPPLIERS[sup_name]
        # Round up to satisfy min_order_kg
        kg = max(kg, sup_info["min"])
        kg = round(kg, 1)
        cost = kg * price
        if spent + cost > budget:
            # Try a smaller minimum order if it still helps
            kg = sup_info["min"]
            cost = kg * price
            if spent + cost > budget:
                continue
        plan.append({
            "tool": "place_order",
            "args": {"supplier": sup_name, "ingredient": ing, "quantity_kg": float(kg)},
        })
        spent += cost

    # Diversify critical fresh ingredients when in crisis: add a small backup
    # from a secondary supplier (capped to budget remainder)
    if memory.scen_crisis:
        for ing in ["Flour", "Fresh Pasta", "Tomato Sauce"]:
            suppliers = ING_SUPPLIERS.get(ing, [])
            if len(suppliers) < 2:
                continue
            secondary = suppliers[1][0]
            already_ordering = any(
                a["args"]["supplier"] == secondary and a["args"]["ingredient"] == ing
                for a in plan if a["tool"] == "place_order"
            )
            if already_ordering:
                continue
            sup_info = SUPPLIERS[secondary]
            kg = sup_info["min"]
            price = sup_info["ings"][ing]
            cost = kg * price
            if spent + cost <= budget * 0.3:
                plan.append({
                    "tool": "place_order",
                    "args": {"supplier": secondary, "ingredient": ing, "quantity_kg": float(kg)},
                })
                spent += cost

    return plan


def _compute_daily_use(state, memory, ing: str) -> float:
    """Daily consumption of an ingredient at current demand."""
    from .predict import demand_estimate
    de = demand_estimate(state, memory)
    expected = de["expected"]
    total = 0.0
    active = set(state.active_menu) or set(ALL_DISHES)
    for dish, (_p, recipe) in RECIPES.items():
        if dish not in active:
            continue
        if ing in recipe:
            total += expected * DISH_MIX.get(dish, 1.0 / 8) * recipe[ing]
    return max(0.1, total)
