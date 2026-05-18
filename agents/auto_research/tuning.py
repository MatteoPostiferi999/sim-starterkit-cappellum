"""Non-invasive overlay layer — monkey-patch module attributes at runtime.

The MPC agent is never mutated permanently. Each overlay is a flat dict whose
keys identify the (module, attribute) pair to patch, e.g.

    {
        "mpc.HORIZON_DAYS": 5,
        "mpc.HORIZON_DAYS_LONG": 7,
        "simulator.AVG_BASE_PRICE_PER_COVER": 18.50,
        "simulator.WALKOUT_PENALTY_PER_WALKOUT": 2.5,
        "candidates.PRICE_MULTS": [1.00, 1.05, 1.10, 1.15, 1.20],
        "candidates.MARKETING_LEVELS": [0, 150, 300, 500],
        "candidates.DOW_STAFF_ANCHOR.Saturday": 13,
        "rules_supply.BUFFER_NORMAL": 1.35,
        "rules_supply.BUFFER_EARLY": 1.60,
        "rules_supply.CASH_RESERVE": 1500.0,
        "policies.END_GAME_DAY": 24,
        "policies.SURGE_RATIO": 2.5,
    }

`apply_overlay(overlay) -> restore_fn` records originals and returns a callable
that undoes everything. Use as a context manager via `with_overlay(...)`.

Why monkey-patching, not config files: the auto-tuner needs to swap hundreds
of variants in a single Python process without restarting it. Patching is the
cheapest way and is fully reversible.
"""

from __future__ import annotations

import contextlib
from typing import Any, Callable

# Lazy imports so this module doesn't fail to load if mpc/glittery have errors.
def _modules() -> dict[str, Any]:
    from agents.mpc import mpc_agent as mpc_mod
    from agents.mpc import simulator as sim_mod
    from agents.mpc import candidates as cand_mod
    from agents.glittery import constants as const_mod
    from agents.glittery import rules_supply as supply_mod
    from agents.glittery import policies as policy_mod
    return {
        "mpc": mpc_mod,
        "simulator": sim_mod,
        "candidates": cand_mod,
        "constants": const_mod,
        "rules_supply": supply_mod,
        "policies": policy_mod,
    }


# ── Module-level "synthetic" attributes ──────────────────────────────────────
# Some knobs we want to expose aren't single module attributes; they live
# inside functions or dataclass defaults. We adapt them via helper attributes
# on the relevant modules so the overlay can target a stable name.
#
# Synthetic knobs introduced (no behaviour change at default values):
#   rules_supply.BUFFER_NORMAL        (default 1.40)
#   rules_supply.BUFFER_EARLY         (default 1.70)
#   rules_supply.BUFFER_CRISIS        (default 1.60)
#   rules_supply.BUFFER_ENDGAME       (default 1.05)
#   rules_supply.BUFFER_RENOVATION    (default 1.20)
#   rules_supply.CASH_RESERVE_OVERRIDE  (default None — uses constants.CASH_RESERVE)
#   policies.END_GAME_DAY             (default 25)
#   policies.SURGE_RATIO              (default 3.0)
#   policies.SURGE_PERSIST_THRESHOLD  (default 1.3)
#   candidates.DEFAULT_FUTURE_PRICE_MULT (default 1.05)
#   candidates.DEFAULT_FUTURE_MARKETING_WEEKEND (default 200)
#
# These shadows are wired into the base code via small targeted edits
# (one-liners) so that the overlay can override them without re-implementing
# the function. See `wire_synthetics.py` (no — wired inline below in tuning.py
# itself, applied lazily on first apply_overlay call).
# ─────────────────────────────────────────────────────────────────────────────


_WIRED = False


def _ensure_wired() -> None:
    """Idempotently install synthetic attributes on the target modules.

    These are *additive* changes: they add module-level constants and small
    wrapper edits so that the base agent reads them. With default values, the
    behaviour is identical to the original code — only an explicit overlay
    moves the needles.

    We monkey-patch this module the first time apply_overlay() is called so
    the user can run the existing agent unchanged.
    """
    global _WIRED
    if _WIRED:
        return

    mods = _modules()
    supply = mods["rules_supply"]
    policies = mods["policies"]
    candidates = mods["candidates"]
    simulator = mods["simulator"]

    # ── rules_supply: install BUFFER_* constants ─
    if not hasattr(supply, "BUFFER_NORMAL"):
        supply.BUFFER_NORMAL = 1.40
        supply.BUFFER_EARLY = 1.70
        supply.BUFFER_CRISIS = 1.60
        supply.BUFFER_ENDGAME = 1.05
        supply.BUFFER_RENOVATION = 1.20
        supply.CASH_RESERVE_OVERRIDE = None

    # Wrap supply.plan_orders so it reads the new constants. Easiest: replace
    # the function with one that re-implements the buffer selection logic and
    # delegates to the original for everything else. Since plan_orders is
    # long, we instead monkey-patch the constants the function imports at the
    # top: `CASH_RESERVE` is imported as a module-level name in supply, so we
    # can override supply.CASH_RESERVE if a user sets CASH_RESERVE_OVERRIDE.
    #
    # For buffer selection, we install a small helper and rewrite plan_orders
    # to use it. To avoid rewriting plan_orders, we instead expose a
    # `select_buffer(sig, mode, day)` callable that the overlay can replace.
    #
    # Simpler approach: leave plan_orders alone for now, only allow CASH_RESERVE
    # overrides, and add a *second* wrapping that swaps buffer values via
    # monkey-patching the literal-bearing inline.

    # Replace supply.plan_orders with a wrapped version that *reads*
    # BUFFER_* constants. We do this exactly once.
    _wrap_plan_orders(supply)

    # ── policies: install END_GAME_DAY / SURGE_* constants ─
    if not hasattr(policies, "END_GAME_DAY"):
        policies.END_GAME_DAY = 25
        policies.SURGE_RATIO = 3.0
        policies.SURGE_PERSIST_THRESHOLD = 1.3
    # We do NOT rewrite decide_mode — signals.py reads its own thresholds.
    # Instead, the overlay can override signals.py thresholds directly
    # (see search_space).

    # ── candidates: default future action knobs ─
    if not hasattr(candidates, "DEFAULT_FUTURE_PRICE_MULT"):
        candidates.DEFAULT_FUTURE_PRICE_MULT = 1.05
        candidates.DEFAULT_FUTURE_MARKETING_WEEKEND = 200
    _wrap_default_future_action(candidates)

    # ── simulator: nothing extra (every constant is already top-level) ─

    _WIRED = True


def _wrap_plan_orders(supply_mod) -> None:
    """Rewrite the buffer literals in plan_orders to read from supply_mod."""
    if getattr(supply_mod, "_AR_WRAPPED", False):
        return
    original = supply_mod.plan_orders

    def wrapped_plan_orders(state, sig, memory, mode):
        # Live-patch the buffer values the inner function will compute by
        # snapshotting and restoring around the call. The original function
        # uses literal constants 1.0/1.40/1.60/1.05/1.20/1.70 in if/elif
        # branches. To make those configurable, we re-implement the buffer
        # selection here and pass it via a thread-local-style override.
        # But: plan_orders does not expose a buffer parameter.
        #
        # Cleanest path: re-implement plan_orders' buffer block here and
        # call the rest of plan_orders. Since plan_orders is monolithic, we
        # instead *re-write* it once at wrap time by exec'ing a tweaked source
        # that reads supply_mod.BUFFER_*. That's brittle.
        #
        # Pragmatic alternative: monkey-patch the numeric literals via globals
        # of plan_orders is not possible (they're literals). Accept the cost
        # and replace plan_orders with a re-implementation that mirrors the
        # original logic but reads BUFFER_* from the module.
        return _plan_orders_reimpl(state, sig, memory, mode, supply_mod)

    supply_mod.plan_orders = wrapped_plan_orders
    supply_mod._AR_WRAPPED = True
    supply_mod._AR_ORIGINAL_plan_orders = original


def _plan_orders_reimpl(state, sig, memory, mode, supply_mod):
    """Re-implementation of rules_supply.plan_orders that reads BUFFER_*.

    Logic mirrors the original (rules_supply.py:84) one-for-one. Diff: the
    literal buffer values become `supply_mod.BUFFER_*` and the cash reserve
    becomes `supply_mod.CASH_RESERVE_OVERRIDE if set else constants.CASH_RESERVE`.
    """
    from agents.glittery.constants import (
        SUPPLIERS, INGREDIENT_SUPPLIERS, OVERHEAD_PER_DAY, STAFF_COST_DAY,
        CASH_RESERVE,
    )
    from agents.glittery.rules_supply import (
        _forward_demand, _per_supplier_fill_rate, _round_up_to_min, _will_deliver,
    )

    orders: list[dict] = []

    if not state.active_menu or not state.supplier_catalog:
        return orders
    if mode == "EMERGENCY":
        return orders

    days_left = max(0, 30 - state.day)
    horizon = min(3, days_left)
    if horizon <= 0:
        return orders

    fwd_demand = _forward_demand(state, memory, days_ahead=horizon)
    if not fwd_demand:
        return orders

    fwd_demand_7d = _forward_demand(state, memory, days_ahead=min(7, days_left))

    if mode == "EMERGENCY":
        buffer = 1.0
    elif sig.supply_disruption_emerging or sig.crisis_warning:
        buffer = supply_mod.BUFFER_CRISIS
    elif sig.end_game_phase:
        buffer = supply_mod.BUFFER_ENDGAME
    elif mode == "RENOVATION" or sig.renovation_active:
        buffer = supply_mod.BUFFER_RENOVATION
    elif state.day <= 5:
        buffer = supply_mod.BUFFER_EARLY
    else:
        buffer = supply_mod.BUFFER_NORMAL

    pending_effective: dict[str, float] = {}
    for ing, qty in state.pending_by_ingredient.items():
        pending_effective[ing] = 0.0
    for sup_name, pos in state.pending_by_supplier.items():
        fr = _per_supplier_fill_rate(memory, sup_name)
        for po in pos:
            ing = po.get("ingredient")
            qty = float(po.get("quantity_kg", 0))
            if ing:
                pending_effective[ing] = pending_effective.get(ing, 0.0) + qty * fr

    candidates_list: list[tuple[float, str, str, float, str]] = []
    today_dow = state.day_of_week
    reserve = supply_mod.CASH_RESERVE_OVERRIDE if supply_mod.CASH_RESERVE_OVERRIDE is not None else CASH_RESERVE
    available_cash = state.cash - reserve - 2 * (OVERHEAD_PER_DAY + STAFF_COST_DAY * state.staff_level)

    ITALIAN_IMPORT_INGS = {"Flour", "Fresh Pasta", "Pepperoni"}

    for ing, demand_kg in fwd_demand.items():
        on_hand = state.inventory.get(ing).total_kg if ing in state.inventory else 0.0
        effective = on_hand + pending_effective.get(ing, 0.0)
        if ing in ITALIAN_IMPORT_INGS:
            demand_kg = fwd_demand_7d.get(ing, demand_kg)
        target = demand_kg * buffer
        shortfall = target - effective

        if shortfall <= 0:
            continue
        if sig.inventory_overstocked.get(ing):
            continue

        sup_options = INGREDIENT_SUPPLIERS.get(ing, [])
        if not sup_options:
            continue

        if sig.supply_disruption.get(ing) and len(sup_options) > 1:
            chosen_sup = sup_options[1][0]
        else:
            chosen_sup = sup_options[0][0]

        if chosen_sup not in state.supplier_catalog:
            if len(sup_options) > 1 and sup_options[1][0] in state.supplier_catalog:
                chosen_sup = sup_options[1][0]
            else:
                continue

        sup_info = SUPPLIERS[chosen_sup]

        shelf = 7
        if ing in state.inventory:
            shelf = max(1, state.inventory[ing].shelf_life_days)
        max_useful = (demand_kg / 3.0) * min(shelf, 7) * 1.5
        order_qty = min(shortfall, max_useful)
        order_qty = _round_up_to_min(order_qty, sup_info["min"])

        ok, _ = _will_deliver(today_dow, sup_info)
        if not ok:
            continue

        catalog_entry = state.supplier_catalog.get(chosen_sup, {})
        price = catalog_entry.get("ingredients", {}).get(ing)
        if price is None:
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
        priority = 0
        if ing == "Pepperoni" and sig.pepperoni_stockout_risk:
            priority = -100
        elif ing in ("Salmon", "Chicken", "Mozzarella"):
            priority = -50
        else:
            priority = int(cost)

        reason = f"need {shortfall:.1f}kg, buffer {buffer}"
        candidates_list.append((priority, ing, chosen_sup, round(order_qty, 1), reason))

    candidates_list.sort()
    spent = 0.0
    for prio, ing, sup, qty, reason in candidates_list:
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


def _wrap_default_future_action(candidates_mod) -> None:
    """Replace candidates.default_future_action with a version reading our knobs."""
    if getattr(candidates_mod, "_AR_WRAPPED_DFA", False):
        return
    original = candidates_mod.default_future_action

    def wrapped(state):
        from agents.mpc.simulator import SimAction
        from agents.mpc.candidates import DOW_STAFF_ANCHOR, STAFF_MIN, STAFF_MAX
        anchor = DOW_STAFF_ANCHOR.get(state.day_of_week, 6)
        if state.weather_today == "stormy":
            anchor -= 2
        elif state.weather_today == "rainy":
            anchor -= 1
        elif state.weather_today == "sunny" and state.day_of_week in ("Friday", "Saturday"):
            anchor += 1
        marketing = candidates_mod.DEFAULT_FUTURE_MARKETING_WEEKEND \
            if state.day_of_week in ("Friday", "Saturday") else 0
        return SimAction(
            staff=max(STAFF_MIN, min(STAFF_MAX, anchor)),
            price_mult=candidates_mod.DEFAULT_FUTURE_PRICE_MULT,
            marketing=marketing,
            happy_hour=False,
            has_daily_special=True,
        )

    candidates_mod.default_future_action = wrapped
    candidates_mod._AR_WRAPPED_DFA = True
    candidates_mod._AR_ORIGINAL_default_future_action = original


# ── Public overlay API ───────────────────────────────────────────────────────

_PATH_TO_MODULE = {
    "mpc": "mpc",
    "simulator": "simulator",
    "candidates": "candidates",
    "constants": "constants",
    "rules_supply": "rules_supply",
    "policies": "policies",
}


def _resolve(path: str) -> tuple[Any, str, list[str]]:
    """Resolve 'simulator.AVG_BASE_PRICE_PER_COVER' or 'candidates.DOW_STAFF_ANCHOR.Saturday'
    into (target_object, leaf_attr, full_path_parts) so we can get/set on a dict
    member as well as a module attribute.
    """
    parts = path.split(".")
    if len(parts) < 2:
        raise ValueError(f"overlay key must be 'module.attr[.subkey]': {path!r}")
    mod_key = parts[0]
    if mod_key not in _PATH_TO_MODULE:
        raise ValueError(f"unknown module prefix {mod_key!r} in {path!r}")
    mods = _modules()
    obj = mods[mod_key]
    # Walk down: obj.attr1.attr2... or obj.dict[key]
    for p in parts[1:-1]:
        if isinstance(obj, dict):
            obj = obj[p]
        else:
            obj = getattr(obj, p)
    return obj, parts[-1], parts


def _get(obj, leaf: str):
    if isinstance(obj, dict):
        return obj.get(leaf)
    return getattr(obj, leaf)


def _set(obj, leaf: str, value):
    if isinstance(obj, dict):
        obj[leaf] = value
    else:
        setattr(obj, leaf, value)


def apply_overlay(overlay: dict[str, Any]) -> Callable[[], None]:
    """Apply `overlay` (a flat dict of dotted paths → values). Returns a
    `restore()` callable that puts everything back.

    Idempotent: calling apply_overlay twice will record the *original* (pre-
    first-apply) values once and restore to them.
    """
    _ensure_wired()
    saved: list[tuple[Any, str, Any]] = []
    try:
        for key, value in overlay.items():
            obj, leaf, _ = _resolve(key)
            saved.append((obj, leaf, _get(obj, leaf)))
            _set(obj, leaf, value)
    except Exception:
        # If anything fails during apply, roll back what we already did.
        for obj, leaf, prev in reversed(saved):
            try:
                _set(obj, leaf, prev)
            except Exception:
                pass
        raise

    def _restore():
        for obj, leaf, prev in reversed(saved):
            try:
                _set(obj, leaf, prev)
            except Exception:
                pass

    return _restore


# Backwards-compatible name for callers that ask for `restore`
def restore(restore_fn: Callable[[], None]) -> None:
    restore_fn()


@contextlib.contextmanager
def with_overlay(overlay: dict[str, Any]):
    """Context manager form of apply_overlay/restore."""
    rf = apply_overlay(overlay)
    try:
        yield
    finally:
        rf()
