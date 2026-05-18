"""Deterministic predictors. Pure functions of state + memory.

These are the agent's "model of the world". They give every other module
concrete numbers to work with — no LLM imagination required.
"""

from __future__ import annotations

from .constants import (
    DOW_COVERS_PRIOR, DOW_ORDER, WEATHER_MULT, TREND_MULT, REP_DEMAND_MULT,
    KITCHEN_THROUGHPUT_PER_STAFF, TABLE_CAPACITY_NORMAL, TABLE_CAPACITY_RENOVATION,
    RECIPES, ALL_DISHES, DISH_MIX, ING_SUPPLIERS, SUPPLIERS, OVERHEAD_PER_DAY,
    STAFF_COST_DAY, CASH_RESERVE,
)
from .memory import dow_avg, supplier_fill_estimate


def demand_estimate(state, memory, weather: str | None = None, dow: str | None = None) -> dict:
    """Predict covers for today (or override with future weather/dow).

    Returns: {expected: int, low: int, high: int, confidence: str}
    """
    use_dow = dow or state.day_of_week
    use_weather = weather or state.weather_today

    observed_avg = dow_avg(memory, use_dow)
    if observed_avg is not None:
        base = observed_avg
        confidence = "high" if len(memory.dow_covers.get(use_dow, [])) >= 2 else "medium"
    else:
        base = DOW_COVERS_PRIOR.get(use_dow, 60.0)
        confidence = "low"

    w_mult = WEATHER_MULT.get(use_weather, 1.0)
    t_mult = TREND_MULT.get(state.customer_trend, 1.0)
    r_mult = REP_DEMAND_MULT.get(state.reputation_band, 1.0)

    # Scenario adjustments
    s_mult = 1.0
    if memory.scen_tourist and state.day <= 4:
        s_mult *= 1.7   # surge days
    elif memory.scen_tourist and 5 <= state.day <= 10:
        s_mult *= 0.75  # post-surge drop
    if memory.scen_health:
        s_mult *= 0.80
    if memory.scen_ban:
        s_mult *= 0.85
    # If renovation: post-renov satisfaction bonus boosts demand
    if memory.scen_renovation and state.day > memory.renov_start + 12:
        s_mult *= 1.10

    expected = base * w_mult * t_mult * r_mult * s_mult
    return {
        "expected": int(round(expected)),
        "low":  int(round(expected * 0.85)),
        "high": int(round(expected * 1.15)),
        "confidence": confidence,
    }


def capacity_estimate(state, staff_level: int) -> int:
    """Combined capacity given staff + table cap + scenarios."""
    table_cap = TABLE_CAPACITY_NORMAL
    if state.day <= 14:
        # Check for active renovation
        alerts_txt = " ".join(state.alerts).lower()
        if "renovation" in alerts_txt or "tables" in alerts_txt:
            table_cap = TABLE_CAPACITY_RENOVATION
    kitchen_cap = staff_level * KITCHEN_THROUGHPUT_PER_STAFF
    return int(min(table_cap, kitchen_cap))


def staff_needed_for(demand_covers: int, state) -> int:
    """Reverse capacity formula. Returns staff to serve `demand_covers` (capped)."""
    # Reserve some headroom — staff at exactly cap → walkouts at peaks
    needed = (demand_covers * 1.05) / KITCHEN_THROUGHPUT_PER_STAFF
    return max(3, min(15, int(needed + 0.5)))


def inventory_future(state, memory, n_days: int = 7) -> dict:
    """Project inventory forward, accounting for incoming orders and spoilage.

    Returns:
      {
        ingredient: {
            "daily_kg": [day1, day2, ...],
            "first_stockout": int | None,
            "expiry_waste": float,   # kg expected to spoil unused
        }
      }
    """
    out: dict[str, dict] = {}

    # Build delivery schedule: ingredient → {day_offset: kg_arriving}
    inbound: dict[str, dict[int, float]] = {}
    for po in state.pending_orders:
        ing = po.get("ingredient")
        if not ing:
            continue
        delivery_day = int(po.get("delivery_day", 0))
        kg = float(po.get("quantity_kg", 0))
        offset = delivery_day - state.day  # day offset from today
        if offset < 0:
            continue
        # Discount by expected fill rate
        sup = po.get("supplier", "")
        fr = supplier_fill_estimate(memory, sup) if sup else 0.75
        kg_eff = kg * fr
        inbound.setdefault(ing, {})[offset] = inbound.get(ing, {}).get(offset, 0) + kg_eff

    # Demand per day forecast (for scaling consumption)
    forecast = []
    forecasts = state.weather_forecast or [state.weather_today]
    for d_off in range(n_days):
        target_dow_idx = (DOW_ORDER.index(state.day_of_week) + d_off) % 7
        target_dow = DOW_ORDER[target_dow_idx]
        wx = forecasts[min(d_off, len(forecasts) - 1)] if forecasts else state.weather_today
        de = demand_estimate(state, memory, weather=wx, dow=target_dow)
        forecast.append(de["expected"])

    # Per-ingredient projection
    for ing, item in state.inventory.items():
        # Track batches as list of (remaining_kg, days_until_expiry)
        batches = [[float(b["quantity_kg"]), int(b.get("expires_in_days", 99))]
                   for b in item.batches]
        # Recipes that use this ingredient
        relevant = [(d, kg) for d, (_p, recipe) in RECIPES.items()
                    for ing_name, kg in recipe.items() if ing_name == ing]
        # Daily consumption rate (kg/day)
        def daily_use(covers: int) -> float:
            total = 0.0
            active = set(state.active_menu) or set(ALL_DISHES)
            for dish, kg_per_cover in relevant:
                if dish not in active:
                    continue
                total += covers * DISH_MIX.get(dish, 1.0 / 8) * kg_per_cover
            return total

        daily_kg = []
        first_stockout = None
        waste = 0.0
        for d_off in range(n_days):
            # 1. Apply incoming orders for this day
            if d_off in inbound.get(ing, {}):
                arriving_kg = inbound[ing][d_off]
                batches.append([arriving_kg, item.shelf_life_days])
            # 2. Age all batches; remove spoiled
            new_batches = []
            for b_kg, b_exp in batches:
                if b_exp <= 0:
                    waste += b_kg
                else:
                    new_batches.append([b_kg, b_exp])
            batches = new_batches
            # 3. Consume FIFO
            use_today = daily_use(forecast[d_off])
            remaining = use_today
            new_batches = []
            for b in batches:
                if remaining <= 0:
                    new_batches.append(b)
                    continue
                take = min(b[0], remaining)
                b[0] -= take
                remaining -= take
                if b[0] > 0.001:
                    new_batches.append(b)
            batches = new_batches
            total_left = sum(b[0] for b in batches)
            daily_kg.append(round(total_left, 2))
            if total_left < 0.001 and first_stockout is None:
                # We couldn't fully serve today
                if remaining > 0.001:
                    first_stockout = d_off
            # 4. Age batches by 1 day
            batches = [[b[0], b[1] - 1] for b in batches]

        out[ing] = {
            "daily_kg": daily_kg,
            "first_stockout": first_stockout,
            "expiry_waste": round(waste, 2),
        }
    return out


def delivery_day_for(state, supplier: str, lead: int, delivery_days: list[str]) -> int:
    """Earliest delivery day for an order placed today."""
    # day_of_week index for today
    today_idx = DOW_ORDER.index(state.day_of_week)
    earliest = state.day + lead
    earliest_idx = (today_idx + lead) % 7
    # Walk forward at most 7 days to find a delivery day
    for i in range(7):
        target_idx = (earliest_idx + i) % 7
        target_day = earliest + i
        if DOW_ORDER[target_idx] in delivery_days:
            return target_day
    return earliest + 7  # fallback


def cash_runway(state, expected_daily_revenue: float) -> dict:
    """Cash projection.

    Returns: {daily_burn, safe_order_budget, days_to_bankruptcy}
    """
    daily_burn = OVERHEAD_PER_DAY + state.staff_level * STAFF_COST_DAY
    net_daily = expected_daily_revenue - daily_burn
    # Sum committed pending orders that haven't been deducted yet — actually pending
    # orders are charged on delivery, so they DO need a budget reserve.
    pending_cost = 0.0
    for po in state.pending_orders:
        # Best-effort: per-kg price lookup
        ing = po.get("ingredient", "")
        sup = po.get("supplier", "")
        kg = float(po.get("quantity_kg", 0))
        catalog_sup = SUPPLIERS.get(sup, {})
        price = catalog_sup.get("ings", {}).get(ing, 5.0)
        pending_cost += kg * price
    # Days to bankruptcy if revenue stays at projected
    if net_daily >= 0:
        days_to_bk = None
    else:
        days_to_bk = max(1, int(state.cash / abs(net_daily)))

    safe_budget = max(0.0, state.cash - CASH_RESERVE - daily_burn * 3 - pending_cost * 0.5)
    return {
        "daily_burn": daily_burn,
        "safe_order_budget": safe_budget,
        "days_to_bankruptcy": days_to_bk,
        "pending_cost": pending_cost,
    }
