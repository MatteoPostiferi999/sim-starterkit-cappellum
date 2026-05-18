"""Run det_agent as: python -m agents.det_agent"""

import os
from agents.runner import run_game
from agents.det_agent import strategy

if __name__ == "__main__":
    team = os.getenv("TEAM_NAME", "det-agent")
    scenario = os.getenv("SCENARIO", "baseline")
    seed = int(os.getenv("SEED", "42"))
    url = os.getenv("RESTBENCH_URL", "http://52.48.183.209:8001")
    run_game(strategy, base_url=url, team_name=team, scenario=scenario, seed=seed)
