"""Menu composition. Keep all 8 dishes active unless an ingredient is unavailable."""

from __future__ import annotations

from .constants import ALL_DISHES, RECIPES, MENU_MIN_DISHES


# Ingredient-to-dish dependency (precomputed)
DISH_INGS = {dish: list(recipe.keys()) for dish, (_p, recipe) in RECIPES.items()}


def plan_menu(state, memory) -> list[str]:
    """Return list of dishes to enable."""
    available = []
    for dish in ALL_DISHES:
        if dish not in state.menu_book:
            continue
        # Check every ingredient: on-hand + pending arriving in ≤2 days
        ok = True
        for ing in DISH_INGS.get(dish, []):
            inv = state.inventory.get(ing)
            on_hand = inv.total_kg if inv else 0.0
            # Pending arriving in next 2 days
            pending_soon = sum(
                float(po.get("quantity_kg", 0)) for po in state.pending_orders
                if po.get("ingredient") == ing
                and int(po.get("delivery_day", 99)) - state.day <= 2
            )
            need_per_cover = RECIPES[dish][1][ing]
            # Need at least 1.5 covers worth to keep dish on menu
            if on_hand + pending_soon < need_per_cover * 1.5:
                ok = False
                break
        if ok:
            available.append(dish)

    # Always keep ≥5 dishes — if we'd shrink below, force-include least-constrained
    if len(available) < MENU_MIN_DISHES:
        remaining = [d for d in ALL_DISHES if d in state.menu_book and d not in available]
        # Sort by "least missing" — fewer empty ingredients first
        def lack(d):
            return sum(
                1 for ing in DISH_INGS.get(d, [])
                if (state.inventory.get(ing).total_kg if state.inventory.get(ing) else 0) < 0.5
            )
        remaining.sort(key=lack)
        for d in remaining:
            if len(available) >= MENU_MIN_DISHES:
                break
            available.append(d)

    return available
