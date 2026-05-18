"""det_agent — deterministic-first RestBench agent.

Built from scratch for the Prosus AISO hackathon. Almost every decision is
made by pure-Python predictors and rule-based policies. The LLM is consulted
sparingly, only for scenario interpretation when alerts or anomalies fire.
"""

from .strategy import strategy

__all__ = ["strategy"]
