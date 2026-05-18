"""Shared utility library used by the MPC agent.

Not an agent itself. Contains:
  - state.py      observation parsing
  - memory.py     persistent state in save_notes
  - signals.py    21 signal detectors
  - policies.py   mode decision (NORMAL / DEFENSIVE / etc.)
  - rules_supply.py  8-step order planner
  - rules_safety.py  action validator
  - rules_staff.py / rules_menu.py  defaults (used by MPC fallback path)
  - constants.py  supplier topology, recipes, DOW priors
"""
