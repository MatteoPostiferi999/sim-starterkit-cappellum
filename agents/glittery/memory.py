"""Persistent state across turns, stored compactly in `save_notes` (≤4000 chars).

Single-letter keys, 1-decimal floats, last-7 array caps. Typical size ~700 chars.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

from .constants import (
    DOW_LETTER, DOW_BY_LETTER, REP_LETTER, REP_BY_LETTER, MODE_LETTER,
    WALKOUT_LETTER, SUPPLIER_CODE, SUPPLIER_BY_CODE, DOW_BASELINE_COVERS,
    FILL_RATE_PRIOR,
)

NOTES_HARD_CAP = 3800     # leave 200 char safety margin under 4000
HISTORY_LEN = 7
MEMORY_VERSION = 3


@dataclass
class Memory:
    # Version & last day we saved
    v: int = MEMORY_VERSION
    d: int = 0

    # Rolling per-DOW covers EMA + count of samples
    cov_by_dow: dict[str, float] = field(default_factory=lambda: {ltr: DOW_BASELINE_COVERS[d] for d, ltr in DOW_LETTER.items()})
    cov_n: dict[str, int] = field(default_factory=lambda: {ltr: 0 for ltr in DOW_LETTER.values()})

    # Per-supplier rolling fill rate (Bayesian: starts at prior)
    sup_fill: dict[str, float] = field(default_factory=lambda: {SUPPLIER_CODE[n]: r for n, r in FILL_RATE_PRIOR.items()})
    sup_n: dict[str, int] = field(default_factory=lambda: {c: 0 for c in SUPPLIER_CODE.values()})

    # Histories (rolling, capped at HISTORY_LEN)
    rep_hist: list[str] = field(default_factory=list)        # reputation letters
    mode_hist: list[str] = field(default_factory=list)       # mode letters
    last_walkouts: list[str] = field(default_factory=list)   # walkout band letters
    cash_hist: list[float] = field(default_factory=list)     # last-7 cash levels
    covers_hist: list[int] = field(default_factory=list)     # last-7 daily covers
    rev_hist: list[float] = field(default_factory=list)      # last-7 daily revenue

    # Counters since-last-event
    since_hh: int = 99
    since_special: int = 99
    since_marketing: int = 99

    # Scenario inference (sticky once set)
    scen_flags: dict[str, int] = field(default_factory=lambda: {"reno": 0, "surge": 0, "crisis": 0, "infl": 0, "scare": 0})

    # Day-1 supplier catalog snapshot (for inflation detection)
    init_cat: dict[str, float] = field(default_factory=dict)  # ingredient -> cheapest price observed day 1

    # Day-1 starting inventory total kg
    init_inv_kg: float = 0.0

    # Counter of LLM consecutive failures
    consec_llm_fail: int = 0

    # Alert keywords seen (set of strings, kept compact)
    alerts_seen: list[str] = field(default_factory=list)

    # Ghost-review rolling rate
    ghost_rate_7d: float = 0.25

    # Mode locks (count-down)
    defensive_lock_days: int = 0


# ─────────────────────── helpers: encoding / decoding ─────────────────────────

def dump(mem: Memory) -> str:
    """Serialize to compact JSON, dropping defaults where possible."""
    d = asdict(mem)

    # round floats
    def _r(x):
        if isinstance(x, float):
            return round(x, 2)
        return x

    def _r_dict(dd):
        return {k: _r(v) for k, v in dd.items()}

    d["cov_by_dow"] = _r_dict(d["cov_by_dow"])
    d["sup_fill"] = _r_dict(d["sup_fill"])
    d["cash_hist"] = [round(v, 0) for v in d["cash_hist"]]
    d["rev_hist"] = [round(v, 0) for v in d["rev_hist"]]
    d["ghost_rate_7d"] = round(d["ghost_rate_7d"], 2)
    d["init_cat"] = _r_dict(d["init_cat"])
    d["init_inv_kg"] = round(d["init_inv_kg"], 1)

    text = json.dumps(d, separators=(",", ":"))

    # Truncate if over cap (drop history arrays one by one)
    if len(text) > NOTES_HARD_CAP:
        for drop_key in ("rev_hist", "cash_hist", "covers_hist", "alerts_seen", "last_walkouts"):
            d[drop_key] = d[drop_key][-3:] if isinstance(d.get(drop_key), list) else d[drop_key]
            text = json.dumps(d, separators=(",", ":"))
            if len(text) <= NOTES_HARD_CAP:
                break
    return text[:NOTES_HARD_CAP]


def load(notes_str: str) -> Memory:
    """Parse memory from notes string. Defensive — returns fresh Memory on any error."""
    if not notes_str or not notes_str.strip():
        return Memory()
    try:
        d = json.loads(notes_str)
        # Build Memory and overlay known keys defensively
        m = Memory()
        for k, v in d.items():
            if hasattr(m, k):
                setattr(m, k, v)
        # Sanity: ensure required dicts exist (in case of version mismatch)
        if not m.cov_by_dow:
            m.cov_by_dow = {ltr: DOW_BASELINE_COVERS[dn] for dn, ltr in DOW_LETTER.items()}
        if not m.sup_fill:
            m.sup_fill = {SUPPLIER_CODE[n]: r for n, r in FILL_RATE_PRIOR.items()}
        return m
    except Exception:
        return Memory()


# ─────────────────────── update: roll forward by one day ──────────────────────

def update(mem: Memory, state, sig=None) -> Memory:
    """Update memory using yesterday's results (state.service holds them).

    Called once per turn after parse(). Idempotent at the day level: if mem.d == state.day
    already, this becomes a no-op (we already updated for this day).
    """
    if mem.d >= state.day:
        return mem

    # 1. Roll per-DOW covers EMA (using *yesterday's* covers under yesterday's DOW)
    # Yesterday's DOW is one before today
    prev_dow = _prev_dow(state.day_of_week)
    prev_dow_ltr = DOW_LETTER[prev_dow]
    if state.total_covers_yesterday > 0 or state.day > 1:
        cov = state.total_covers_yesterday
        n = mem.cov_n.get(prev_dow_ltr, 0)
        old = mem.cov_by_dow.get(prev_dow_ltr, DOW_BASELINE_COVERS[prev_dow])
        # Bayesian update: blend prior with new sample
        alpha = 1.0 / (n + 2)  # n=0 → 0.5, n=1 → 0.33, n=2 → 0.25
        new = (1 - alpha) * old + alpha * cov
        mem.cov_by_dow[prev_dow_ltr] = round(new, 1)
        mem.cov_n[prev_dow_ltr] = n + 1

    # 2. Roll per-supplier fill rate from delivery_history
    # delivery_history is the last ~14 days of completed deliveries (server-truncated).
    # Field is `delivery_day` (the day delivered). Use a 14-day window for stability.
    recent_cutoff = max(1, state.day - 14)
    per_sup_records: dict[str, list[tuple[float, float]]] = {}
    for dh in state.delivery_history:
        if dh.get("delivery_day", state.day) < recent_cutoff:
            continue
        sup = dh.get("supplier")
        ordered = float(dh.get("ordered_kg", 0))
        delivered = float(dh.get("delivered_kg", 0))
        if not sup or ordered <= 0:
            continue
        per_sup_records.setdefault(sup, []).append((ordered, delivered))
    for sup, recs in per_sup_records.items():
        code = SUPPLIER_CODE.get(sup)
        if not code:
            continue
        fill = sum(d / o for o, d in recs) / len(recs)
        mem.sup_fill[code] = round(fill, 3)
        mem.sup_n[code] = len(recs)

    # 3. Reputation history (append today's band — letter form)
    rep_ltr = REP_LETTER.get(state.reputation_band, "VG")
    mem.rep_hist = (mem.rep_hist + [rep_ltr])[-HISTORY_LEN:]

    # 4. Last walkout
    wo_ltr = WALKOUT_LETTER.get(state.walkout_band, "N")
    mem.last_walkouts = (mem.last_walkouts + [wo_ltr])[-HISTORY_LEN:]

    # 5. Cash & covers history
    mem.cash_hist = (mem.cash_hist + [state.cash])[-HISTORY_LEN:]
    mem.covers_hist = (mem.covers_hist + [state.total_covers_yesterday])[-HISTORY_LEN:]
    mem.rev_hist = (mem.rev_hist + [state.yesterday_revenue])[-HISTORY_LEN:]

    # 6. Snapshot day-1 catalog (for inflation detection)
    if state.day == 1 and not mem.init_cat:
        for ing, suppliers in {}.items():
            pass
        # Lowest price per ingredient observed today
        from .constants import INGREDIENT_SUPPLIERS
        init_cat = {}
        for ing in INGREDIENT_SUPPLIERS:
            best = float("inf")
            for sup_name, _price in INGREDIENT_SUPPLIERS[ing]:
                catalog_entry = state.supplier_catalog.get(sup_name)
                if catalog_entry:
                    p = catalog_entry.get("ingredients", {}).get(ing)
                    if p is not None and p < best:
                        best = float(p)
            if best < float("inf"):
                init_cat[ing] = round(best, 2)
        mem.init_cat = init_cat
        # Snapshot starting inventory
        mem.init_inv_kg = sum(item.total_kg for item in state.inventory.values())

    # 7. Ghost review rolling rate
    if state.recent_reviews:
        # Count reviews from last 14 days where stars < 1 (ghost reviews)
        cutoff = max(1, state.day - 14)
        recent = [r for r in state.recent_reviews if r.get("day_posted", 0) >= cutoff]
        if recent:
            ghost = sum(1 for r in recent if float(r.get("stars", 0)) < 1.0)
            mem.ghost_rate_7d = round(ghost / len(recent), 3)

    # 8. Alert keywords seen (compact: just first 30 chars)
    for alert in state.alerts:
        key = alert[:40].lower()
        if key not in mem.alerts_seen:
            mem.alerts_seen = (mem.alerts_seen + [key])[-10:]

    # 9. Decrement defensive lock if active
    if mem.defensive_lock_days > 0:
        mem.defensive_lock_days -= 1

    mem.d = state.day
    return mem


def _prev_dow(dow: str) -> str:
    """Return the day-of-week one day before `dow`."""
    from .constants import DOW_ORDER
    if dow not in DOW_ORDER:
        return "Monday"
    i = DOW_ORDER.index(dow)
    return DOW_ORDER[(i - 1) % 7]


def baseline_covers(mem: Memory, dow: str) -> float:
    """Resolve expected covers for a DOW, using prior + Bayesian update."""
    ltr = DOW_LETTER.get(dow, "M")
    return mem.cov_by_dow.get(ltr, DOW_BASELINE_COVERS.get(dow, 50.0))


def supplier_fill_rate(mem: Memory, supplier_name: str) -> float:
    """Resolve fill rate prior for a supplier."""
    code = SUPPLIER_CODE.get(supplier_name, "")
    return mem.sup_fill.get(code, FILL_RATE_PRIOR.get(supplier_name, 0.8))
