"""Compact key:value memory persisted via save_notes.

Format keeps total length under 3500 chars even after 30 days of accumulation.
Compact letters: M/T/W/R/F/S/U for weekdays. P/F/G/V/E for rep bands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from .constants import DOW_LETTER, FILL_RATE_PRIOR


@dataclass
class Memory:
    # Per-DOW running average of observed covers
    dow_covers: dict[str, list[int]] = field(default_factory=dict)
    # Per-supplier observed fill rate (Bayesian: starts from prior)
    supplier_fill: dict[str, list[float]] = field(default_factory=dict)
    # Per-dish price elasticity log: dish → list of {d, m (mult), cov, rev}
    price_log: dict[str, list[dict]] = field(default_factory=dict)
    # Last 10 stockout events
    stockouts: list[dict] = field(default_factory=list)
    # Last happy hour day + streak
    last_hh_day: int = -10
    hh_streak: int = 0
    last_marketing_day: int = -10
    # Last applied price multiplier per dish (for next-day elasticity comparison)
    last_price_mult: dict[str, float] = field(default_factory=dict)
    # Last covers value to detect demand swings
    last_covers: int = 0
    last_revenue: float = 0.0
    last_walkout_band: str = "None"
    # Scenario flags (1 = active, persists)
    scen_crisis: int = 0
    scen_renovation: int = 0
    scen_tourist: int = 0
    scen_inflation: int = 0
    scen_health: int = 0
    scen_ban: int = 0
    # Day the renovation alert fired
    renov_start: int = 0
    # LLM advisor flags (set by llm_advisor.py)
    llm_recommendation: str = ""
    llm_recommendation_day: int = 0


def dow_avg(mem: Memory, dow: str) -> float | None:
    arr = mem.dow_covers.get(dow)
    if not arr:
        return None
    return sum(arr) / len(arr)


def supplier_fill_estimate(mem: Memory, supplier: str) -> float:
    arr = mem.supplier_fill.get(supplier, [])
    prior = FILL_RATE_PRIOR.get(supplier, 0.75)
    if not arr:
        return prior
    # Weighted average: prior counts as 3 observations
    return (sum(arr) + prior * 3) / (len(arr) + 3)


def update(mem: Memory, state) -> Memory:
    """Update memory using yesterday's observed result + alerts."""
    # Track DOW covers
    if state.day > 1 and state.total_covers_yesterday > 0:
        # day-1 was yesterday's DOW; reconstruct
        prev_dow_idx = (state.day - 2) % 7
        from .constants import DOW_ORDER
        prev_dow = DOW_ORDER[prev_dow_idx]
        arr = mem.dow_covers.setdefault(prev_dow, [])
        arr.append(state.total_covers_yesterday)
        # Cap at 5 obs/day to avoid notes bloat
        if len(arr) > 5:
            arr.pop(0)

    # Track supplier fill rates from delivery_history
    for d in state.delivery_history:
        if d.get("delivery_day") == state.day - 1:
            sup = d.get("supplier", "")
            ordered = float(d.get("ordered_kg", 0) or 0)
            delivered = float(d.get("delivered_kg", 0) or 0)
            if ordered > 0 and sup:
                fr = min(1.0, delivered / ordered)
                arr = mem.supplier_fill.setdefault(sup, [])
                arr.append(fr)
                if len(arr) > 6:
                    arr.pop(0)

    # Stockouts from yesterday
    su = state.service_summary.get("dishes_unavailable_at", {}) or {}
    for dish in su.keys():
        mem.stockouts.append({"d": state.day - 1, "dish": dish})
    if len(mem.stockouts) > 10:
        mem.stockouts = mem.stockouts[-10:]

    # Update covers/revenue tracking for anomaly detection
    mem.last_covers = state.total_covers_yesterday
    mem.last_revenue = state.yesterday_revenue
    mem.last_walkout_band = state.walkout_band

    # Scenario detection from alerts
    alerts_txt = " ".join(state.alerts).lower()
    from .constants import (ALERT_CRISIS, ALERT_RENOVATION, ALERT_TOURIST,
                            ALERT_INFLATION, ALERT_HEALTH, ALERT_BAN)
    if any(k in alerts_txt for k in ALERT_CRISIS):
        mem.scen_crisis = 1
    if any(k in alerts_txt for k in ALERT_RENOVATION):
        if mem.scen_renovation == 0:
            mem.renov_start = state.day
        mem.scen_renovation = 1
    if any(k in alerts_txt for k in ALERT_TOURIST):
        mem.scen_tourist = 1
    if any(k in alerts_txt for k in ALERT_INFLATION):
        mem.scen_inflation = 1
    if any(k in alerts_txt for k in ALERT_HEALTH):
        mem.scen_health = 1
    if any(k in alerts_txt for k in ALERT_BAN):
        mem.scen_ban = 1

    return mem


def price_response_log(mem: Memory, dish: str, day: int, mult: float, covers: int, revenue: float) -> None:
    arr = mem.price_log.setdefault(dish, [])
    arr.append({"d": day, "m": round(mult, 2), "cov": covers, "rev": round(revenue, 0)})
    if len(arr) > 6:
        arr.pop(0)


def dump(mem: Memory) -> str:
    """Compact key:value serialization."""
    lines = []
    # DOW averages
    if mem.dow_covers:
        parts = []
        for dow, arr in mem.dow_covers.items():
            if arr:
                avg = sum(arr) / len(arr)
                parts.append(f"{DOW_LETTER[dow]}{avg:.0f}")
        if parts:
            lines.append("dow:" + ",".join(parts))
    # Supplier fill
    if mem.supplier_fill:
        parts = []
        for sup, arr in mem.supplier_fill.items():
            if arr:
                fr = sum(arr) / len(arr)
                code = sup.split()[0][:3].upper()
                parts.append(f"{code}{fr:.2f}")
        if parts:
            lines.append("fill:" + ",".join(parts))
    # HH / mkt
    lines.append(f"hh:{mem.last_hh_day}:s{mem.hh_streak}|mkt:{mem.last_marketing_day}")
    # Scenario flags
    scen_flags = []
    if mem.scen_crisis: scen_flags.append("crisis")
    if mem.scen_renovation: scen_flags.append(f"renov{mem.renov_start}")
    if mem.scen_tourist: scen_flags.append("tour")
    if mem.scen_inflation: scen_flags.append("inflt")
    if mem.scen_health: scen_flags.append("health")
    if mem.scen_ban: scen_flags.append("ban")
    if scen_flags:
        lines.append("scen:" + ",".join(scen_flags))
    # Stockouts
    if mem.stockouts:
        parts = [f"d{s['d']}:{s['dish'][:4]}" for s in mem.stockouts[-5:]]
        lines.append("so:" + ",".join(parts))
    # Last metrics
    lines.append(f"last:cov{mem.last_covers}|rev{mem.last_revenue:.0f}|w{mem.last_walkout_band[0]}")
    # LLM rec
    if mem.llm_recommendation:
        lines.append(f"llm:d{mem.llm_recommendation_day}:{mem.llm_recommendation[:140]}")
    # Price log (last 4 entries per dish, max 4 dishes)
    pl_parts = []
    for dish, log in list(mem.price_log.items())[:4]:
        if log:
            recent = log[-3:]
            d3 = "/".join(f"d{e['d']}m{e['m']}c{e['cov']}" for e in recent)
            pl_parts.append(f"{dish[:5]}={d3}")
    if pl_parts:
        lines.append("pr:" + "|".join(pl_parts))

    text = "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800]
    return text


def load(notes: str) -> Memory:
    """Parse notes back into Memory. Best-effort, never raises."""
    mem = Memory()
    if not notes:
        return mem
    from .constants import DOW_ORDER
    dow_by_letter = {v: k for k, v in DOW_LETTER.items()}

    for line in notes.splitlines():
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        try:
            if key == "dow":
                for tok in val.split(","):
                    if len(tok) >= 2:
                        letter = tok[0]
                        n = float(tok[1:])
                        d = dow_by_letter.get(letter)
                        if d:
                            mem.dow_covers[d] = [int(n)]
            elif key == "fill":
                for tok in val.split(","):
                    if len(tok) >= 4:
                        code = tok[:3]
                        fr = float(tok[3:])
                        # Resolve code → name (best match)
                        for sup_name in FILL_RATE_PRIOR.keys():
                            if sup_name.split()[0][:3].upper() == code:
                                mem.supplier_fill[sup_name] = [fr]
                                break
            elif key == "hh":
                # hh:last:sN
                parts = val.split("|")
                if parts:
                    a, _, b = parts[0].partition(":")
                    mem.last_hh_day = int(a)
                    if b.startswith("s"):
                        mem.hh_streak = int(b[1:])
                if len(parts) > 1 and parts[1].startswith("mkt:"):
                    mem.last_marketing_day = int(parts[1].split(":")[1])
            elif key == "scen":
                for tok in val.split(","):
                    if tok == "crisis":
                        mem.scen_crisis = 1
                    elif tok.startswith("renov"):
                        mem.scen_renovation = 1
                        try:
                            mem.renov_start = int(tok[5:])
                        except ValueError:
                            mem.renov_start = 1
                    elif tok == "tour":
                        mem.scen_tourist = 1
                    elif tok == "inflt":
                        mem.scen_inflation = 1
                    elif tok == "health":
                        mem.scen_health = 1
                    elif tok == "ban":
                        mem.scen_ban = 1
            elif key == "last":
                # last:covN|revN|wF
                for p in val.split("|"):
                    if p.startswith("cov"):
                        mem.last_covers = int(p[3:])
                    elif p.startswith("rev"):
                        mem.last_revenue = float(p[3:])
                    elif p.startswith("w"):
                        from .constants import WALKOUT_BANDS
                        first = p[1]
                        for wb in WALKOUT_BANDS:
                            if wb[0] == first:
                                mem.last_walkout_band = wb
                                break
            elif key == "llm":
                # llm:dN:text
                a, _, b = val.partition(":")
                try:
                    mem.llm_recommendation_day = int(a.lstrip("d"))
                except ValueError:
                    pass
                mem.llm_recommendation = b
        except (ValueError, IndexError):
            continue
    return mem
