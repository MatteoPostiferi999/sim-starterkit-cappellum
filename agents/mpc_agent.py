"""Thin entry point for the MPC agent.

Usage:
    RESTBENCH_URL=http://52.48.183.209:8001 python -m agents.mpc_agent
    python -m agents.evaluate agents.mpc_agent

Why MPC?
    Standard agents use rules-of-thumb OR ask an LLM. We tried both — LLMs hurt
    scores in calibration. Instead this agent uses Model-Predictive Control: a
    Python digital twin of the restaurant predicts the outcome of each candidate
    action set, we simulate 3 days forward for ~60 candidates per turn, and we
    submit whichever scores highest. The same technique self-driving cars use.
"""

from __future__ import annotations

import os
import sys

from agents.mpc import strategy  # noqa: F401  re-export for evaluate.py
from agents.runner import run_game


def main() -> None:
    team_name = os.getenv("TEAM_NAME", "mpc_digital_twin")
    scenario = os.getenv("SCENARIO", "baseline")
    seed = int(os.getenv("SEED", "42"))

    result = run_game(strategy, team_name=team_name, scenario=scenario, seed=seed)
    if result and "score" in result:
        s = result["score"]
        print(f"\n{'=' * 50}")
        print(f"FINAL: total={s['total_score']:.0f}, profit={s['net_profit']:.0f}, "
              f"days={result['days_survived']}, status={result['status']}")
        print(f"  rep_pen={s.get('reputation_penalty', 0):.0f}, "
              f"sat_pen={s.get('satisfaction_penalty', 0):.0f}, "
              f"walk_pen={s.get('walkout_penalty', 0):.0f}, "
              f"waste_pen={s.get('waste_penalty', 0):.0f}")


if __name__ == "__main__":
    main()
