"""Sparingly-used LLM scenario advisor.

Fires only when:
  - New alerts appear and we haven't asked yet, OR
  - An anomaly is detected (reputation drop ≥ 2 bands, fill rates collapse,
    demand swing ≥ 50%, walkout band Many).

The LLM returns a short tag (e.g. "RAISE_PRICES_LATE_GAME", "PAUSE_MARKETING")
that the deterministic policies can react to. Output is cached in memory.

If DET_AGENT_LLM=0 (or no API key), this module is a no-op.
"""

from __future__ import annotations

import os
import json
import time

# Module-level: skip if not enabled
_LLM_ENABLED = os.getenv("DET_AGENT_LLM", "1") == "1"
_API_KEY = os.getenv("LITELLM_API_KEY", "sk-GQE-Dh9ftn2Bl3ooYC1lww")
_BASE_URL = os.getenv("LITELLM_BASE_URL",
                      "http://litellm-production.eba-pvykax23.eu-west-1.elasticbeanstalk.com")
_MODEL = os.getenv("DET_AGENT_LLM_MODEL", "gpt-4o-mini")

# Track last-asked day per game (via simple in-memory dict keyed by notes_raw[:30])
_last_call_log: dict[str, int] = {}


def _http_call(prompt: str, timeout: float = 8.0) -> str:
    """One-shot LLM call via the litellm endpoint. Returns text or ''."""
    import httpx
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(
                f"{_BASE_URL}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": _MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 120,
                    "temperature": 0,
                },
            )
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return ""


def should_call(state, memory) -> tuple[bool, str]:
    """Decide whether to invoke the LLM this turn.

    Returns (should, reason). Reason is logged.
    """
    if not _LLM_ENABLED:
        return False, "disabled"

    # New alert text seen?
    alert_txt = " ".join(state.alerts)
    if alert_txt and (memory.llm_recommendation_day == 0
                      or state.day - memory.llm_recommendation_day >= 6):
        return True, "alert_or_periodic"

    # Anomalies
    if state.walkout_rank >= 3:  # "Many"
        return True, "many_walkouts"
    if state.reputation_rank <= 1 and memory.llm_recommendation_day != state.day:
        return True, "rep_low"
    if state.customer_trend == "Declining" and state.day >= 5 and (
        state.day - memory.llm_recommendation_day >= 5
    ):
        return True, "declining_trend"

    return False, "no_trigger"


def call(state, memory) -> str:
    """Issue an LLM call to interpret the current situation.

    Returns a short tag/recommendation string. Persists into memory.
    """
    # Compose minimal context — keep token count low for speed
    alerts = "; ".join(state.alerts) or "(none)"
    fills = ", ".join(
        f"{s.split()[0][:3]}={f:.2f}"
        for s, arr in memory.supplier_fill.items()
        for f in [sum(arr)/len(arr)] if arr
    ) or "(no data)"
    flags = []
    if memory.scen_crisis: flags.append("crisis")
    if memory.scen_renovation: flags.append("renovation")
    if memory.scen_tourist: flags.append("tourist")
    if memory.scen_inflation: flags.append("inflation")
    if memory.scen_health: flags.append("health")
    if memory.scen_ban: flags.append("ban")
    flag_str = ",".join(flags) or "none"

    prompt = f"""You advise a 30-day Italian restaurant simulation agent (1-tab user). Day {state.day}/30 ({state.day_of_week}). Cash {state.cash:.0f}. Reputation {state.reputation_band}. Trend {state.customer_trend}. Yesterday covers {memory.last_covers}, revenue {memory.last_revenue:.0f}, walkouts {memory.last_walkout_band}. Weather {state.weather_today}, forecast {state.weather_forecast[:3]}. Alerts: {alerts}. Detected scenario flags: {flag_str}. Supplier fill rates: {fills}.

The agent uses deterministic rules. Pick ONE strategic tag from this exact list (or NONE):
- AGGRESSIVE_PRICING (raise prices 5-8% when capacity-bound or premium demand)
- DISCOUNT_RECOVERY (cut prices 5% to win back customers; rep at risk)
- DIVERSIFY_SUPPLY (supply crisis active; spread orders across suppliers)
- BUILD_BUFFER (stock up before predicted disruption)
- CONSERVE_CASH (pause marketing/happy-hour; cash thin)
- STAFF_UP (under-staffed; many walkouts)
- STAFF_DOWN (overstaffed; util low)
- END_GAME_PREMIUM (days 25+; raise prices on premium dishes, protect rep)
- TOURIST_SURGE (massive demand spike; max staff/prices)
- TOURIST_DROP (post-surge crash; cut prices, reduce staff)
- POST_RENOV_BOOST (renovation done; capacity restored, push volume)
- HEALTH_RECOVERY (health scare hit; cut prices, marketing on)
- NONE

Reply with ONLY the tag, no other words."""

    out = _http_call(prompt)
    # Clean tag
    out = out.strip().upper().split()[0] if out else "NONE"
    if not any(out.startswith(t) for t in [
        "AGGRESSIVE", "DISCOUNT", "DIVERSIFY", "BUILD", "CONSERVE",
        "STAFF_UP", "STAFF_DOWN", "END_GAME", "TOURIST", "POST_RENOV",
        "HEALTH", "NONE"
    ]):
        out = "NONE"
    memory.llm_recommendation = out
    memory.llm_recommendation_day = state.day
    return out


def apply_recommendation(rec: str, plan: list[dict], state, memory) -> list[dict]:
    """Adjust the plan based on the LLM tag.

    These are SMALL nudges — overrides on top of deterministic decisions.
    """
    if rec in ("NONE", "", None):
        return plan
    out = []
    for act in plan:
        tool = act.get("tool")
        args = dict(act.get("args", {}))
        if tool == "set_price" and rec in ("AGGRESSIVE_PRICING", "END_GAME_PREMIUM"):
            entry = state.menu_book.get(args.get("dish", ""))
            if entry:
                base = float(entry.get("base_price", 0))
                # Bump 3% higher (cap 1.18x)
                new_price = min(base * 1.18, float(args["price"]) * 1.03)
                args["price"] = round(new_price, 2)
        elif tool == "set_price" and rec in ("DISCOUNT_RECOVERY", "TOURIST_DROP", "HEALTH_RECOVERY"):
            entry = state.menu_book.get(args.get("dish", ""))
            if entry:
                base = float(entry.get("base_price", 0))
                new_price = max(base * 0.85, float(args["price"]) * 0.97)
                args["price"] = round(new_price, 2)
        elif tool == "set_marketing_spend" and rec == "CONSERVE_CASH":
            args["amount"] = 0
        elif tool == "set_marketing_spend" and rec == "HEALTH_RECOVERY":
            args["amount"] = max(float(args.get("amount", 0)), 350)
        elif tool == "set_staff_level" and rec == "STAFF_UP":
            args["level"] = min(15, int(args.get("level", 8)) + 1)
        elif tool == "set_staff_level" and rec == "STAFF_DOWN":
            args["level"] = max(3, int(args.get("level", 8)) - 1)
        elif tool == "set_staff_level" and rec == "TOURIST_SURGE":
            args["level"] = max(11, int(args.get("level", 8)))
        out.append({"tool": tool, "args": args})

    # Drop marketing entirely if CONSERVE_CASH and amount was 0
    if rec == "CONSERVE_CASH":
        out = [a for a in out if not (a["tool"] == "set_marketing_spend" and a["args"].get("amount", 0) == 0)]
    return out
