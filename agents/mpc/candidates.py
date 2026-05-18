"""Generate candidate action sets for MPC to evaluate.

The action space is enumerated with sensible defaults centered on the
deterministic baseline. Total candidates per turn ~60-100 — fast to score.
"""

from __future__ import annotations

from .simulator import SimAction


# Default staffing per DOW (anchors the search)
DOW_STAFF_ANCHOR = {
    "Monday": 4, "Tuesday": 6, "Wednesday": 6, "Thursday": 8,
    "Friday": 10, "Saturday": 12, "Sunday": 3,
}

# Stay within API bounds
STAFF_MIN, STAFF_MAX = 3, 15
PRICE_MULTS = [0.95, 1.00, 1.05, 1.10, 1.15, 1.20]   # 1.20 added to suppress demand during renovation
MARKETING_LEVELS = [0, 200, 500]


def enumerate_candidates(state, sig=None) -> list[SimAction]:
    """Generate candidate action sets for today.

    state: glittery.state.WorldState
    Returns ~60 candidates clustered around sensible defaults.
    """
    candidates: list[SimAction] = []

    anchor = DOW_STAFF_ANCHOR.get(state.day_of_week, 6)

    # Weather hint
    if state.weather_today == "stormy":
        anchor -= 2
    elif state.weather_today == "rainy":
        anchor -= 1
    elif state.weather_today == "sunny" and state.day_of_week in ("Friday", "Saturday"):
        anchor += 1

    # Anchor the staff search at the DOW default ± 2
    staff_options = sorted(set(
        max(STAFF_MIN, min(STAFF_MAX, anchor + d)) for d in (-2, -1, 0, 1, 2)
    ))

    # If signals say surge, push staff higher
    if sig is not None and getattr(sig, "demand_surge", False):
        staff_options = sorted(set(staff_options + [min(STAFF_MAX, anchor + 3), min(STAFF_MAX, anchor + 4)]))

    # Restrict on Sun/Mon to avoid overspending (recon shows ~0 covers many such days)
    if state.day_of_week in ("Sunday", "Monday"):
        staff_options = [s for s in staff_options if s <= 5]

    # Renovation: capacity-bound by tables → fewer staff helps margins
    is_renovation = (sig is not None and getattr(sig, "renovation_active", False))
    if is_renovation:
        staff_options = [s for s in staff_options if s <= 8]

    # Happy hour gating (cooldown after 2+ consecutive days)
    hh_options: list[bool] = [False]
    # Add hh=True only on slow weekdays and if not capacity-bound
    if (state.day_of_week in ("Tuesday", "Wednesday", "Thursday")
            and not (sig and getattr(sig, "capacity_bound", False))
            and not is_renovation):
        hh_options.append(True)

    daily_special_options: list[bool] = [True, False]

    for staff in staff_options:
        for price_mult in PRICE_MULTS:
            for marketing in MARKETING_LEVELS:
                for hh in hh_options:
                    # Skip extreme marketing on capacity-bound days
                    if sig and getattr(sig, "capacity_bound", False) and marketing > 0:
                        continue
                    # Skip high marketing on Sun/Mon (low demand DOW)
                    if state.day_of_week in ("Sunday", "Monday") and marketing > 200:
                        continue
                    # During renovation, NO marketing (tables are the bottleneck)
                    if is_renovation and marketing > 0:
                        continue
                    candidates.append(SimAction(
                        staff=staff,
                        price_mult=price_mult,
                        marketing=marketing,
                        happy_hour=hh,
                        has_daily_special=True,  # always include (free upside)
                    ))

    return candidates


def default_future_action(state) -> "SimAction":
    """Default action used by the simulator for days 2..N of the horizon.

    A reasonable middle-of-the-road policy that the MPC compares its first-day
    action against in expected forward-rolling outcomes.
    """
    from .simulator import SimAction
    anchor = DOW_STAFF_ANCHOR.get(state.day_of_week, 6)
    if state.weather_today == "stormy":
        anchor -= 2
    elif state.weather_today == "rainy":
        anchor -= 1
    elif state.weather_today == "sunny" and state.day_of_week in ("Friday", "Saturday"):
        anchor += 1
    return SimAction(
        staff=max(STAFF_MIN, min(STAFF_MAX, anchor)),
        price_mult=1.05,
        marketing=200 if state.day_of_week in ("Friday", "Saturday") else 0,
        happy_hour=False,
        has_daily_special=True,
    )
