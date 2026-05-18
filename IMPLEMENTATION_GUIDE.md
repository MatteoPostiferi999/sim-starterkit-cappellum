# RestBench Multi-Agent Implementation Guide

> Hand this file to Claude Code as the full specification. It describes architecture,
> data structures, file layout, every class and function contract, and implementation
> priorities. Do not start coding until you have read all sections.

---

## 0. Project context

We are building an AI agent for the **RestBench hackathon**: a 30-day Italian
restaurant simulation exposed as a REST API. The agent must order ingredients,
set prices, manage staff, and run promotions to maximise a composite score.

```
total_score = net_profit - penalties(satisfaction, reputation, walkouts, waste)
bankruptcy = instant −100,000
```

Baseline (naive rule) scores around −15,000. Target: positive score, consistent
across all 10 evaluation scenarios × 3 seeds = 30 games.

API server: `http://52.48.183.209:8001`  
Swagger docs: `http://52.48.183.209:8001/docs`

### OpenAI-compatible client (use everywhere)

```python
import openai

client = openai.OpenAI(
    api_key="<INSERT_KEY_MANUALLY>",
    base_url="http://litellm-production.eba-pvykax23.eu-west-1.elasticbeanstalk.com",
)
```

Model to use: `gpt-4o` for judges and orchestrator, `gpt-4o-mini` for
proposers and critics (cheaper, faster, good enough for domain reasoning).

---

## 1. Repository layout

```
agents/
  my_agent.py             # entry point — imports orchestrator, runs game
  orchestrator.py         # master loop: routes obs → experts → merges actions
  game_state.py           # GameState, DaySnapshot, RollingStats, compress_notes()
  deterministic_tools.py  # pure-Python tools: inventory_future, delivery_schedule, …
  experts/
    __init__.py
    base_expert.py        # ExpertBase: propose → critic → judge loop
    supply_chain.py       # SupplyChainExpert
    staffing.py           # StaffingExpert
    pricing_menu.py       # PricingMenuExpert
    marketing_promos.py   # MarketingPromosExpert
    scenario_alerts.py    # ScenarioAlertsExpert
  runner.py               # unchanged from starter kit
  naive_rule.py           # unchanged — used as fallback
```

---

## 2. Data model: `game_state.py`

### 2.1 `DaySnapshot`

One instance per completed day. Stored in `GameState.history`.

```python
@dataclass
class DerivedMetrics:
    supplier_fill_rates: dict[str, float]      # supplier_name → fill_rate (0-1)
    supplier_ontime_rates: dict[str, float]    # supplier_name → on_time_rate (0-1)
    inventory_delta: dict[str, float]          # ingredient → kg change vs prev day
    covers: int                                # total_covers from service_summary
    revenue: float
    waste_cost: float
    walkout_band: str                          # "None"|"Few"|"Some"|"Many"
    bottleneck_hours: list[int]
    stockouts: list[str]                       # dishes that ran out
    avg_wait: float
    promo_ran: bool                            # happy hour ran this day
    marketing_spend: float
    daily_special: str | None

@dataclass
class DaySnapshot:
    day: int
    day_of_week: str
    observation: dict          # full raw observation, unchanged
    actions_taken: list[dict]  # tool calls submitted this turn
    day_result: dict           # from end-turn response
    derived: DerivedMetrics
```

### 2.2 `RollingStats`

Always-current aggregations recomputed from `history` on every turn. Passed
to every expert as part of their context.

```python
@dataclass
class RollingStats:
    # Demand patterns
    dow_covers: dict[str, list[int]]    # "Monday" → [covers_day1, covers_day8, ...]
    dow_avg: dict[str, float]           # "Monday" → rolling average
    weather_covers: dict[str, list[int]]  # "sunny" → [covers, ...]

    # Supplier reliability (recomputed from full delivery_history each turn)
    supplier_fill: dict[str, float]     # fill_rate per supplier
    supplier_ontime: dict[str, float]   # on_time_rate per supplier
    supplier_last_failure: dict[str, int | None]  # day of last failure

    # Promotion state
    happy_hour_streak: int              # consecutive days happy hour ran
    last_happy_hour_rest_day: int | None
    consecutive_marketing_days: int

    # Price elasticity log
    # dish → list of {day, price_multiplier, covers, revenue}
    price_log: dict[str, list[dict]]

    # Stockout history
    stockout_history: list[dict]        # {day, dish, hour}

    # Review/reputation trend
    recent_star_avg: float              # avg stars over last 7 days
    star_trend: str                     # "improving"|"stable"|"declining"

    # Scenario detection
    scenario_signals: list[dict]        # {day, signal_type, text}
    scenario_hypothesis: str            # "baseline"|"supply_crisis"|"tourist_season"|...
    scenario_confidence: float          # 0.0–1.0

    # Financial trend
    daily_profits: list[float]          # revenue - costs per day
    cash_trend: str                     # "improving"|"stable"|"declining"
```

### 2.3 `GameState`

The single object passed everywhere. Created once per game, mutated each turn.

```python
class GameState:
    def __init__(self, game_id: str, initial_observation: dict):
        self.game_id = game_id
        self.history: list[DaySnapshot] = []
        self.rolling: RollingStats = RollingStats(...)   # zeroed/empty
        self.current_observation: dict = initial_observation
        self.current_day: int = 1

    def record_turn(
        self,
        observation_before: dict,
        actions_taken: list[dict],
        day_result: dict,
        observation_after: dict,
    ) -> None:
        """
        Called after each end-turn response. Appends a DaySnapshot,
        recomputes RollingStats, and updates current_observation.
        """

    def rebuild_rolling_stats(self) -> None:
        """
        Recomputes all RollingStats fields from scratch using self.history.
        Called at the end of record_turn(). Pure computation, no side effects.
        """

    def compress_notes(self) -> str:
        """
        Serialises RollingStats into ≤4000 chars for save_notes.
        Format: compact key:value lines — see section 4 for schema.
        """

    def get_expert_context(self, expert_name: str) -> dict:
        """
        Returns a filtered dict of everything an expert needs:
          - current_observation (full)
          - rolling stats (full RollingStats as dict)
          - last N DaySnapshots (N=7) as condensed dicts
          - deterministic tool results pre-computed for this turn
        Each expert receives the same current_observation and rolling stats.
        The 'deterministic tool results' are pre-run before any expert fires,
        so experts reason from facts, not from LLM-imagined inventory math.
        """
```

### 2.4 Snapshot computation helpers

These are called inside `record_turn` to produce `DerivedMetrics`:

```python
def _compute_supplier_fill_rates(delivery_history: list) -> dict[str, float]:
    """Group by supplier, compute delivered/ordered ratio."""

def _compute_stockouts(service_summary: dict) -> list[str]:
    """Return list of dish names present in dishes_unavailable_at."""

def _compute_promo_ran(actions_taken: list[dict]) -> bool:
    """True if any action has tool == 'run_happy_hour'."""

def _compute_daily_special(actions_taken: list[dict]) -> str | None:
    """Extract dish from offer_daily_special action, or None."""
```

---

## 3. Deterministic tools: `deterministic_tools.py`

All functions in this file are **pure Python** — no LLM calls, no HTTP calls.
They are fast and called before any expert fires, so experts receive computed
facts rather than having to reason about them.

### 3.1 `inventory_future`

```python
def inventory_future(
    inventory: list[dict],          # from observation
    pending_orders: list[dict],     # from observation
    menu_book: list[dict],          # from observation
    active_menu: list[str],         # from observation
    expected_covers_per_day: dict,  # {day_offset: int} — from demand_estimate
    dish_mix: dict[str, float],     # dish → fraction of covers (from dishes_sold)
    n_days: int = 7,
) -> dict:
    """
    Simulates inventory levels day by day.

    Algorithm:
      For each future day d in 1..n_days:
        1. Apply pending deliveries scheduled for day (current_day + d).
           Delivery arrives as a new batch with full shelf_life.
        2. Remove batches where expires_in_days - d <= 0 (spoilage).
        3. Compute expected consumption:
             for each active dish:
               covers_today * dish_mix[dish] * recipe_qty_per_dish[ingredient]
           Subtract from oldest batches first (FIFO).
        4. Record remaining kg per ingredient.

    Returns:
    {
      "daily_levels": {
        "Tomato Sauce": [8.3, 6.1, 3.9, 1.7, 0.0, 0.0, 0.0],  # kg each day
        ...
      },
      "first_stockout": {
        "Tomato Sauce": 4,   # day offset when it hits 0, or None
        ...
      },
      "expiry_waste": {
        "Mozzarella": {"day": 2, "kg": 1.5},   # batch that will expire unused
        ...
      }
    }
    """
```

### 3.2 `delivery_schedule`

```python
def delivery_schedule(
    current_day: int,               # simulation day (1-30), where day 1 = Monday
    supplier: dict,                 # one entry from supplier_catalog
    order_placed_today: bool = True,
) -> int:
    """
    Computes the simulation day an order placed today will arrive.

    Algorithm:
      earliest_day = current_day + supplier["lead_time_days"]
      Walk forward from earliest_day until we hit a weekday that is in
      supplier["delivery_days"]. Return that simulation day.

    Day-of-week mapping (day 1 = Monday):
      day % 7 == 1 → Monday, == 2 → Tuesday, ..., == 0 → Sunday

    Returns: int (simulation day of delivery)
    """

def next_delivery_window(
    current_day: int,
    supplier: dict,
    n_windows: int = 3,
) -> list[int]:
    """Returns the next n delivery days for a supplier."""
```

### 3.3 `demand_estimate`

```python
def demand_estimate(
    day_of_week: str,
    weather: str,
    rolling: RollingStats,
    customer_trend: str,
) -> dict:
    """
    Returns a covers estimate using observed data.

    Algorithm:
      base = rolling.dow_avg.get(day_of_week, 80)   # fallback if no data yet

      weather_modifier = {
        "sunny": 1.10, "cloudy": 1.00, "rainy": 0.85, "stormy": 0.65
      }[weather]

      trend_modifier = {
        "Growing": 1.08, "Stable": 1.00, "Declining": 0.90
      }[customer_trend]

      estimate = base * weather_modifier * trend_modifier

    Returns:
    {
      "expected_covers": int,
      "low": int,   # estimate * 0.85
      "high": int,  # estimate * 1.15
      "confidence": "high"|"medium"|"low"  # low until 2+ obs per weekday
    }
    """
```

### 3.4 `cash_runway`

```python
def cash_runway(
    cash: float,
    staff_level: int,
    pending_order_costs: float,     # sum of pending order values (already committed)
    expected_daily_revenue: float,
    days_remaining: int,
) -> dict:
    """
    Projects cash position.

    Daily burn = 300 (fixed) + staff_level * 120
    Net daily = expected_daily_revenue - daily_burn

    Returns:
    {
      "days_until_bankruptcy": int | None,   # None if never
      "projected_final_cash": float,
      "safe_order_budget": float,            # cash - 3*daily_burn - buffer(1500)
    }
    """
```

### 3.5 `compute_order_gap`

```python
def compute_order_gap(
    inventory_future_result: dict,
    supplier_catalog: list[dict],
    pending_orders: list[dict],
    current_day: int,
    supplier_fill_rates: dict[str, float],
) -> list[dict]:
    """
    Identifies ingredients that will stock out before the next delivery
    and no pending order covers the gap.

    For each ingredient with first_stockout day D:
      Check if a pending order arrives before D.
      If not, compute how much needs to be ordered and from which supplier
      (cheapest reliable one — weight price by fill_rate).

    Returns list of recommended orders:
    [
      {
        "ingredient": "Tomato Sauce",
        "supplier": "Fresh Farms NL",
        "quantity_kg": 15.0,
        "delivery_day": 6,
        "urgency": "critical"|"high"|"medium",
        "reason": "stockout projected day 4, no pending order",
      },
      ...
    ]
    """
```

---

## 4. Notes compression schema: `compress_notes()`

The notes field is 4000 chars. We need to persist enough for the LLM to have
continuity. Format is compact key:value lines, no prose.

```
d:{day}|cash:{cash:.0f}|rep:{reputation_band}|trend:{customer_trend}|wx:{weather_today}
dow:{Mon}={avg:.0f},{Tue}={avg:.0f},{Wed}={avg:.0f},{Thu}={avg:.0f},{Fri}={avg:.0f},{Sat}={avg:.0f},{Sun}={avg:.0f}
fill:{supplier1}={fill_rate:.2f},{supplier2}={fill_rate:.2f},...
ontime:{supplier1}={ontime:.2f},...
hh_streak:{n}|hh_rest:{day_or_none}|mkt_streak:{n}
price:{dish1}={multiplier:.2f}x:d{start}-{end}:{rev_delta:+.0f}%|{dish2}=...
stockouts:{dish}:d{day},{dish}:d{day},...   (last 10 only)
scenario:{hypothesis}:conf={confidence:.2f}:d{detected_day}
flags:{comma_separated_expert_flags}
profit7:{sum of last 7 days net profit:.0f}
star7:{recent_star_avg:.1f}|star_trend:{improving/stable/declining}
spec_last:{dish}:d{day},{dish}:d{day}  (last 3 specials)
```

Rules for `compress_notes()`:
- Always write all lines even if values are defaults/empty.
- Truncate `stockouts` to the 10 most recent.
- Truncate `price` log to the 5 most recent price changes.
- If total length exceeds 4000, drop `price` log first, then `stockouts`,
  then `spec_last`. Never drop `d:`, `dow:`, `fill:`, `scenario:`, `flags:`.

---

## 5. Expert base class: `experts/base_expert.py`

```python
class ExpertBase:
    name: str                    # e.g. "supply_chain"
    model_proposer: str          # "gpt-4o-mini"
    model_critic: str            # "gpt-4o-mini"
    model_judge: str             # "gpt-4o"
    max_rounds: int = 2          # propose → critique → revise → judge
    system_prompt: str           # domain knowledge — see section 6
    critic_prompt: str
    judge_prompt: str

    def run(self, context: dict) -> ExpertResult:
        """
        Runs the propose → criticise → judge loop.

        context contains:
          - observation: full current observation
          - rolling: RollingStats as dict
          - precomputed: results of deterministic tools (see section 3)
          - scenario_context: injected by ScenarioAlertsExpert (see section 6.5)
          - notes: current compressed notes string

        Returns ExpertResult.
        """

    def _call_llm(self, model: str, messages: list) -> str:
        """Wraps client.chat.completions.create, returns content string."""

    def _parse_actions(self, response: str) -> list[dict]:
        """
        Parses JSON array of tool calls from LLM response.
        Strips markdown fences. Returns [] on parse error (never raises).
        """
```

```python
@dataclass
class ExpertResult:
    expert_name: str
    proposed_actions: list[dict]    # tool calls
    confidence: str                 # "high"|"medium"|"low"
    reasoning: str                  # judge's reasoning, stored in notes/flags
    rounds_taken: int
    flags: list[str]                # e.g. ["low_cash_warning", "supply_crisis_signal"]
```

### Loop implementation

```
Round 0:
  proposal = proposer_llm(system_prompt, context)

For round in 0..max_rounds-1:
  critique = critic_llm(critic_prompt, context + proposal)
  judgment = judge_llm(judge_prompt, context + proposal + critique)
  if judgment["proceed"]:
      break
  proposal = proposer_llm(system_prompt, context + critique + "Revise.")

Return ExpertResult(proposal, judgment["confidence"], judgment["reasoning"])
```

Judge LLM must return JSON:
```json
{
  "proceed": true,
  "confidence": "high",
  "reasoning": "Orders cover 5-day buffer, no double-ordering, within budget.",
  "flags": []
}
```

---

## 6. Expert specifications

Each expert has three prompts: `system_prompt` (proposer role + domain
knowledge), `critic_prompt`, and `judge_prompt`. Below is the **knowledge
and context** each expert must have. Write these as detailed system prompts.

### 6.1 `SupplyChainExpert`

**Proposes:** `place_order` actions only.

**System prompt must encode:**
- Delivery calendar math: order today on day D with lead_time L and
  delivery_days [Mon, Wed, Fri] → find first weekday ≥ D+L in that list.
- FIFO consumption: oldest batches consumed first; batches expiring in ≤1 day
  are unreliable and should be treated as zero for coverage purposes.
- Effective coverage = fresh_stock + pending_orders (discounted by fill_rate) −
  projected consumption until next delivery window.
- Never order what's already in pending that will arrive before stock runs out.
- Diversify critical ingredients across 2 suppliers when fill_rate < 0.90.
- Overstocking accelerates spoilage; don't order more than 5 days of demand
  for short-shelf-life ingredients (shelf_life ≤ 7 days).
- Cash safety: don't order if `precomputed.cash_runway.safe_order_budget` < cost.

**Context keys used:**
`observation.inventory`, `observation.pending_orders`,
`observation.supplier_catalog`, `observation.delivery_history`,
`observation.service_summary.dishes_unavailable_at`,
`rolling.supplier_fill`, `rolling.stockout_history`,
`precomputed.inventory_future`, `precomputed.order_gap`,
`precomputed.cash_runway`, `scenario_context`

**Critic focuses on:**
- Double-ordering (pending already covers the gap)
- Over-ordering perishables (waste risk)
- Budget overrun
- Choosing unreliable supplier when a reliable alternative exists

**Judge accepts when:**
- No projected stockout in the next 4 days after orders land
- No double-order detected
- Total order cost ≤ `safe_order_budget`
- Short-shelf-life orders ≤ 5 days demand

---

### 6.2 `StaffingExpert`

**Proposes:** `set_staff_level` only.

**System prompt must encode:**
- Staff range 3–15, cost 120 EUR/day each.
- Default 8. Naive baseline uses flat 5.
- Kitchen bottleneck hours in service_summary → understaffed.
- Peak wait > 10 min → understaffed.
- Walkout band "Some" or "Many" + bottleneck hours → urgent understaffing.
- Table utilization 1.0 + no bottleneck hours → table cap, not staff cap.
  Adding staff won't help; don't waste budget.
- Day-of-week demand table: use `rolling.dow_avg` for base covers.
- Weather: stormy → −35% covers, rainy → −15%, sunny → +10%.
- tourist_season alert → staff up proactively.
- renovation scenario (days 1–12) → capacity capped ~70 covers, max staff 7.
- End-game (days 28–30): protect quality, don't cut staff.

**Context keys used:**
`observation.staff_level`, `observation.service_summary`,
`observation.weather_forecast`, `observation.day_of_week`,
`observation.customer_trend`, `rolling.dow_avg`, `rolling.dow_covers`,
`precomputed.demand_estimate`, `scenario_context`

**Critic focuses on:**
- Overstaffing on confirmed slow days (waste wages)
- Understaffing when demand signal is high
- Missing renovation scenario capacity cap
- Not using tomorrow's weather forecast (index 0 of weather_forecast)

**Judge accepts when:**
- Proposed level matches demand estimate within 20%
- No bottleneck signals present (or level addresses them)
- Cost delta vs current level is justified by revenue upside

---

### 6.3 `PricingMenuExpert`

**Proposes:** `set_price` and `set_menu` actions.

**System prompt must encode:**
- Price range: 0.80×–1.20× base_price per dish.
- Moderate increases (≤1.10×) tolerable; aggressive (>1.15×) drives away demand.
- Price elasticity is asymmetric — downside of over-pricing is steeper than upside.
- Raise prices on high-demand days (sunny, weekend, tourist surge).
- Lower prices on slow days to drive volume.
- Use `rolling.price_log` to see past price → revenue response before changing.
- Never change prices in last 5 days without strong reason (no time to recover).
- Menu: minimum 5 active dishes; stay comfortably at 7+.
- Never shrink the menu — variety loss erodes demand.
- Avoid changing the menu mid-game unless a dish has zero sales for 3+ days or
  its ingredient is unavailable.
- New dishes trigger a 2-day kitchen learning curve — avoid late-game additions.

**Context keys used:**
`observation.menu_book`, `observation.active_menu`,
`observation.service_summary.dishes_sold`,
`observation.service_summary.dishes_unavailable_at`,
`rolling.price_log`, `observation.days_remaining`, `scenario_context`

**Critic focuses on:**
- Raising prices when dish was unavailable (stockout, not inelastic demand)
- Adding new dishes late in the game
- Shrinking menu below 7 dishes
- Changing prices without elasticity evidence from `price_log`

**Judge accepts when:**
- Price changes are supported by at least 2 days of elasticity evidence, or
  scenario context clearly warrants it
- Menu has ≥ 7 active dishes
- No new dishes if `days_remaining` < 10

---

### 6.4 `MarketingPromosExpert`

**Proposes:** `run_happy_hour`, `offer_daily_special`, `set_marketing_spend`.

**System prompt must encode:**
- Happy hour runs 15:00–18:00. Boosts demand AND discounts prices.
- Diminishing returns after 3 consecutive days. Rest for 2+ days to restore.
- Stopping happy hour cold after sustained use causes a demand dip next day.
  Plan gradual exits — don't run 7 days then stop.
- Never run happy hour when table_utilization_peak > 0.85 — walkouts will
  absorb the extra demand.
- Daily special: always propose one, pick the dish whose ingredient has the
  shortest remaining `expires_in_days`. Free satisfaction bonus, no downside.
- Marketing spend 0–500 EUR. Diminishing returns within day and across days.
  150–200 EUR often better ROI than 500 EUR. Do not spend flat 500 every day.
  Strategic timing: spend more on days with spare capacity and upside.
  Never spend marketing when table_utilization_peak ≥ 0.90.
- Early-game marketing (days 1–10) converts prospects to regulars — higher ROI.

**Context keys used:**
`observation.service_summary.table_utilization_peak`,
`observation.day_of_week`, `observation.weather_today`,
`observation.cash`, `observation.customer_trend`,
`rolling.happy_hour_streak`, `rolling.last_happy_hour_rest_day`,
`rolling.consecutive_marketing_days`,
`precomputed.demand_estimate`, `scenario_context`

**Critic focuses on:**
- Happy hour during high utilization (manufactures walkouts)
- Running happy hour 4+ consecutive days (decaying returns)
- Spending 500 EUR marketing on an already-packed day
- Forgetting daily special (always a missed free upside)
- Not picking the expiring-ingredient dish as special

**Judge accepts when:**
- Happy hour correctly gated by utilization and streak
- Daily special always present and pointed at expiring ingredients
- Marketing spend calibrated to spare capacity, not flat maximum

---

### 6.5 `ScenarioAlertsExpert`

**Does not propose tool calls directly.** Instead, it produces a
`scenario_context` dict that is injected into every other expert's context.

**System prompt must encode:**
- Four known scenarios: baseline, supply_crisis, tourist_season, renovation.
  Six hidden scenarios will appear at evaluation time — infer generically.
- supply_crisis signals: `alerts` containing "supply", "logistics", "disruption",
  "halted"; delivery_history fill_rate drops suddenly; multiple supplier
  failures in 3 days.
- tourist_season signals: covers surge well above dow_avg baseline;
  `alerts` containing "tourist", "surge", "demand"; then a drop after the surge.
- renovation signals: `alerts` containing "renovation", "capacity", "tables";
  covers hard-capped at lower level than dow_avg predicts.
- Inflation signals: `alerts` containing "price", "inflation"; supplier prices
  rising across catalog.
- Generic alert keywords to always react to: "supply", "demand", "price",
  "disruption", "closure", "surge", "shortage", "staff", "weather".
- Hypothesis confidence: "provisional" (1 signal), "confident" (3+ signals or
  explicit alert confirmation).

**Output (scenario_context dict):**
```python
{
  "hypothesis": "supply_crisis",
  "confidence": 0.85,
  "detected_day": 9,
  "adjustments": {
    "supply_chain": "diversify suppliers, build 5-day buffer, avoid concentration",
    "staffing": "no change",
    "pricing_menu": "no change",
    "marketing_promos": "pause marketing spend, conserve cash",
  },
  "flags": ["SUPPLY_CRISIS_PROVISIONAL"],
  "raw_alerts": ["Supplier X has halted operations"],
}
```

**Context keys used:**
`observation.alerts`, `observation.delivery_history`,
`observation.supplier_catalog`, `rolling.scenario_signals`,
`rolling.scenario_hypothesis`, `rolling.scenario_confidence`,
`rolling.dow_avg`, `observation.service_summary.total_covers`,
`observation.day`

---

## 7. Orchestrator: `orchestrator.py`

```python
class Orchestrator:
    def __init__(self, game_state: GameState):
        self.state = game_state
        self.experts = {
            "scenario_alerts": ScenarioAlertsExpert(),
            "supply_chain": SupplyChainExpert(),
            "staffing": StaffingExpert(),
            "pricing_menu": PricingMenuExpert(),
            "marketing_promos": MarketingPromosExpert(),
        }

    def decide(self, observation: dict, day: int) -> list[dict]:
        """
        Main per-turn entry point. Returns list of tool calls to submit.

        Steps:
          1. Pre-compute deterministic tools (inventory_future, delivery_schedule,
             demand_estimate, cash_runway, compute_order_gap). These produce
             'precomputed' dict injected into every expert context.

          2. Run ScenarioAlertsExpert first (no loop needed — it reads signals
             and produces scenario_context). Its output is injected into all
             other experts' context.

          3. Run remaining 4 experts IN PARALLEL (use ThreadPoolExecutor).
             Each receives: observation, rolling stats, precomputed, scenario_context.

          4. Collect ExpertResult from each expert.

          5. Run merge_actions() to produce final action list.

          6. Append save_notes action with compressed notes.

          7. Return final action list.
        """

    def _precompute(self, observation: dict) -> dict:
        """Runs all deterministic tools and returns results as a dict."""

    def merge_actions(self, results: list[ExpertResult]) -> list[dict]:
        """
        Combines actions from all experts. Rules:
        - set_staff_level: use StaffingExpert's value. Ignore duplicates.
        - place_order: deduplicate by (supplier, ingredient). If two experts
          somehow propose the same order, keep the larger quantity.
        - set_price: if two experts propose different prices for the same dish,
          use PricingMenuExpert's value (it is the authority on pricing).
        - set_menu: use PricingMenuExpert's value. Max one set_menu per turn.
        - run_happy_hour: use MarketingPromosExpert's decision (bool).
        - offer_daily_special: use MarketingPromosExpert's choice.
        - set_marketing_spend: use MarketingPromosExpert's value.
        - save_notes: generated by orchestrator, not experts.

        After dedup: validate all actions against game rules (price bounds,
        staff range, min_order_kg). Drop invalid ones and log a flag.

        Final order: set_staff_level → set_menu → set_price × N →
          place_order × N → set_marketing_spend → run_happy_hour →
          offer_daily_special → save_notes
        """
```

---

## 8. Entry point: `agents/my_agent.py`

```python
from agents.runner import run_game
from agents.game_state import GameState
from agents.orchestrator import Orchestrator

_state: GameState | None = None
_orchestrator: Orchestrator | None = None

def strategy(observation: dict, day: int) -> list[dict]:
    global _state, _orchestrator

    if day == 1:
        # Game just started — create state and orchestrator
        # game_id not available here; use a placeholder
        _state = GameState(game_id="active", initial_observation=observation)
        _orchestrator = Orchestrator(_state)

    # Record the previous turn's result into state
    # On day 1 there is no previous result — skip.
    # On day 2+, observation already contains yesterday's service results.
    if day > 1:
        _state.update_observation(observation)

    # Decide
    actions = _orchestrator.decide(observation, day)

    return actions

if __name__ == "__main__":
    result = run_game(strategy, team_name="team_name", seed=42)
```

**Important:** `strategy()` is called once per day by `runner.py`. State must
be preserved across calls. Use module-level globals (as above) because
`runner.py` calls `strategy` as a plain function without passing state.

---

## 9. Timing and parallelism

The tick budget is 30 seconds per turn. The orchestrator must finish all LLM
calls and submit actions within this window.

```
Timing budget (approximate):
  Deterministic tools:    ~0.01s
  ScenarioAlertsExpert:   ~1s   (1 LLM call, small context)
  4 experts in parallel:  ~4–6s (each 2 rounds × 2 LLM calls = 4 calls parallel)
  Merge + validation:     ~0.01s
  HTTP action POSTs:      ~1–2s (5–10 actions × 100ms each)
  Total estimate:         ~7–10s — comfortable within 30s
```

Use `concurrent.futures.ThreadPoolExecutor` with `max_workers=4` for the
parallel expert phase. Each expert is independent at this point.

If any expert times out or raises an exception, log it and continue with the
results from the remaining experts. Never let one expert failure abort the turn.

---

## 10. Fallback safety

If the full orchestrator raises an unhandled exception, fall back to the naive
rule agent for that turn:

```python
try:
    actions = _orchestrator.decide(observation, day)
except Exception as e:
    print(f"Orchestrator error day {day}: {e}")
    from agents.naive_rule import strategy as naive_strategy
    actions = naive_strategy(observation, day)
```

The naive rule agent is never the primary path — only the emergency fallback.

---

## 11. Implementation priorities

Build and test in this order. Do not skip ahead.

### Phase 1 — Foundation (no LLM calls yet)

1. `game_state.py`: `DaySnapshot`, `DerivedMetrics`, `RollingStats`, `GameState`
   with `record_turn()` and `rebuild_rolling_stats()`.
2. `deterministic_tools.py`: all 5 functions. Write unit tests for each.
   Test `inventory_future` with a hand-crafted scenario.
   Test `delivery_schedule` against the worked example in the README
   (order Thu, 1-day lead, Wed/Fri delivery → next Friday).
3. `compress_notes()`: implement and verify output ≤ 4000 chars for 30 days
   of fake data.

### Phase 2 — Single expert end-to-end

4. `base_expert.py`: propose → critic → judge loop with real LLM calls.
5. `supply_chain.py`: implement and test against a real game. Run
   `python -m agents.my_agent` and watch that no stockouts occur.

### Phase 3 — All experts

6. Implement `staffing.py`, `pricing_menu.py`, `marketing_promos.py`,
   `scenario_alerts.py` in that order.
7. Implement `orchestrator.py` merge logic.

### Phase 4 — Integration and scoring

8. Run full 30-day game with all experts. Score should be > naive_rule (> −15,000).
9. Run `python -m agents.evaluate agents.my_agent --scenarios baseline,supply_crisis`
10. Tune prompts based on failure modes visible in the logs.
11. Target: all 4 known scenarios surviving without bankruptcy.

---

## 12. Key invariants (never violate)

- **Never go bankrupt.** The orchestrator must check `cash_runway` before
  every order and abort orders that would bring cash below 1500 EUR reserve.
- **Never miss a daily special.** Always include `offer_daily_special` in
  every turn's actions. It is free upside.
- **Always call `save_notes`.** Every turn must end with a `save_notes` action
  carrying the latest `compress_notes()` output.
- **Never double-order.** `SupplyChainExpert` must account for pending orders
  before proposing new ones. `compute_order_gap` enforces this in precompute.
- **Names are case-sensitive.** Use exact supplier, ingredient, and dish names
  from the observation. A typo silently rejects the action.
- **Menu must have ≥ 5 dishes.** `set_menu` with fewer is rejected and wastes
  the tick budget. `PricingMenuExpert` must validate before proposing.
- **Prices must be 0.80×–1.20× base.** Validate in `merge_actions` before
  submitting.

---

## 13. Prompt writing guidelines

When writing the system prompts for each expert:

- Open with the expert's role and single most important rule.
- State what actions the expert may and may not propose.
- List all context keys it should use, by name, so the LLM knows where to look.
- Encode the key heuristics from section 6 explicitly — do not assume the LLM
  will derive them from first principles.
- Close with the output format: "Respond with ONLY a JSON array of tool calls.
  No explanation, no markdown fences."
- For the critic prompt: "You are reviewing a proposed set of actions.
  Identify any violations of the rules above. Be specific. If the proposal is
  sound, say so briefly."
- For the judge prompt: "Given the proposal and critique, decide whether to
  proceed. Respond with JSON only: {proceed, confidence, reasoning, flags}."

---

## 14. Notes on the game mechanics (critical facts)

These are confirmed by the game contract and strategy guide. Encode them in
the relevant expert prompts:

- **Day 1 = Monday.** The full 30-day weekday calendar is deterministic:
  weekends on days 6,7,13,14,20,21,27,28.
- **Service hours 11:00–22:00.** `hourly_covers` index 0 = 11:00, index 11 = 22:00.
- **Walkout penalty is linear** (every walkout costs a fixed amount).
  Reputation/satisfaction penalties are quadratic (crossing below threshold is
  catastrophic — keep a safety margin).
- **Reputation starts "Very Good"** and updates daily as an asymmetric moving
  average. Bad experiences weigh more than good ones. Final reputation matters
  more than average.
- **Ghost reviews from walkouts are always negative** and feed reputation.
  One walkout = lost revenue + penalty + bad review.
- **Overstocking accelerates spoilage** beyond normal expiry rates.
- **New dish learning curve ≈ 2 days.** Kitchen is less efficient with recently
  added dishes.
- **Happy hour effectiveness decays after ~3 consecutive days.**
- **Stopping happy hour cold after sustained use causes a demand dip.**
- **Marketing: constant spend ≠ strategic spend.** Flat 500 EUR/day
  underperforms timed spend.
- **Daily specials do more than the tool reference implies.** Always use one.
- **Renovation scenario:** reduced table capacity days 1–12. After day 12,
  capacity returns to normal.
- **supply_crisis scenario:** a major supplier goes into outage mid-game.
  Watch `delivery_history` for the onset.
- **tourist_season scenario:** large demand swings — surge then drop.

---

## 15. Testing commands

```bash
# Single baseline run
python -m agents.my_agent

# Compare against naive rule
python -m agents.compare

# Evaluate across known scenarios and dev seeds
python -m agents.evaluate agents.my_agent \
  --scenarios baseline,supply_crisis,tourist_season,renovation \
  --seeds 42,88,123

# Quick single-scenario test
python -m agents.evaluate agents.my_agent \
  --scenarios baseline --seeds 42 --quiet
```

Leaderboard check:
```bash
curl http://52.48.183.209:8001/leaderboard
```
