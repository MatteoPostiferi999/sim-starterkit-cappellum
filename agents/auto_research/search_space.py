"""The set of knobs the auto-tuner is allowed to mutate.

Each entry is a tuple: (overlay_key, candidate_values, group).

Grouping limits cross-knob interaction during coordinate descent:
  - "horizon"   : MPC planning lookahead + discount
  - "sim"       : digital-twin calibration constants
  - "candidates": staff / price / marketing enumeration grid
  - "supply"    : ordering buffers and cash reserve
  - "anchor"    : per-DOW staff anchor

The search space is deliberately conservative: every value is a small, plausible
perturbation of the current default. Big rewrites belong in a separate branch.
"""

from __future__ import annotations

# Each tuple: (key, candidate_values, group)
SEARCH_SPACE: list[tuple[str, list, str]] = [
    # ── MPC horizon & discount ─
    ("mpc.HORIZON_DAYS",         [2, 3, 4, 5],            "horizon"),
    ("mpc.HORIZON_DAYS_LONG",    [4, 5, 6, 7],            "horizon"),
    ("simulator.GAMMA",          [0.85, 0.90, 0.92, 0.95, 0.98], "horizon"),

    # ── Digital-twin calibration ─
    ("simulator.AVG_BASE_PRICE_PER_COVER",  [16.5, 17.5, 18.3, 19.0, 20.0], "sim"),
    ("simulator.AVG_INGREDIENT_COST_PER_COVER", [2.0, 2.45, 3.0, 3.5],      "sim"),
    ("simulator.KITCHEN_THROUGHPUT_PER_STAFF",  [9.0, 11.0, 13.0, 15.0],    "sim"),
    ("simulator.TABLE_CAPACITY_NORMAL",         [100.0, 115.0, 130.0],       "sim"),
    ("simulator.TABLE_CAPACITY_RENOVATION",     [50.0, 60.0, 70.0],          "sim"),
    ("simulator.WALKOUT_PENALTY_PER_WALKOUT",   [1.5, 3.0, 5.0, 8.0],        "sim"),
    ("simulator.REP_PENALTY_SCALE",             [10.0, 18.0, 30.0, 50.0],    "sim"),
    ("simulator.SAT_PENALTY_SCALE",             [15.0, 25.0, 40.0],          "sim"),

    # ── Candidate enumeration ─
    ("candidates.PRICE_MULTS",        [
        [0.95, 1.00, 1.05, 1.10, 1.15, 1.20],            # default
        [1.00, 1.05, 1.10, 1.15, 1.18, 1.20],            # bias high
        [1.05, 1.10, 1.12, 1.15, 1.18, 1.20],            # premium-only
        [0.90, 1.00, 1.05, 1.10, 1.15, 1.20],            # wider down
        [0.95, 1.00, 1.05, 1.08, 1.12, 1.15, 1.18, 1.20], # finer grid
    ], "candidates"),
    ("candidates.MARKETING_LEVELS", [
        [0, 200, 500],
        [0, 150, 300, 500],
        [0, 100, 250],
        [0, 350],
        [0],  # disable marketing entirely
    ], "candidates"),
    ("candidates.DEFAULT_FUTURE_PRICE_MULT",        [1.00, 1.05, 1.08, 1.10, 1.12, 1.15], "candidates"),
    ("candidates.DEFAULT_FUTURE_MARKETING_WEEKEND", [0, 150, 200, 300, 500],              "candidates"),

    # ── Supply buffer ─
    ("rules_supply.BUFFER_NORMAL",      [1.20, 1.30, 1.40, 1.50, 1.60], "supply"),
    ("rules_supply.BUFFER_EARLY",       [1.40, 1.55, 1.70, 1.85, 2.00], "supply"),
    ("rules_supply.BUFFER_CRISIS",      [1.40, 1.60, 1.80, 2.00],       "supply"),
    ("rules_supply.BUFFER_ENDGAME",     [0.95, 1.00, 1.05, 1.10],       "supply"),
    ("rules_supply.BUFFER_RENOVATION",  [1.05, 1.15, 1.20, 1.30, 1.40], "supply"),
    ("rules_supply.CASH_RESERVE_OVERRIDE", [None, 1000.0, 1500.0, 2000.0, 3000.0], "supply"),

    # ── Per-DOW staff anchor ─
    ("candidates.DOW_STAFF_ANCHOR.Monday",    [3, 4, 5],          "anchor"),
    ("candidates.DOW_STAFF_ANCHOR.Tuesday",   [5, 6, 7],          "anchor"),
    ("candidates.DOW_STAFF_ANCHOR.Wednesday", [5, 6, 7],          "anchor"),
    ("candidates.DOW_STAFF_ANCHOR.Thursday",  [7, 8, 9],          "anchor"),
    ("candidates.DOW_STAFF_ANCHOR.Friday",    [9, 10, 11, 12],    "anchor"),
    ("candidates.DOW_STAFF_ANCHOR.Saturday",  [11, 12, 13, 14, 15], "anchor"),
    ("candidates.DOW_STAFF_ANCHOR.Sunday",    [3, 4, 5],          "anchor"),
]


def all_keys() -> list[str]:
    return [k for k, _, _ in SEARCH_SPACE]


def candidates_for(key: str) -> list:
    for k, vals, _ in SEARCH_SPACE:
        if k == key:
            return list(vals)
    return []


def group_of(key: str) -> str:
    for k, _, g in SEARCH_SPACE:
        if k == key:
            return g
    return "other"
