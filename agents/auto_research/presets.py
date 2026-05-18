"""A handful of hand-curated overlays worth trying first.

Each preset is a flat overlay dict that can be passed to `run_benchmark`. The
auto-tuner does NOT use these directly — it explores the SEARCH_SPACE — but
you can run any preset manually to test a hypothesis:

    python -m agents.auto_research.benchmark \
      --overlay-file <(python -c "from agents.auto_research import presets, json; \
         print(json.dumps(presets.PRESETS['premium_pricing']))")

Or pass --overlay 'JSON-as-string' directly.
"""

from __future__ import annotations

PRESETS: dict[str, dict] = {
    # ── Push prices higher: bias the price grid toward the upper end. ─
    "premium_pricing": {
        "candidates.PRICE_MULTS": [1.00, 1.05, 1.10, 1.15, 1.18, 1.20],
        "candidates.DEFAULT_FUTURE_PRICE_MULT": 1.10,
    },

    # ── Tighter supply: reduce buffers, accept more stockout risk for less waste. ─
    "tight_supply": {
        "rules_supply.BUFFER_NORMAL": 1.25,
        "rules_supply.BUFFER_EARLY": 1.50,
        "rules_supply.BUFFER_CRISIS": 1.50,
        "rules_supply.CASH_RESERVE_OVERRIDE": 1500.0,
    },

    # ── Longer horizon: plan 5 days instead of 3 (default). ─
    "long_horizon": {
        "mpc.HORIZON_DAYS": 5,
        "mpc.HORIZON_DAYS_LONG": 7,
        "simulator.GAMMA": 0.95,
    },

    # ── Stronger Saturday push: more staff anchor on peak day. ─
    "saturday_push": {
        "candidates.DOW_STAFF_ANCHOR.Saturday": 14,
        "candidates.DOW_STAFF_ANCHOR.Friday": 11,
    },

    # ── Discourage marketing: zero out marketing levels. ─
    "no_marketing": {
        "candidates.MARKETING_LEVELS": [0],
        "candidates.DEFAULT_FUTURE_MARKETING_WEEKEND": 0,
    },

    # ── Calibrate twin to match observed avg revenue more aggressively. ─
    "twin_realprice": {
        "simulator.AVG_BASE_PRICE_PER_COVER": 19.50,
        "simulator.AVG_INGREDIENT_COST_PER_COVER": 3.20,
    },

    # ── Heavier walkout aversion: pushes the planner to over-staff a bit. ─
    "walkout_averse": {
        "simulator.WALKOUT_PENALTY_PER_WALKOUT": 6.0,
        "simulator.REP_PENALTY_SCALE": 30.0,
    },

    # ── Combined "best-guess winning recipe" stack. ─
    "stack_v1": {
        "mpc.HORIZON_DAYS": 5,
        "simulator.GAMMA": 0.95,
        "candidates.PRICE_MULTS": [1.05, 1.10, 1.15, 1.18, 1.20],
        "candidates.DEFAULT_FUTURE_PRICE_MULT": 1.10,
        "rules_supply.BUFFER_NORMAL": 1.30,
        "rules_supply.BUFFER_EARLY": 1.55,
        "candidates.DOW_STAFF_ANCHOR.Saturday": 13,
        "candidates.DOW_STAFF_ANCHOR.Friday": 11,
    },
}


def main():
    import json, sys
    if len(sys.argv) < 2 or sys.argv[1] == "list":
        for name, overlay in PRESETS.items():
            print(f"  {name:<20} {len(overlay)} knob(s)")
        return
    name = sys.argv[1]
    if name not in PRESETS:
        print(f"Unknown preset {name!r}; available: {', '.join(PRESETS)}")
        sys.exit(1)
    print(json.dumps(PRESETS[name]))


if __name__ == "__main__":
    main()
