"""Supply planner — the highest-leverage module.

8-step algorithm:
  1. Compute 3-day forward demand per ingredient (DOW × dish-mix × kg-per-cover).
  2. Apply over-fill buffer (1.30 normal, 1.50 emerging disruption, 1.05 end-game).
  3. Effective stock = on_hand + (pending × supplier_fill_rate_prior).
  4. Cap by spoilage (shelf_life × daily_demand).
  5. Round up to supplier min_order_kg.
  6. Pick supplier (cheapest, or alternative under disruption).
  7. Delivery-window check (today + lead).DOW in supplier.delivery_days.
  8. Budget cap (≤ cash − reserve − overhead).

Italian Imports special-case: Wed-only delivery + 3-day lead means orders must be
placed by Sunday for next Wednesday. We bundle Pepperoni/Flour/Pasta/Tomato Sauce
into a single Sunday/Monday/Tuesday order.
"""

from __future__ import annotations

from .constants import (
    SUPPLIERS, INGREDIENT_SUPPLIERS, RECIPES, DOW_ORDER, DOW_BASELINE_COVERS,
    DISH_MIX_PRIOR, OVERHEAD_PER_DAY, STAFF_COST_DAY, CASH_RESERVE,
    SUPPLIER_CODE,
)
from .memory import Memory, baseline_covers, supplier_fill_rate
from .state import WorldState


def _next_dow(dow: str, n: int) -> str:
    """Day-of-week n days after `dow`."""
    if dow not in DOW_ORDER:
        return "Monday"
    return DOW_ORDER[(DOW_ORDER.index(dow) + n) % 7]


def _will_deliver(today_dow: str, supplier: dict) -> tuple[bool, int]:
    """Return (would-deliver-soon, days-until-next-delivery)."""
    lead = supplier["lead"]
    days = supplier["days"]
    # Check next 7 days
    for d in range(7):
        future_dow = _next_dow(today_dow, lead + d)
        if future_dow in days:
            return True, lead + d
    return False, 99


def _forward_demand(state: WorldState, memory: Memory, days_ahead: int = 3) -> dict[str, float]:
    """Sum expected kg used over next N days across the active menu."""
    demand: dict[str, float] = {}

    # Per-day expected covers for next N days
    daily_covers = []
    for d in range(1, days_ahead + 1):
        future_dow = _next_dow(state.day_of_week, d)
        daily_covers.append(baseline_covers(memory, future_dow))

    # Normalize dish mix over active menu
    active = [d for d in state.active_menu if d in RECIPES]
    if not active:
        return demand
    total_prior = sum(DISH_MIX_PRIOR.get(d, 1.0 / len(active)) for d in active)
    mix = {d: DISH_MIX_PRIOR.get(d, 1.0 / len(active)) / total_prior for d in active}

    for dish in active:
        _, ings = RECIPES[dish]
        share = mix[dish]
        for total_covers in daily_covers:
            dish_covers = total_covers * share
            for ing, kg_per_cover in ings.items():
                demand[ing] = demand.get(ing, 0.0) + dish_covers * kg_per_cover

    return demand


def _per_supplier_fill_rate(memory: Memory, supplier_name: str) -> float:
    return max(0.4, supplier_fill_rate(memory, supplier_name))


def _round_up_to_min(qty: float, min_order: float) -> float:
    return max(qty, min_order)


def plan_orders(state: WorldState, sig, memory: Memory, mode: str) -> list[dict]:
    """Build a list of place_order actions for today.

    Defensive: never exceeds (cash - reserve - 2-day overhead).
    """
    orders: list[dict] = []

    # ── 0. Sanity: must have menu and suppliers populated ──
    if not state.active_menu or not state.supplier_catalog:
        return orders

    # In EMERGENCY mode, no orders.
    if mode == "EMERGENCY":
        return orders

    # End-game order suppression: no orders that won't deliver before game ends.
    # Day 30 is last service day, day 31 has no service. Don't order if today + lead > 29.
    days_left = max(0, 30 - state.day)

    # ── 1. Forward demand (7-day for Italian Imports, 3-day for others) ──
    # Shrink the lookahead window near end-game to avoid ordering for unsold days.
    horizon = min(3, days_left)
    if horizon <= 0:
        return orders
    fwd_demand = _forward_demand(state, memory, days_ahead=horizon)
    if not fwd_demand:
        return orders

    # Extended 7-day demand specifically for Italian Imports (Wed-only delivery)
    # Cap horizon at remaining days
    fwd_demand_7d = _forward_demand(state, memory, days_ahead=min(7, days_left))

    # ── 2. Over-fill buffer ──
    # Recon fill rate is 73-84%, so we must order ~1.35-1.45 to cover demand.
    # Early-game (days 1-5): forecast uses DOW priors that under-estimate true demand
    # (recon prior of Mon=35 vs actual ~90). Aggressive buffer prevents starvation.
    if mode == "EMERGENCY":
        buffer = 1.0   # don't burn cash
    elif sig.supply_disruption_emerging or sig.crisis_warning:
        buffer = 1.60
    elif sig.end_game_phase:
        buffer = 1.05  # taper to avoid waste
    elif mode == "RENOVATION" or sig.renovation_active:
        buffer = 1.20  # half capacity → less needed
    elif state.day <= 5:
        buffer = 1.70  # early game: priors under-estimate demand, over-order
    else:
        buffer = 1.40

    # ── 3. Effective stock = on_hand + (pending × per-supplier fill) ──
    # But pending is keyed by ingredient + supplier. We use per-ingredient pending
    # weighted by the supplier's fill rate.
    pending_effective: dict[str, float] = {}
    for ing, qty in state.pending_by_ingredient.items():
        # Find which supplier this pending came from (use any with matching ingredient)
        # Actually pending is per-supplier in state.pending_by_supplier — use that
        pending_effective[ing] = 0.0
    for sup_name, pos in state.pending_by_supplier.items():
        fr = _per_supplier_fill_rate(memory, sup_name)
        for po in pos:
            ing = po.get("ingredient")
            qty = float(po.get("quantity_kg", 0))
            if ing:
                pending_effective[ing] = pending_effective.get(ing, 0.0) + qty * fr

    # ── Build candidate orders per ingredient ──
    candidates: list[tuple[float, str, str, float, str]] = []  # (priority, ingredient, supplier, qty, reason)

    today_dow = state.day_of_week
    available_cash = state.cash - CASH_RESERVE - 2 * (OVERHEAD_PER_DAY + STAFF_COST_DAY * state.staff_level)

    # Italian Imports ingredients: use 7-day forecast since they deliver Wed only.
    ITALIAN_IMPORT_INGS = {"Flour", "Fresh Pasta", "Pepperoni"}  # Tomato Sauce has dual source

    for ing, demand_kg in fwd_demand.items():
        on_hand = state.inventory.get(ing).total_kg if ing in state.inventory else 0.0
        effective = on_hand + pending_effective.get(ing, 0.0)
        # For Italian Imports ingredients, use 7-day forecast (next delivery is ≤7 days out)
        if ing in ITALIAN_IMPORT_INGS:
            demand_kg = fwd_demand_7d.get(ing, demand_kg)
        target = demand_kg * buffer
        shortfall = target - effective

        # Skip if no shortfall, or if overstocked and expiring
        if shortfall <= 0:
            continue
        if sig.inventory_overstocked.get(ing):
            continue

        # Pick a supplier (cheapest available; switch if disruption)
        sup_options = INGREDIENT_SUPPLIERS.get(ing, [])
        if not sup_options:
            continue

        # If disruption flag set on this ingredient, prefer alternative supplier
        if sig.supply_disruption.get(ing) and len(sup_options) > 1:
            chosen_sup = sup_options[1][0]
        else:
            chosen_sup = sup_options[0][0]

        # Check the supplier is in the active supplier_catalog (still operational)
        if chosen_sup not in state.supplier_catalog:
            # Try alternative
            if len(sup_options) > 1 and sup_options[1][0] in state.supplier_catalog:
                chosen_sup = sup_options[1][0]
            else:
                continue

        sup_info = SUPPLIERS[chosen_sup]

        # ── 4. Spoilage cap ──
        shelf = 7  # default; refined per-ingredient if in state.inventory
        if ing in state.inventory:
            shelf = max(1, state.inventory[ing].shelf_life_days)
        # Use 7-day demand at most (to leave room for the supplier's actual fill rate)
        max_useful = (demand_kg / 3.0) * min(shelf, 7) * 1.5
        order_qty = min(shortfall, max_useful)

        # ── 5. Round up to supplier minimum ──
        order_qty = _round_up_to_min(order_qty, sup_info["min"])

        # ── 7. Delivery-window check ──
        # The server queues orders for the next valid delivery day. Always place
        # the order; the server figures out when it arrives. We just need to verify
        # a valid delivery day exists within 7 days. (Italian Imports = Wed-only
        # with 3-day lead = at most 6 days out.)
        ok, days_to_arr = _will_deliver(today_dow, sup_info)
        if not ok:
            continue

        # ── 6. Supplier-price check from current catalog (handles inflation) ──
        catalog_entry = state.supplier_catalog.get(chosen_sup, {})
        price = catalog_entry.get("ingredients", {}).get(ing)
        if price is None:
            # Supplier no longer carries this ingredient (outage)
            if len(sup_options) > 1:
                alt = sup_options[1][0]
                if alt in state.supplier_catalog and ing in state.supplier_catalog[alt].get("ingredients", {}):
                    chosen_sup = alt
                    sup_info = SUPPLIERS[chosen_sup]
                    price = state.supplier_catalog[chosen_sup]["ingredients"][ing]
                    order_qty = _round_up_to_min(shortfall, sup_info["min"])
                else:
                    continue
            else:
                continue

        cost = order_qty * float(price)

        # Priority: critical ingredients (single-source, low fill, pepperoni) first
        priority = 0
        if ing == "Pepperoni" and sig.pepperoni_stockout_risk:
            priority = -100  # highest priority
        elif ing in ("Salmon", "Chicken", "Mozzarella"):
            priority = -50
        else:
            priority = int(cost)  # cheaper orders first to fit budget

        reason = f"need {shortfall:.1f}kg, buffer {buffer}"
        candidates.append((priority, ing, chosen_sup, round(order_qty, 1), reason))

    # ── 8. Budget cap ──
    candidates.sort()  # lowest priority number first

    spent = 0.0
    for prio, ing, sup, qty, reason in candidates:
        catalog = state.supplier_catalog.get(sup, {}).get("ingredients", {})
        price = catalog.get(ing, 0)
        cost = qty * price
        if spent + cost > available_cash:
            continue
        orders.append({
            "tool": "place_order",
            "args": {"supplier": sup, "ingredient": ing, "quantity_kg": qty},
        })
        spent += cost

    return orders
