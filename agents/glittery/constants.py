"""Hand-curated constants from recon — single source of truth.

Sourced from recon_data/SUMMARY.md and the per-scenario observations. These do
NOT change between scenarios (verified across 12 games × 4 scenarios). Only
day-to-day values that can drift (supplier prices under inflation, supplier
availability under outage) are read fresh from each observation.
"""

from __future__ import annotations

# ── Supplier topology (constant across scenarios in recon) ─────────────────────
SUPPLIERS: dict[str, dict] = {
    "Fresh Farms NL": {
        "lead": 1, "days": ["Monday", "Wednesday", "Friday"], "min": 5.0,
        "ings": {"Tomato Sauce": 3.1, "Mushrooms": 4.2, "Lettuce": 2.8, "Chicken": 8.5},
        "code": "FFN",
    },
    "Canal Dairy Co.": {
        "lead": 1, "days": ["Tuesday", "Thursday", "Saturday"], "min": 5.0,
        "ings": {"Mozzarella": 9.5, "Cream": 3.8},
        "code": "CDC",
    },
    "Italian Imports Co.": {
        "lead": 3, "days": ["Wednesday"], "min": 10.0,
        "ings": {"Flour": 1.5, "Fresh Pasta": 4.8, "Pepperoni": 12.0, "Tomato Sauce": 2.6},
        "code": "III",
    },
    "Nordic Fish Co.": {
        "lead": 1, "days": ["Monday", "Thursday"], "min": 5.0,
        "ings": {"Salmon": 18.5},
        "code": "NFC",
    },
    "North Sea Millers": {
        "lead": 2, "days": ["Monday", "Wednesday", "Friday"], "min": 10.0,
        "ings": {"Flour": 1.8, "Fresh Pasta": 5.5},
        "code": "NSM",
    },
}

# Reverse lookup: which supplier(s) sell each ingredient (cheapest first)
INGREDIENT_SUPPLIERS: dict[str, list[tuple[str, float]]] = {}
for _sup_name, _sup in SUPPLIERS.items():
    for _ing, _price in _sup["ings"].items():
        INGREDIENT_SUPPLIERS.setdefault(_ing, []).append((_sup_name, _price))
for _ing in INGREDIENT_SUPPLIERS:
    INGREDIENT_SUPPLIERS[_ing].sort(key=lambda x: x[1])

# Outage / disruption resilience signals
SINGLE_SOURCE = {ing for ing, sups in INGREDIENT_SUPPLIERS.items() if len(sups) == 1}
DUAL_SOURCE = {ing for ing, sups in INGREDIENT_SUPPLIERS.items() if len(sups) >= 2}

# 3-letter supplier code → full name (for compact memory)
SUPPLIER_BY_CODE = {info["code"]: name for name, info in SUPPLIERS.items()}
SUPPLIER_CODE = {name: info["code"] for name, info in SUPPLIERS.items()}

# ── Recipes ────────────────────────────────────────────────────────────────────
# name -> (base_price, {ingredient: kg_per_cover})
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

# Dish-mix prior — observed proportions when all 8 dishes active. From recon
# (these are reasonable defaults; agent will adapt if certain dishes are dropped).
DISH_MIX_PRIOR: dict[str, float] = {
    "Pizza Margherita":     0.16,
    "Pizza Pepperoni":      0.13,
    "Spaghetti Carbonara":  0.13,
    "Chicken Caesar Salad": 0.10,
    "Mushroom Tagliatelle": 0.13,
    "Mushroom Risotto":     0.10,
    "Chicken Parmesan":     0.13,
    "Grilled Salmon":       0.12,
}

# ── Day-of-week priors (covers observed under recon's survival agent w/ staff=6) ─
# Used in early game (day ≤ 3) before per-DOW data accumulates.
DOW_BASELINE_COVERS: dict[str, float] = {
    "Monday": 35.0, "Tuesday": 64.0, "Wednesday": 58.0, "Thursday": 75.0,
    "Friday": 88.0, "Saturday": 64.0, "Sunday": 13.0,
}

DOW_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DOW_LETTER = {"Monday": "M", "Tuesday": "T", "Wednesday": "W", "Thursday": "R",
              "Friday": "F", "Saturday": "S", "Sunday": "U"}
DOW_BY_LETTER = {v: k for k, v in DOW_LETTER.items()}

# DOW staffing template (from recon analysis — Sat needs max staff; Sun/Mon dead)
DOW_STAFF_DEFAULT: dict[str, int] = {
    "Monday": 4, "Tuesday": 6, "Wednesday": 6, "Thursday": 8,
    "Friday": 10, "Saturday": 12, "Sunday": 3,
}

# ── Reputation band ordering (low → high) ──────────────────────────────────────
REP_BANDS = ["Poor", "Fair", "Good", "Very Good", "Excellent"]
REP_RANK = {b: i for i, b in enumerate(REP_BANDS)}
REP_LETTER = {"Poor": "P", "Fair": "F", "Good": "G", "Very Good": "VG", "Excellent": "E"}
REP_BY_LETTER = {v: k for k, v in REP_LETTER.items()}

WALKOUT_BANDS = ["None", "Few", "Some", "Many"]
WALKOUT_RANK = {b: i for i, b in enumerate(WALKOUT_BANDS)}
WALKOUT_LETTER = {"None": "N", "Few": "F", "Some": "S", "Many": "M"}

MODE_LETTER = {"NORMAL": "N", "DEFENSIVE": "D", "END_GAME": "E", "SURGE": "U",
               "RENOVATION": "R", "EMERGENCY": "X"}

# ── Supplier reliability priors (observed across recon) ────────────────────────
FILL_RATE_PRIOR: dict[str, float] = {
    "Fresh Farms NL":      0.78,
    "Canal Dairy Co.":     0.79,
    "Italian Imports Co.": 0.82,
    "Nordic Fish Co.":     0.85,
    "North Sea Millers":   0.85,
}

# ── Economics ──────────────────────────────────────────────────────────────────
OVERHEAD_PER_DAY = 300.0    # fixed
STAFF_COST_DAY   = 120.0    # per-person per-day
CASH_RESERVE     = 2000.0   # never drop below
STARTING_CASH    = 15000.0

# Price multiplier grid the LLM may use (must match prompt.py SYSTEM_PROMPT)
PRICE_GRID = [0.85, 0.90, 0.95, 1.0, 1.05, 1.10, 1.15]
MARKETING_GRID = [0, 100, 200, 350, 500]
STAFF_DELTA_GRID = [-2, -1, 0, 1, 2]

# Hard bounds from AGENT_CONTRACT
STAFF_MIN, STAFF_MAX = 3, 15
MARKETING_MIN, MARKETING_MAX = 0, 500
MENU_MIN_DISHES = 5
PRICE_MULT_MIN, PRICE_MULT_MAX = 0.8, 1.2

# Alert keywords → behavioral flags (no scenario-name matching anywhere)
ALERT_KEYWORDS_CRISIS = ("disruption", "shipping", "shortage", "outage")
ALERT_KEYWORDS_RENOVATION = ("renovation", "dining room")
ALERT_KEYWORDS_WARN = ("warn", "potential")
ALERT_KEYWORDS_SCARE = ("scare", "health", "outbreak", "illness")
ALERT_KEYWORDS_FESTIVAL = ("festival", "tourist", "surge")
ALERT_KEYWORDS_INFLATION = ("inflation", "price", "cost rise")
ALERT_KEYWORDS_BAN = ("ban", "recall", "closed")
