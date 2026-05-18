"""21 signal detectors. Pure functions of (WorldState, Memory) — no I/O.

All thresholds operate on observable quantities. No scenario-name matching.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .constants import (
    ALERT_KEYWORDS_CRISIS, ALERT_KEYWORDS_RENOVATION, ALERT_KEYWORDS_WARN,
    ALERT_KEYWORDS_SCARE, ALERT_KEYWORDS_FESTIVAL, ALERT_KEYWORDS_INFLATION,
    ALERT_KEYWORDS_BAN, OVERHEAD_PER_DAY, STAFF_COST_DAY, REP_RANK,
    INGREDIENT_SUPPLIERS, SUPPLIER_CODE,
)
from .memory import Memory, baseline_covers, supplier_fill_rate


@dataclass
class Signals:
    # Mode-driving
    bankrupt_risk: bool = False
    cash_trajectory_bad: bool = False
    demand_surge: bool = False
    demand_surge_persistent: bool = False
    demand_collapse: bool = False
    demand_collapse_persistent: bool = False
    capacity_bound: bool = False
    kitchen_bound: bool = False
    kitchen_bound_persistent: bool = False
    reputation_decline: bool = False
    reputation_shock: bool = False     # 2-band drop in 3 days
    recovery_hysteresis_active: bool = False
    renovation_active: bool = False
    crisis_warning: bool = False
    inflation_active: bool = False
    ghost_review_spike: bool = False
    end_game_phase: bool = False
    early_game_phase: bool = False

    # Per-ingredient signals
    supply_disruption: dict[str, bool] = field(default_factory=dict)         # ingredient -> bool
    supply_disruption_emerging: bool = False
    inventory_overstocked: dict[str, bool] = field(default_factory=dict)
    pepperoni_stockout_risk: bool = False
    salmon_waste_risk: bool = False

    # Numeric features (for LLM prompt and rule modifiers)
    baseline_covers_today: float = 0.0
    covers_today_vs_baseline: float = 1.0     # ratio
    cash_3d_avg_delta: float = 0.0
    fill_rate_min: float = 1.0


def compute(state, memory: Memory) -> Signals:
    s = Signals()

    # ── Phase signals ──
    s.early_game_phase = state.day <= 3
    s.end_game_phase = state.day >= 25

    # ── Cash/bankruptcy ──
    # Aggressive threshold: if cash < 3 days of overhead-at-current-staff, go defensive.
    # This is well above the absolute reserve and gives us time to react.
    overhead = OVERHEAD_PER_DAY + STAFF_COST_DAY * state.staff_level
    critical_pending = 0.0
    s.bankrupt_risk = state.cash < (3.0 * overhead + critical_pending)

    if len(memory.cash_hist) >= 4:
        deltas = [memory.cash_hist[i] - memory.cash_hist[i - 1] for i in range(1, len(memory.cash_hist))]
        recent3 = deltas[-3:] if len(deltas) >= 3 else deltas
        s.cash_3d_avg_delta = sum(recent3) / len(recent3) if recent3 else 0.0
        s.cash_trajectory_bad = s.cash_3d_avg_delta < -800.0

    # ── Demand vs DOW baseline ──
    s.baseline_covers_today = baseline_covers(memory, state.day_of_week)
    today_covers = state.total_covers_yesterday  # yesterday's was *yesterday's* DOW
    # But we want today's *expected* demand. Use today's DOW baseline.
    # The "surge" signal is based on yesterday's covers vs yesterday's baseline.
    from .constants import DOW_ORDER
    if state.day_of_week in DOW_ORDER:
        prev_idx = (DOW_ORDER.index(state.day_of_week) - 1) % 7
        prev_dow = DOW_ORDER[prev_idx]
        prev_baseline = baseline_covers(memory, prev_dow)
        if prev_baseline > 0:
            s.covers_today_vs_baseline = today_covers / prev_baseline

    # demand_surge fires when YESTERDAY's covers far exceeded YESTERDAY's baseline.
    # Use a stricter ratio in early-game (priors are noisy) and a relaxed one once
    # we have learned baselines.
    surge_threshold = 2.0 if state.day <= 4 else 1.4
    s.demand_surge = s.covers_today_vs_baseline > surge_threshold

    # demand_collapse: covers far below baseline AND not because of capacity
    s.capacity_bound = state.table_utilization_peak > 0.9
    s.demand_collapse = s.covers_today_vs_baseline < 0.6 and not s.capacity_bound and state.day > 1

    # Persistence checks via memory.covers_hist
    if len(memory.covers_hist) >= 3:
        last3 = memory.covers_hist[-3:]
        baseline_avg = s.baseline_covers_today
        if baseline_avg > 0:
            s.demand_surge_persistent = all(c > 1.3 * baseline_avg for c in last3)
            s.demand_collapse_persistent = all(c < 0.6 * baseline_avg for c in last3)

    # ── Kitchen bottleneck ──
    s.kitchen_bound = len(state.kitchen_bottleneck_hours) > 0
    if len(memory.last_walkouts) >= 3:
        # Proxy persistence: walkouts "Many" 2 of 3 last days
        many_count = sum(1 for w in memory.last_walkouts[-3:] if w == "M")
        s.kitchen_bound_persistent = many_count >= 2

    # ── Reputation ──
    if len(memory.rep_hist) >= 2:
        from .constants import REP_BY_LETTER
        cur_band = REP_BY_LETTER.get(memory.rep_hist[-1], "Very Good")
        prev_band = REP_BY_LETTER.get(memory.rep_hist[-2], cur_band)
        s.reputation_decline = REP_RANK.get(cur_band, 3) < REP_RANK.get(prev_band, 3)

        # 2-band shock within last 3 days (health_scare detector)
        if len(memory.rep_hist) >= 3:
            ranks = [REP_RANK.get(REP_BY_LETTER.get(l, "Very Good"), 3) for l in memory.rep_hist[-3:]]
            if max(ranks) - min(ranks) >= 2 and ranks[-1] < ranks[0]:
                s.reputation_shock = True

        # Recovery hysteresis: rep recovered within last 3 days
        if len(memory.rep_hist) >= 4:
            for i in range(len(memory.rep_hist) - 1, max(-1, len(memory.rep_hist) - 4), -1):
                cur = REP_RANK.get(REP_BY_LETTER.get(memory.rep_hist[i], "Very Good"), 3)
                prev = REP_RANK.get(REP_BY_LETTER.get(memory.rep_hist[i - 1], "Very Good"), 3) if i >= 1 else cur
                if cur > prev:
                    s.recovery_hysteresis_active = True
                    break

    # Defensive lock from memory has priority
    if memory.defensive_lock_days > 0:
        s.recovery_hysteresis_active = True

    # ── Alerts → keyword flags ──
    alerts_text = " ".join(state.alerts).lower()
    alerts_seen_text = " ".join(memory.alerts_seen).lower()

    s.crisis_warning = any(kw in alerts_text or kw in alerts_seen_text
                            for kw in (ALERT_KEYWORDS_CRISIS + ALERT_KEYWORDS_WARN + ALERT_KEYWORDS_BAN))
    s.renovation_active = any(kw in alerts_text or kw in alerts_seen_text
                              for kw in ALERT_KEYWORDS_RENOVATION)
    festival_alert = any(kw in alerts_text for kw in ALERT_KEYWORDS_FESTIVAL)
    if festival_alert:
        s.demand_surge = True

    scare_alert = any(kw in alerts_text or kw in alerts_seen_text for kw in ALERT_KEYWORDS_SCARE)
    if scare_alert or s.reputation_shock:
        # Force defensive lock for 5 days
        memory.defensive_lock_days = max(memory.defensive_lock_days, 5)
        s.reputation_shock = True

    # ── Inflation detection (supplier price drift vs day 1) ──
    if memory.init_cat:
        drift_ratios = []
        for ing, init_price in memory.init_cat.items():
            current_best = float("inf")
            for sup_name, _ in INGREDIENT_SUPPLIERS.get(ing, []):
                catalog_entry = state.supplier_catalog.get(sup_name)
                if catalog_entry:
                    p = catalog_entry.get("ingredients", {}).get(ing)
                    if p is not None and p < current_best:
                        current_best = float(p)
            if current_best < float("inf") and init_price > 0:
                drift_ratios.append(current_best / init_price)
        if drift_ratios:
            avg_drift = sum(drift_ratios) / len(drift_ratios)
            s.inflation_active = avg_drift > 1.08

    # Renovation back-up detection: high utilization + low covers persisting
    if state.table_utilization_peak >= 0.99 and state.total_covers_yesterday < 60 and len(memory.covers_hist) >= 2:
        prev = memory.covers_hist[-2] if len(memory.covers_hist) >= 2 else 100
        if prev < 60:
            s.renovation_active = True

    # ── Ghost review spike ──
    s.ghost_review_spike = memory.ghost_rate_7d > 0.35

    # ── Supplier fill-rate per ingredient ──
    min_fill = 1.0
    for sup_name, sup_code in SUPPLIER_CODE.items():
        fr = supplier_fill_rate(memory, sup_name)
        n = memory.sup_n.get(sup_code, 0)
        # Bayesian-ish: only trust low fill rates after some samples
        threshold = 0.5 if n >= 5 else (0.6 if n >= 2 else 0.4)
        if fr < threshold:
            # Mark all ingredients sold by this supplier as disrupted
            for ing in [i for i, suplist in INGREDIENT_SUPPLIERS.items() if any(s_ == sup_name for s_, _ in suplist)]:
                # Only mark if this is the ingredient's *cheapest* (active) supplier
                if INGREDIENT_SUPPLIERS[ing][0][0] == sup_name:
                    s.supply_disruption[ing] = True
        min_fill = min(min_fill, fr)
    s.fill_rate_min = min_fill

    # Emerging supply disruption: any supplier showing recent decline
    # (proxy: fill rate < 0.7 with n >= 3)
    for sup_code, fr in memory.sup_fill.items():
        if fr < 0.7 and memory.sup_n.get(sup_code, 0) >= 3:
            s.supply_disruption_emerging = True
            break

    # ── Inventory overstocking & waste risks ──
    for ing, item in state.inventory.items():
        # Daily demand approx for this ingredient
        if item.expiring_soon(2) > 0:
            # Crude: if > 6 kg expiring in 2 days, flag
            if item.expiring_soon(2) > 6.0:
                s.inventory_overstocked[ing] = True
        if ing == "Salmon" and item.expiring_soon(2) > 6.0:
            s.salmon_waste_risk = True

    # ── Pepperoni stockout risk (Wednesday cliff for Italian Imports) ──
    pep = state.inventory.get("Pepperoni")
    pep_pending = state.pending_by_ingredient.get("Pepperoni", 0)
    pep_total = (pep.total_kg if pep else 0) + pep_pending
    # 3-day demand estimate: roughly 0.07 kg/cover × DISH_MIX_PRIOR[Pepperoni]=0.13 × ~60 covers × 3d ≈ 1.6 kg
    if pep_total < 2.0 and state.day_of_week not in ("Sunday", "Monday", "Tuesday"):
        s.pepperoni_stockout_risk = True

    return s
