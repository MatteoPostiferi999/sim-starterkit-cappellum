"""Final safety pass — re-validate every action AFTER LLM merge.

The LLM could pick a marketing spend that breaches the cash reserve, or a
price outside the API's valid range, etc. This module drops or fixes any
unsafe action before submission.
"""

from __future__ import annotations

from .constants import (
    STAFF_MIN, STAFF_MAX, MARKETING_MIN, MARKETING_MAX,
    PRICE_MULT_MIN, PRICE_MULT_MAX, MENU_MIN_DISHES,
    OVERHEAD_PER_DAY, STAFF_COST_DAY, CASH_RESERVE,
)


def validate(plan: list[dict], state, sig) -> list[dict]:
    """Filter and fix actions before submission. Never raises."""
    out: list[dict] = []
    cash_committed = 0.0  # cumulative committed spend from this plan

    # Pull supplier prices for cost validation
    catalog = state.supplier_catalog

    available_cash = max(0.0, state.cash - CASH_RESERVE - 2 * (OVERHEAD_PER_DAY + STAFF_COST_DAY * state.staff_level))

    for action in plan:
        tool = action.get("tool")
        args = action.get("args", {}) or {}

        if tool == "place_order":
            sup = args.get("supplier")
            ing = args.get("ingredient")
            qty = float(args.get("quantity_kg", 0))
            if not sup or not ing or qty <= 0:
                continue
            sup_entry = catalog.get(sup)
            if not sup_entry:
                continue
            price = sup_entry.get("ingredients", {}).get(ing)
            if price is None:
                continue
            cost = qty * float(price)
            if cash_committed + cost > available_cash:
                continue
            # Enforce min_order from current catalog
            min_order = sup_entry.get("min_order_kg", 0)
            if qty < min_order:
                qty = min_order
                cost = qty * float(price)
                if cash_committed + cost > available_cash:
                    continue
                args["quantity_kg"] = round(qty, 1)
            cash_committed += cost
            out.append({"tool": tool, "args": args})

        elif tool == "set_staff_level":
            level = int(args.get("level", state.staff_level))
            level = max(STAFF_MIN, min(STAFF_MAX, level))
            out.append({"tool": tool, "args": {"level": level}})

        elif tool == "set_menu":
            dishes = list(args.get("dishes") or [])
            # De-dupe and filter to recognized dishes
            seen = set()
            cleaned = []
            for d in dishes:
                if d in state.menu_book and d not in seen:
                    cleaned.append(d)
                    seen.add(d)
            if len(cleaned) < MENU_MIN_DISHES:
                # Pad with menu_book dishes
                for d in state.menu_book.keys():
                    if d not in seen:
                        cleaned.append(d)
                        seen.add(d)
                        if len(cleaned) >= MENU_MIN_DISHES:
                            break
            if len(cleaned) >= MENU_MIN_DISHES:
                out.append({"tool": tool, "args": {"dishes": cleaned}})

        elif tool == "set_price":
            dish = args.get("dish")
            price = float(args.get("price", 0))
            entry = state.menu_book.get(dish)
            if not entry:
                continue
            base = float(entry.get("base_price", 0))
            if base <= 0:
                continue
            # Clamp to [0.8x, 1.2x] base, but keep 0.001 margin from each end —
            # server rejects prices that exactly equal 1.20× (boundary, not range).
            lo = round(base * (PRICE_MULT_MIN + 0.001), 2)
            hi = round(base * (PRICE_MULT_MAX - 0.001), 2)
            price = max(lo, min(hi, round(price, 2)))
            out.append({"tool": tool, "args": {"dish": dish, "price": price}})

        elif tool == "set_marketing_spend":
            amount = int(args.get("amount", 0))
            amount = max(MARKETING_MIN, min(MARKETING_MAX, amount))
            if cash_committed + amount > available_cash:
                amount = max(0, int(available_cash - cash_committed))
            cash_committed += amount
            out.append({"tool": tool, "args": {"amount": amount}})

        elif tool == "run_happy_hour":
            out.append({"tool": tool, "args": {}})

        elif tool == "offer_daily_special":
            dish = args.get("dish")
            if dish and dish in state.menu_book:
                out.append({"tool": tool, "args": {"dish": dish}})

        elif tool == "save_notes":
            text = args.get("text", "")
            if isinstance(text, str):
                out.append({"tool": tool, "args": {"text": text[:4000]}})

        else:
            # Unknown tool — drop
            continue

    return out
