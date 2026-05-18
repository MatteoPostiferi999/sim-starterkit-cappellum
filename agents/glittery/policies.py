"""Mode decision — single source of truth for which posture the agent takes today.

Modes: NORMAL / DEFENSIVE / END_GAME / SURGE / RENOVATION / EMERGENCY
"""

from __future__ import annotations


def decide_mode(state, sig, memory) -> str:
    # Survival first — bankruptcy threat overrides everything
    if sig.bankrupt_risk:
        return "EMERGENCY"

    # Renovation has its own posture (constrained capacity)
    if sig.renovation_active:
        return "RENOVATION"

    # Reputation shock — defensive lock for 5 days
    if sig.reputation_shock or memory.defensive_lock_days > 0:
        return "DEFENSIVE"

    # End-game (counter-intuitive: increase quality investment late)
    if sig.end_game_phase:
        return "END_GAME"

    # Tourist surge — fire on persistence (3 consecutive days above baseline) or
    # on a single extreme spike (>3x baseline). Avoids flipping off noise.
    if sig.demand_surge_persistent or sig.covers_today_vs_baseline > 3.0:
        return "SURGE"

    # Cash trajectory bad → conservative
    if sig.cash_trajectory_bad:
        return "DEFENSIVE"

    # Reputation declined yesterday or still recovering
    if sig.reputation_decline or sig.recovery_hysteresis_active:
        return "DEFENSIVE"

    return "NORMAL"
