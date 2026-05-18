"""det_agent main strategy entry point.

Per-turn pipeline:
  1. Parse observation → State
  2. Load + update Memory (DOW averages, supplier fill, scenario flags)
  3. Run deterministic predictors (inventory_future, cash_runway)
  4. (Optional) consult LLM for scenario interpretation
  5. Plan menu, staff, pricing, orders, marketing, happy hour, daily special
  6. Apply LLM nudges (if any)
  7. Safety validate
  8. Persist memory via save_notes
"""

from __future__ import annotations

from . import state as state_mod
from . import memory as memory_mod
from . import predict
from . import decide_orders
from . import decide_staff
from . import decide_pricing
from . import decide_promo
from . import decide_menu
from . import safety
from . import llm_advisor


def strategy(observation: dict, day: int) -> list[dict]:
    # 1-2: parse + memory
    state = state_mod.parse(observation, day)
    memory = memory_mod.load(state.notes_raw)
    memory = memory_mod.update(memory, state)

    # 3: predictors
    de = predict.demand_estimate(state, memory)
    inv_future = predict.inventory_future(state, memory, n_days=7)
    expected_rev = de["expected"] * 18.0   # rough avg-cover price baseline
    runway = predict.cash_runway(state, expected_rev)

    # 4: LLM advisor (optional, sparing)
    rec = ""
    should, reason = llm_advisor.should_call(state, memory)
    if should:
        rec = llm_advisor.call(state, memory)

    # 5: build plan
    plan: list[dict] = []

    # Menu first (drives capacity-aware orders + pricing)
    menu = decide_menu.plan_menu(state, memory)
    plan.append({"tool": "set_menu", "args": {"dishes": menu}})

    # Staff
    staff = decide_staff.plan_staff(state, memory)
    plan.append({"tool": "set_staff_level", "args": {"level": staff}})

    # Update state.active_menu to match planned menu so pricing knows what to set
    state.active_menu = menu

    # Pricing (per-dish)
    prices = decide_pricing.plan_prices(state, memory)
    for dish, price in prices.items():
        plan.append({"tool": "set_price", "args": {"dish": dish, "price": price}})

    # Orders
    orders = decide_orders.plan_orders(state, memory, inv_future, runway)
    plan.extend(orders)

    # Marketing
    mkt = decide_promo.decide_marketing(state, memory)
    if mkt > 0:
        plan.append({"tool": "set_marketing_spend", "args": {"amount": mkt}})

    # Happy hour
    if decide_promo.decide_happy_hour(state, memory):
        plan.append({"tool": "run_happy_hour", "args": {}})
        # Update streak/last day in memory
        if state.day - memory.last_hh_day == 1:
            memory.hh_streak += 1
        else:
            memory.hh_streak = 1
        memory.last_hh_day = state.day
    else:
        if state.day - memory.last_hh_day > 1:
            memory.hh_streak = 0

    # Daily special
    special = decide_promo.decide_daily_special(state, memory)
    if special:
        plan.append({"tool": "offer_daily_special", "args": {"dish": special}})

    if mkt > 0:
        memory.last_marketing_day = state.day

    # 6: LLM nudges
    if rec and rec != "NONE":
        plan = llm_advisor.apply_recommendation(rec, plan, state, memory)

    # 7: safety
    plan = safety.validate(plan, state)

    # 8: save_notes (always last)
    plan.append({"tool": "save_notes", "args": {"text": memory_mod.dump(memory)}})

    return plan
