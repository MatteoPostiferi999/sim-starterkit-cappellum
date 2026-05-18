"""AutoResearchYC-style improvement harness for the MPC restaurant agent.

A small toolkit that:
  - applies a *non-invasive* overlay of hyperparameter mutations to the MPC
    agent (the base code is never modified),
  - runs `agents.evaluate.evaluate` against the live server with that overlay,
  - logs every game to a JSONL file with a stable run_id,
  - computes a single robust_score that penalises bankruptcies and variance,
  - lets you compare two runs by run_id,
  - and (optionally) drives an automated coordinate-descent tuner over the
    search space defined in `search_space.py`.

Inspired by https://github.com/FlorisFok/AutoResearchYC. The MPC agent is the
read-only "system under test"; only `tuning.py`'s overlay can mutate behaviour.
"""

from .tuning import apply_overlay, restore, with_overlay  # noqa: F401

# Optional re-exports — guarded so a partially-built tree still imports.
try:
    from .benchmark import run_benchmark, RunResult  # noqa: F401
except Exception:  # pragma: no cover
    pass

__all__ = [
    "apply_overlay",
    "restore",
    "with_overlay",
    "run_benchmark",
    "RunResult",
]
