"""Compare two benchmark runs by run_id.

    python -m agents.auto_research.compare <baseline_run_id> <candidate_run_id>

Prints:
  - overlay diff
  - per-scenario score delta
  - mean / stddev / bankruptcy delta
  - robust_score delta and verdict (ACCEPT / REJECT / NEUTRAL)
"""

from __future__ import annotations

import argparse
import json
import sys

from .benchmark import load_manifest, RUNS_DIR


ACCEPT_THRESHOLD = 100.0   # robust_score delta required to flag ACCEPT
REJECT_THRESHOLD = -100.0  # robust_score delta below which it's REJECT


def _diff_overlay(a: dict, b: dict) -> tuple[dict, dict, dict]:
    """Return (only_in_a, only_in_b, changed_pairs)."""
    only_a, only_b, changed = {}, {}, {}
    keys = set(a) | set(b)
    for k in sorted(keys):
        if k in a and k not in b:
            only_a[k] = a[k]
        elif k in b and k not in a:
            only_b[k] = b[k]
        elif a[k] != b[k]:
            changed[k] = (a[k], b[k])
    return only_a, only_b, changed


def compare(base_id: str, cand_id: str) -> dict:
    a = load_manifest(base_id)
    b = load_manifest(cand_id)

    only_a, only_b, changed = _diff_overlay(a.get("overlay") or {}, b.get("overlay") or {})

    print("=" * 70)
    print(f"COMPARE  {base_id}  →  {cand_id}")
    print("=" * 70)

    if changed or only_a or only_b:
        print("\n  Overlay diff:")
        for k, (av, bv) in changed.items():
            print(f"    {k}: {av!r}  →  {bv!r}")
        for k, v in only_a.items():
            print(f"    {k}: {v!r}  → (removed)")
        for k, v in only_b.items():
            print(f"    {k}: (none)  →  {v!r}")
    else:
        print("\n  Overlay diff: (none — both runs used identical overlays)")

    # Per-scenario
    a_avg = a.get("per_scenario_avg", {})
    b_avg = b.get("per_scenario_avg", {})
    scenarios = sorted(set(a_avg) | set(b_avg))
    print(f"\n  {'scenario':<18} {'baseline':>10} {'candidate':>10} {'delta':>10}")
    print("  " + "-" * 52)
    for s in scenarios:
        av = a_avg.get(s, float("nan"))
        bv = b_avg.get(s, float("nan"))
        d = bv - av
        marker = "▲" if d > 0 else ("▼" if d < 0 else " ")
        print(f"  {s:<18} {av:>10.0f} {bv:>10.0f} {d:>+10.0f} {marker}")

    # Summary
    print()
    print(f"  {'metric':<16} {'baseline':>12} {'candidate':>12} {'delta':>12}")
    print("  " + "-" * 56)
    for label, key in [
        ("mean",          "mean_score"),
        ("min",           "min_score"),
        ("max",           "max_score"),
        ("stddev",        "stddev_score"),
        ("bankruptcies",  "bankruptcies"),
        ("errors",        "errors"),
        ("robust",        "robust_score"),
    ]:
        av = a.get(key, 0)
        bv = b.get(key, 0)
        d = bv - av
        print(f"  {label:<16} {av:>12.0f} {bv:>12.0f} {d:>+12.0f}")

    delta = b["robust_score"] - a["robust_score"]
    if delta > ACCEPT_THRESHOLD:
        verdict = "ACCEPT"
        verdict_msg = f"candidate beats baseline by {delta:.0f} robust points"
    elif delta < REJECT_THRESHOLD:
        verdict = "REJECT"
        verdict_msg = f"candidate is worse by {abs(delta):.0f} robust points"
    else:
        verdict = "NEUTRAL"
        verdict_msg = f"within noise (|Δ|={abs(delta):.0f} < {ACCEPT_THRESHOLD:.0f})"
    print()
    print(f"  VERDICT: {verdict}  ({verdict_msg})")
    print("=" * 70)
    return {"verdict": verdict, "delta": delta, "baseline": a, "candidate": b}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("baseline", help="run_id of the baseline")
    p.add_argument("candidate", help="run_id of the candidate")
    args = p.parse_args()
    compare(args.baseline, args.candidate)


if __name__ == "__main__":
    main()
