"""Constants for det_agent — calibrated from recon_data/SUMMARY.md."""

from __future__ import annotations

# ── Suppliers (verified across 12 recon games) ─────────────────────────────────
SUPPLIERS: dict[str, dict] = {
    "Fresh Farms NL": {
        "lead": 1, "days": ["Monday", "Wednesday", "Friday"], "min": 5.0,
        "ings": {"Tomato Sauce": 3.1, "Mushrooms": 4.2, "Lettuce": 2.8, "Chicken": 8.5},
    },
    "Canal Dairy Co.": {
        "lead": 1, "days": ["Tuesday", "Thursday", "Saturday"], "min": 5.0,
        "ings": {"Mozzarella": 9.5, "Cream": 3.8},
    },
    "Italian Imports Co.": {
        "lead": 3, "days": ["Wednesday"], "min": 10.0,
        "ings": {"Flour": 1.5, "Fresh Pasta": 4.8, "Pepperoni": 12.0, "Tomato Sauce": 2.6},
    },
    "Nordic Fish Co.": {
        "lead": 1, "days": ["Monday", "Thursday"], "min": 5.0,
        "ings": {"Salmon": 18.5},
    },
    "North Sea Millers": {
        "lead": 2, "days": ["Monday", "Wednesday", "Friday"], "min": 10.0,
        "ings": {"Flour": 1.8, "Fresh Pasta": 5.5},
    },
}

# Reverse: ingredient → [(supplier, price)] cheapest first
ING_SUPPLIERS: dict[str, list[tuple[str, float]]] = {}
for _sn, _s in SUPPLIERS.items():
    for _ing, _p in _s["ings"].items():
        ING_SUPPLIERS.setdefault(_ing, []).append((_sn, _p))
for _ing in ING_SUPPLIERS:
    ING_SUPPLIERS[_ing].sort(key=lambda x: x[1])

# ── Recipes ────────────────────────────────────────────────────────────────────
# dish → (base_price, {ingredient: kg_per_cover})
RECIPES: dict[str, tuple[float, dict[str, float]]] = {
    "Pizza Margherita":     (14.5, {"Flour": 0.25, "Tomato Sauce": 0.09, "Mozzarella": 0.11}),
    "Pizza Pepperoni":      (16.0, {"Flour": 0.25, "Tomato Sauce": 0.085, "Mozzarella": 0.10, "Pepperoni": 0.07}),
    "Spaghetti Carbonara":  (16.5, {"Fresh Pasta": 0.18, "Cream": 0.08}),
    "Chicken Caesar Salad": (15.0, {"Chicken": 0.14, "Lettuce": 0.12}),
    "Mushroom Tagliatelle": (17.5, {"Fresh Pasta": 0.18, "Cream": 0.12, "Mushrooms": 0.09}),
    "Mushroom Risotto":     (19.0, {"Mushrooms": 0.12, "Cream": 0.10}),
    "Chicken Parmesan":     (20.0, {"Chicken": 0.18, "Tomato Sauce": 0.08, "Mozzarella": 0.06}),
    "Grilled Salmon":       (24.0, {"Salmon": 0.20}),
}
ALL_DISHES = list(RECIPES.keys())

# Observed dish mix when all 8 active
DISH_MIX: dict[str, float] = {
    "Pizza Margherita":     0.16,
    "Pizza Pepperoni":      0.13,
    "Spaghetti Carbonara":  0.13,
    "Chicken Caesar Salad": 0.10,
    "Mushroom Tagliatelle": 0.13,
    "Mushroom Risotto":     0.10,
    "Chicken Parmesan":     0.13,
    "Grilled Salmon":       0.12,
}

# ── Day-of-week priors (covers w/ staff=6, baseline survival agent) ────────────
DOW_COVERS_PRIOR: dict[str, float] = {
    "Monday": 35.0, "Tuesday": 64.0, "Wednesday": 58.0, "Thursday": 75.0,
    "Friday": 88.0, "Saturday": 64.0, "Sunday": 13.0,
}

# Target staff level by DOW (matches observed peak demand on each day).
# Sunday is dead (~13 covers) so we still need min 3.
# Tuned down for v1 — over-staffing was eating margin.
DOW_STAFF: dict[str, int] = {
    "Monday": 4, "Tuesday": 5, "Wednesday": 5, "Thursday": 7,
    "Friday": 9, "Saturday": 10, "Sunday": 3,
}

DOW_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DOW_LETTER = {"Monday": "M", "Tuesday": "T", "Wednesday": "W", "Thursday": "R",
              "Friday": "F", "Saturday": "S", "Sunday": "U"}

# ── Reputation / walkout bands ─────────────────────────────────────────────────
REP_BANDS = ["Poor", "Fair", "Good", "Very Good", "Excellent"]
REP_RANK = {b: i for i, b in enumerate(REP_BANDS)}
WALKOUT_BANDS = ["None", "Few", "Some", "Many"]
WALKOUT_RANK = {b: i for i, b in enumerate(WALKOUT_BANDS)}

# ── Empirical supplier fill rates from recon ───────────────────────────────────
FILL_RATE_PRIOR: dict[str, float] = {
    "Fresh Farms NL":      0.73,
    "Canal Dairy Co.":     0.79,
    "Italian Imports Co.": 0.76,
    "Nordic Fish Co.":     0.77,
    "North Sea Millers":   0.80,
}

# ── Economics ──────────────────────────────────────────────────────────────────
OVERHEAD_PER_DAY = 300.0
STAFF_COST_DAY   = 120.0
CASH_RESERVE     = 2000.0
STARTING_CASH    = 15000.0

# Hard bounds
STAFF_MIN, STAFF_MAX = 3, 15
MARKETING_MIN, MARKETING_MAX = 0, 500
MENU_MIN_DISHES = 5
PRICE_MULT_MIN, PRICE_MULT_MAX = 0.80, 1.20

# Weather demand modifiers (from STRATEGY_GUIDE typical assumptions)
WEATHER_MULT = {
    "sunny": 1.10, "cloudy": 1.00, "rainy": 0.85, "stormy": 0.65,
}
TREND_MULT = {"Growing": 1.08, "Stable": 1.00, "Declining": 0.92}
REP_DEMAND_MULT = {"Excellent": 1.10, "Very Good": 1.00, "Good": 0.90,
                   "Fair": 0.75, "Poor": 0.55}

# Approx kitchen throughput (covers per staff per day) — back-fit from recon
KITCHEN_THROUGHPUT_PER_STAFF = 11.0
TABLE_CAPACITY_NORMAL = 115
TABLE_CAPACITY_RENOVATION = 60

# Alert keywords → behavioral flags
ALERT_CRISIS = ("disruption", "shipping", "shortage", "outage", "halted", "halt")
ALERT_RENOVATION = ("renovation", "dining room", "tables unavailable")
ALERT_TOURIST = ("tourist", "festival", "surge", "demand spike")
ALERT_INFLATION = ("inflation", "cost rise", "price increase")
ALERT_HEALTH = ("scare", "health", "outbreak", "illness", "recall")
ALERT_BAN = ("ban", "closed")
