"""Digital twin of the restaurant — predicts one day's outcome given (state, action).

Pure Python, no LLM. Parameters calibrated from the 12 recon games. Not perfect
prediction — directionally correct, fast (~50µs per call), and good enough to
RANK candidate action sets, which is all MPC needs.

Parameters and equations sourced from:
  - recon_data/SUMMARY.md (DOW demand priors, supplier fill rates)
  - recon_data/games/baseline_seed*/observations.jsonl (calibration: avg €18.30/cover)
  - AGENT_CONTRACT.md (scoring: walkout linear, rep/sat quadratic)
  - STRATEGY_GUIDE.md (price elasticity asymmetric; happy hour decay; weather drives demand)
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── Constants calibrated from recon ───────────────────────────────────────────

# True daily demand (covers wanted) per DOW under neutral weather, base prices,
# Very Good reputation, no marketing, no happy hour, no scenario events.
# Derived from cases in recon where walkout=None (served = demand).
DOW_TRUE_DEMAND: dict[str, float] = {
    "Monday":    80.0,
    "Tuesday":   95.0,
    "Wednesday": 95.0,
    "Thursday": 100.0,
    "Friday":   115.0,
    "Saturday": 140.0,   # heavily kitchen-bound in recon; true demand far above served
    "Sunday":    35.0,
}

# Weather effect on demand (multiplicative). From recon Monday: sunny 96/rainy 53.
WEATHER_MULT: dict[str, float] = {
    "sunny": 1.12, "cloudy": 1.00, "rainy": 0.70, "stormy": 0.45,
}

# Reputation impact on demand (regulars churn under bad rep).
REP_MULT: dict[int, float] = {0: 0.65, 1: 0.82, 2: 0.93, 3: 1.00, 4: 1.08}

# Customer trend signal
TREND_MULT: dict[str, float] = {"Declining": 0.88, "Stable": 1.00, "Growing": 1.08}

# Price elasticity (ASYMMETRIC per strategy guide: increases hurt more than cuts help).
# Returns demand multiplier given price multiplier.
def price_elasticity(price_mult: float) -> float:
    if price_mult >= 1.0:
        # Upward: elasticity coefficient -1.4 (10% price rise → 14% demand drop)
        return max(0.50, 1.0 - (price_mult - 1.0) * 1.4)
    else:
        # Downward: more elastic but capped (10% cut → 12% demand rise)
        return min(1.20, 1.0 + (1.0 - price_mult) * 1.2)


# Marketing diminishing returns (covers boost from EUR spend)
def marketing_lift(amount: float) -> float:
    if amount <= 0:
        return 1.0
    # Saturating: 0→1.0, 100→1.035, 200→1.055, 350→1.07, 500→1.075
    return 1.0 + 0.08 * (1 - 2.71828 ** (-amount / 250.0))


# Happy hour boost — decays with consecutive runs (per strategy guide)
def happy_hour_lift(active: bool, consecutive_days: int) -> float:
    if not active:
        return 1.0
    if consecutive_days == 0:
        return 1.18
    elif consecutive_days == 1:
        return 1.12
    elif consecutive_days == 2:
        return 1.07
    else:
        return 1.02   # decayed


# Kitchen throughput: max covers per staff per day. Recon: staff=6 served ~75 = 12.5/staff
KITCHEN_THROUGHPUT_PER_STAFF = 11.0

# Table capacity: 22 tables × ~5 turnovers in 11-hour service ≈ 110 max covers/day
TABLE_CAPACITY_NORMAL = 115.0
TABLE_CAPACITY_RENOVATION = 60.0   # half tables during renovation

# Revenue / cost calibration
AVG_BASE_PRICE_PER_COVER = 18.30   # measured directly from recon
AVG_INGREDIENT_COST_PER_COVER = 2.45   # derived from RECIPES × supplier prices
OVERHEAD_PER_DAY = 300.0
STAFF_COST_DAY = 120.0

# Scoring constants — calibrated against recon's penalty totals
WALKOUT_PENALTY_PER_WALKOUT = 3.0
REP_PENALTY_SCALE = 18.0  # per day, scales with (2 - rep_rank)^2 below "Good"
SAT_PENALTY_SCALE = 25.0  # per walkout-band severity step below "Few"

# Walkout band → estimated walkout count midpoint (for scoring)
WALKOUT_BAND_COUNT = {"None": 0, "Few": 3, "Some": 12, "Many": 35}

# Discount factor for future days in MPC
GAMMA = 0.92


# ── Simulator data classes ────────────────────────────────────────────────────

@dataclass
class SimState:
    """Compact snapshot of restaurant state used by the simulator."""
    day: int
    day_of_week: str
    weather_today: str
    weather_forecast: list[str]   # next 3 days
    cash: float
    reputation_rank: int          # 0=Poor .. 4=Excellent
    customer_trend: str
    staff_level: int              # yesterday's staff
    inventory_kg_estimate: float  # rough total inventory kg
    consecutive_hh_days: int
    days_since_special: int
    scenario_flags: dict          # {"renovation":1, "tourist_surge":0, ...}
    active_dish_count: int        # menu size (≥5)
    is_capacity_bound: bool       # from yesterday's signal


@dataclass
class SimAction:
    """A candidate action set for one day."""
    staff: int
    price_mult: float
    marketing: int
    happy_hour: bool
    has_daily_special: bool

    def key(self) -> tuple:
        return (self.staff, self.price_mult, self.marketing, self.happy_hour, self.has_daily_special)


@dataclass
class SimResult:
    """Predicted outcome of running an action for one day."""
    demand: float
    served: float
    walkouts: float
    revenue: float
    cost: float
    profit: float
    walkout_pen: float
    rep_pen: float
    sat_pen: float
    score_delta: float
    new_rep_rank: int


# ── Core prediction ───────────────────────────────────────────────────────────

def predict_day(state: SimState, action: SimAction) -> SimResult:
    """Predict the outcome of one day given current state and chosen action."""

    # ── 1. Demand prediction ──
    dow_base = DOW_TRUE_DEMAND.get(state.day_of_week, 80.0)
    weather_m = WEATHER_MULT.get(state.weather_today, 1.0)
    rep_m = REP_MULT.get(state.reputation_rank, 1.0)
    trend_m = TREND_MULT.get(state.customer_trend, 1.0)
    elasticity_m = price_elasticity(action.price_mult)
    marketing_m = marketing_lift(action.marketing)
    hh_m = happy_hour_lift(action.happy_hour, state.consecutive_hh_days)

    # Daily special is free upside per strategy guide ("Daily specials do MORE...")
    special_m = 1.03 if action.has_daily_special else 1.0

    # Menu variety effect (per strategy guide)
    variety_m = 1.0 if state.active_dish_count >= 7 else 0.95

    # Scenario modulations
    scen_m = 1.0
    if state.scenario_flags.get("tourist_surge"):
        scen_m *= 1.6
    if state.scenario_flags.get("crisis"):
        scen_m *= 0.95  # mild demand dampening from cost/disruption news
    # NOTE: renovation does NOT reduce demand — customers still come, they just
    # walk out because half the tables are unavailable. The capacity cap below
    # already models this. Adding a demand multiplier here over-corrects.

    demand = dow_base * weather_m * rep_m * trend_m * elasticity_m * marketing_m * hh_m * special_m * variety_m * scen_m

    # ── 2. Capacity ──
    table_cap = TABLE_CAPACITY_NORMAL
    if state.scenario_flags.get("renovation") or state.scenario_flags.get("reno"):
        table_cap = TABLE_CAPACITY_RENOVATION
    kitchen_cap = action.staff * KITCHEN_THROUGHPUT_PER_STAFF
    # Inventory cap — rough estimate (if inventory low, throughput limited)
    inv_cap = max(15.0, state.inventory_kg_estimate * 1.5) if state.inventory_kg_estimate > 0 else float("inf")
    capacity = min(table_cap, kitchen_cap, inv_cap)

    # ── 3. Served and walkouts ──
    served = min(demand, capacity)
    walkouts = max(0.0, demand - served)

    # ── 4. Revenue ──
    # Effective price = base × multiplier × (1 - happy_hour_discount)
    hh_discount = 0.10 if action.happy_hour else 0.0
    effective_price = AVG_BASE_PRICE_PER_COVER * action.price_mult * (1 - hh_discount)
    revenue = served * effective_price

    # ── 5. Costs ──
    ingredient_cost = served * AVG_INGREDIENT_COST_PER_COVER
    staff_cost = action.staff * STAFF_COST_DAY
    marketing_cost = action.marketing
    cost = OVERHEAD_PER_DAY + staff_cost + ingredient_cost + marketing_cost
    profit = revenue - cost

    # ── 6. Reputation transition ──
    # Walkouts and low quality drive rep down; smooth service drives rep up.
    # Asymmetric per strategy guide: bad weighs more than good, but moderate
    # walkouts should not panic the planner into starvation prices.
    walkout_ratio = walkouts / max(1.0, demand)
    if walkout_ratio > 0.30:
        rep_pressure = -1.0
    elif walkout_ratio > 0.15:
        rep_pressure = -0.5
    elif walkout_ratio > 0.08:
        rep_pressure = -0.15
    elif walkout_ratio < 0.04 and action.has_daily_special:
        rep_pressure = +0.30
    else:
        rep_pressure = 0.0

    new_rep_float = state.reputation_rank + rep_pressure
    new_rep_rank = max(0, min(4, round(new_rep_float)))

    # ── 7. Penalties ──
    walkout_pen = walkouts * WALKOUT_PENALTY_PER_WALKOUT
    # Reputation penalty is quadratic below "Good" (rank 2)
    rep_gap = max(0, 2 - new_rep_rank)
    rep_pen = (rep_gap ** 2) * REP_PENALTY_SCALE
    # Satisfaction penalty proxied by walkout band
    if walkouts > 20:
        sat_pen = SAT_PENALTY_SCALE * 4
    elif walkouts > 10:
        sat_pen = SAT_PENALTY_SCALE * 1.5
    elif walkouts > 3:
        sat_pen = SAT_PENALTY_SCALE * 0.5
    else:
        sat_pen = 0.0

    score_delta = profit - walkout_pen - rep_pen - sat_pen

    return SimResult(
        demand=demand, served=served, walkouts=walkouts,
        revenue=revenue, cost=cost, profit=profit,
        walkout_pen=walkout_pen, rep_pen=rep_pen, sat_pen=sat_pen,
        score_delta=score_delta, new_rep_rank=new_rep_rank,
    )


# ── Forward simulation: roll state forward across N days ──────────────────────

def simulate_horizon(initial_state: SimState, first_action: SimAction,
                      default_action_fn, horizon: int = 3) -> float:
    """Run an N-day forward simulation; return total discounted projected score.

    Day 0 uses `first_action`; days 1..N use `default_action_fn(state)`.
    """
    state = initial_state
    total = 0.0
    discount = 1.0

    for d in range(horizon):
        action = first_action if d == 0 else default_action_fn(state)
        result = predict_day(state, action)
        total += discount * result.score_delta
        discount *= GAMMA

        # Roll state forward
        next_dow = _next_dow(state.day_of_week)
        next_weather = state.weather_forecast[0] if state.weather_forecast else state.weather_today
        new_forecast = state.weather_forecast[1:] + [state.weather_forecast[-1]] if state.weather_forecast else []

        # Update consecutive happy hour
        new_hh_days = state.consecutive_hh_days + 1 if action.happy_hour else 0

        # Estimate inventory consumption
        inv_consumed = result.served * 0.5  # rough avg kg/cover
        new_inv = max(0.0, state.inventory_kg_estimate - inv_consumed)

        # Cash update
        new_cash = state.cash + result.profit

        # Customer trend evolves based on rep change
        new_trend = state.customer_trend
        if result.new_rep_rank < state.reputation_rank:
            new_trend = "Declining"
        elif result.new_rep_rank > state.reputation_rank:
            new_trend = "Growing"

        state = SimState(
            day=state.day + 1,
            day_of_week=next_dow,
            weather_today=next_weather,
            weather_forecast=new_forecast,
            cash=new_cash,
            reputation_rank=result.new_rep_rank,
            customer_trend=new_trend,
            staff_level=action.staff,
            inventory_kg_estimate=new_inv,
            consecutive_hh_days=new_hh_days,
            days_since_special=0 if action.has_daily_special else state.days_since_special + 1,
            scenario_flags=state.scenario_flags,
            active_dish_count=state.active_dish_count,
            is_capacity_bound=result.walkouts > 0 and result.served >= TABLE_CAPACITY_NORMAL * 0.9,
        )

    return total


# ── Helpers ───────────────────────────────────────────────────────────────────

_DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _next_dow(dow: str) -> str:
    if dow not in _DOW:
        return "Monday"
    return _DOW[(_DOW.index(dow) + 1) % 7]
