"""DOW-aware staffing template with signal-based modifiers.

Defaults from recon: Sat=11 (was getting "Many" walkouts in 12/12 games with staff=6).
Modifiers are additive then clamped to [3, 15].
"""

from __future__ import annotations

from .constants import DOW_STAFF_DEFAULT, STAFF_MIN, STAFF_MAX


def plan_staff(state, sig, memory, mode: str) -> int:
    base = DOW_STAFF_DEFAULT.get(state.day_of_week, 6)

    # Mode adjustments first
    if mode == "EMERGENCY":
        return STAFF_MIN
    if mode == "RENOVATION":
        # Half tables → roughly half throughput. Don't pay for staff that idle.
        base = max(4, base // 2 + 2)
    if mode == "SURGE":
        # Tourist surge: demand can be 2-4× baseline. Push toward max.
        base = max(base, 13) if state.day_of_week in ("Friday", "Saturday") else max(base, 10)
    if mode == "END_GAME":
        base = base + 1  # don't tank quality in last 5 days

    # Additive modifiers
    delta = 0
    if sig.demand_surge and mode != "SURGE":
        delta += 2
    if sig.demand_surge_persistent:
        delta += 1
    if sig.demand_collapse and not sig.kitchen_bound:
        delta -= 2
    if sig.kitchen_bound:
        delta += 1
    if sig.kitchen_bound_persistent:
        delta += 2
    if sig.ghost_review_spike:
        delta += 1
    if sig.capacity_bound and not sig.kitchen_bound:
        # No point adding staff if tables are the binding constraint
        delta = min(delta, 0)

    # Weather: stormy = fewer customers, cut staff
    if state.weather_today == "stormy":
        delta -= 2
    elif state.weather_today == "rainy":
        delta -= 1
    elif state.weather_today == "sunny" and state.day_of_week in ("Friday", "Saturday", "Sunday"):
        delta += 1  # sunny weekend = boost

    result = base + delta
    return max(STAFF_MIN, min(STAFF_MAX, result))


def apply_llm_delta(default_staff: int, llm_delta: int) -> int:
    """Apply LLM's staff_delta and clamp."""
    return max(STAFF_MIN, min(STAFF_MAX, default_staff + max(-2, min(2, int(llm_delta)))))
