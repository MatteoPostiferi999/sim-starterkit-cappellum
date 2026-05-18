"""Final action validator. Drop or fix anything that violates game rules."""

from __future__ import annotations

from .constants import (
    SUPPLIERS, RECIPES, STAFF_MIN, STAFF_MAX, MARKETING_MIN, MARKETING_MAX,
    MENU_MIN_DISHES, PRICE_MULT_MIN, PRICE_MULT_MAX, CASH_RESERVE,
)


def validate(plan: list[dict], state) -> list[dict]:
    """Return a cleaned plan with invalid actions removed/fixed."""
    out: list[dict] = []
    cash_left = state.cash
    seen_staff = False
    seen_menu = False
    seen_hh = False
    seen_special = False
    seen_marketing = False
    set_prices_for: set[str] = set()
    set_orders_for: set[tuple[str, str]] = set()

    for act in plan:
        tool = act.get("tool")
        args = act.get("args", {}) or {}
        if tool == "place_order":
            sup = args.get("supplier")
            ing = args.get("ingredient")
            kg = float(args.get("quantity_kg", 0))
            sup_info = SUPPLIERS.get(sup)
            if not sup_info or ing not in sup_info["ings"]:
                continue
            if kg < sup_info["min"]:
                kg = sup_info["min"]
            cost = kg * sup_info["ings"][ing]
            if cash_left - cost < CASH_RESERVE:
                continue
            key = (sup, ing)
            if key in set_orders_for:
                continue
            set_orders_for.add(key)
            cash_left -= cost
            out.append({"tool": "place_order", "args": {"supplier": sup, "ingredient": ing, "quantity_kg": float(round(kg, 1))}})

        elif tool == "set_staff_level":
            if seen_staff:
                continue
            level = int(args.get("level", state.staff_level))
            level = max(STAFF_MIN, min(STAFF_MAX, level))
            seen_staff = True
            out.append({"tool": "set_staff_level", "args": {"level": level}})

        elif tool == "set_menu":
            if seen_menu:
                continue
            dishes = list(args.get("dishes", []) or [])
            dishes = [d for d in dishes if d in state.menu_book]
            # dedup preserving order
            seen = set()
            dishes = [d for d in dishes if not (d in seen or seen.add(d))]
            if len(dishes) < MENU_MIN_DISHES:
                # Fill from available menu_book
                for d in state.menu_book:
                    if d not in dishes:
                        dishes.append(d)
                    if len(dishes) >= MENU_MIN_DISHES:
                        break
            seen_menu = True
            out.append({"tool": "set_menu", "args": {"dishes": dishes}})

        elif tool == "set_price":
            dish = args.get("dish")
            price = float(args.get("price", 0))
            entry = state.menu_book.get(dish)
            if not entry or price <= 0:
                continue
            base = float(entry.get("base_price", 0))
            if base <= 0:
                continue
            # Clamp
            lo, hi = base * PRICE_MULT_MIN, base * PRICE_MULT_MAX
            price = max(lo, min(hi, price))
            if dish in set_prices_for:
                continue
            set_prices_for.add(dish)
            out.append({"tool": "set_price", "args": {"dish": dish, "price": round(price, 2)}})

        elif tool == "set_marketing_spend":
            if seen_marketing:
                continue
            amount = float(args.get("amount", 0))
            amount = max(MARKETING_MIN, min(MARKETING_MAX, amount))
            if cash_left - amount < CASH_RESERVE:
                amount = max(0.0, cash_left - CASH_RESERVE)
                amount = round(amount / 50) * 50
            if amount <= 0:
                continue
            seen_marketing = True
            cash_left -= amount
            out.append({"tool": "set_marketing_spend", "args": {"amount": float(amount)}})

        elif tool == "run_happy_hour":
            if seen_hh:
                continue
            seen_hh = True
            out.append({"tool": "run_happy_hour", "args": {}})

        elif tool == "offer_daily_special":
            if seen_special:
                continue
            dish = args.get("dish")
            if dish not in state.menu_book:
                continue
            seen_special = True
            out.append({"tool": "offer_daily_special", "args": {"dish": dish}})

        elif tool == "save_notes":
            # save_notes will be appended at the very end by strategy.py
            continue

        else:
            out.append(act)

    return out
