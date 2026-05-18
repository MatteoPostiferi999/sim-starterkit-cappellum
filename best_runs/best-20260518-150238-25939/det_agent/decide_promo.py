"""Marketing, happy hour, and daily special decisions."""

from __future__ import annotations

from .constants import RECIPES, ALL_DISHES
from .predict import demand_estimate, capacity_estimate
from . import config


# Happy hour gates
HH_MIN_REST_DAYS = 2     # days between runs after a streak
HH_MAX_STREAK = 3        # diminishing returns after 3 consecutive days
HH_UTIL_GATE = 0.80      # don't run if peak utilization > 0.80

# Marketing spend levels
MKT_DEFAULT = 200
MKT_LOW = 100
MKT_HIGH = 350


def decide_happy_hour(state, memory) -> bool:
    """Should we run happy hour today?"""
    if config.HH_MODE == "off":
        return False
    if state.table_util_peak >= HH_UTIL_GATE:
        return False
    if state.walkout_rank >= 2:
        return False
    # Never run during renovation — capacity already maxed
    if memory.scen_renovation and state.day <= memory.renov_start + 13:
        return False
    if memory.hh_streak >= HH_MAX_STREAK:
        return False
    days_since = state.day - memory.last_hh_day
    if memory.hh_streak >= 2 and days_since < HH_MIN_REST_DAYS:
        return False
    if state.cash < 3500:
        return False
    if config.HH_MODE == "aggressive":
        if state.day_of_week in ("Sunday", "Monday", "Tuesday", "Wednesday"):
            return True
        if state.weather_today in ("rainy", "stormy"):
            return True
        if state.days_remaining <= 4:
            return False
        return False
    # default
    if state.day_of_week in ("Monday", "Tuesday", "Wednesday"):
        return True
    if state.weather_today in ("rainy", "stormy"):
        return True
    if memory.scen_tourist and 5 <= state.day <= 9:
        return True
    if state.days_remaining <= 4:
        return False
    return False


def decide_marketing(state, memory) -> int:
    """Marketing spend (0-500). Driven by config.MARKETING_MODE."""
    mode = config.MARKETING_MODE
    if mode == "off":
        return 0
    # Cash floor — always honor
    if state.cash < 4000:
        return 0
    # Don't spend when capacity-bound
    if state.table_util_peak >= 0.85:
        return 0
    if state.walkout_rank >= 2:
        return 0
    if state.customer_trend == "Declining" and state.reputation_rank <= 1:
        return 0
    if state.days_remaining <= 4:
        return 0
    # Renovation: don't spend during capacity-limited period (waste of cash)
    if memory.scen_renovation and state.day <= memory.renov_start + 13:
        return 0
    # Post-renov: push marketing to capitalize on satisfaction bonus
    if memory.scen_renovation and memory.renov_start + 14 <= state.day <= memory.renov_start + 22:
        return MKT_HIGH
    if mode == "heavy":
        if 2 <= state.day <= 22:
            return MKT_HIGH
        return MKT_LOW if state.day <= 27 else 0
    if mode == "low":
        if 3 <= state.day <= 12:
            return MKT_LOW
        return 0
    # default
    if 3 <= state.day <= 9:
        return MKT_HIGH
    if 10 <= state.day <= 20:
        if state.day_of_week in ("Sunday", "Monday", "Tuesday") and state.table_util_peak < 0.70:
            return MKT_LOW
        return 0
    return 0


def decide_daily_special(state, memory) -> str | None:
    """Pick the daily special. Always include one — free satisfaction bonus."""
    active = state.active_menu or ALL_DISHES
    if not active:
        return None

    # Prefer dishes using ingredients that expire in <=2 days
    best: tuple[str, float] | None = None  # (dish, expiring_kg)
    for dish in active:
        _, recipe = RECIPES.get(dish, (0, {}))
        score = 0.0
        for ing in recipe:
            inv = state.inventory.get(ing)
            if inv:
                # Prioritize larger expiring quantities
                score += inv.expiring_within(2)
        if best is None or score > best[1]:
            best = (dish, score)
    if best and best[1] > 0.3:
        return best[0]

    # Otherwise: pick highest-margin dish available
    by_margin = sorted(
        [d for d in active if d in RECIPES],
        key=lambda d: -RECIPES[d][0],
    )
    return by_margin[0] if by_margin else None
