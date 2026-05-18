"""Named, numbered version checkpoints.

Each *version* is an overlay + the run that produced it, stored at
`auto_research_runs/versions/v<N>_<slug>.json`. The harness auto-saves a new
version whenever the tuner commits an improvement; you can also save manually:

    python -m agents.auto_research.versions save <run_id> --label "premium pricing only"
    python -m agents.auto_research.versions list
    python -m agents.auto_research.versions show v3
    python -m agents.auto_research.versions run v3
    python -m agents.auto_research.versions diff v2 v5

The versions index (`versions.json`) is a sorted list of:
    {
      "name": "v3",
      "label": "premium-pricing-only",
      "run_id": "run_177...",
      "overlay": {...},
      "robust_score": ...,
      "mean_score": ...,
      "per_scenario_avg": {...},
      "timestamp": "...",
      "parent": "v2" | null,
    }

Versions never overwrite each other. Re-running an already-recorded overlay
creates a new version row pointing at the new run_id.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from .benchmark import RUNS_DIR, load_manifest, run_benchmark, print_summary


VERSIONS_DIR = RUNS_DIR / "versions"
INDEX_PATH = VERSIONS_DIR / "index.json"


def _slug(label: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (label or "").strip()).strip("-").lower()
    return s[:40] or "unnamed"


def _read_index() -> list[dict]:
    if not INDEX_PATH.exists():
        return []
    with open(INDEX_PATH) as f:
        return json.load(f)


def _write_index(idx: list[dict]) -> None:
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    with open(INDEX_PATH, "w") as f:
        json.dump(idx, f, indent=2, default=str)


def _next_name(idx: list[dict]) -> str:
    nums = []
    for v in idx:
        m = re.match(r"^v(\d+)$", v["name"])
        if m:
            nums.append(int(m.group(1)))
    return f"v{(max(nums) if nums else 0) + 1}"


def _find(name: str, idx: list[dict] | None = None) -> dict | None:
    idx = idx or _read_index()
    for v in idx:
        if v["name"] == name or v.get("slug") == name or v.get("run_id") == name:
            return v
    return None


def save_version(
    run_id: str,
    *,
    label: str = "",
    parent: str | None = None,
) -> dict:
    """Snapshot a run_id as a new version. Returns the version dict."""
    manifest = load_manifest(run_id)
    idx = _read_index()
    name = _next_name(idx)
    slug = _slug(label) if label else _slug(name)
    version = {
        "name": name,
        "slug": slug,
        "label": label,
        "run_id": run_id,
        "overlay": manifest.get("overlay") or {},
        "robust_score": manifest.get("robust_score"),
        "mean_score": manifest.get("mean_score"),
        "min_score": manifest.get("min_score"),
        "max_score": manifest.get("max_score"),
        "stddev_score": manifest.get("stddev_score"),
        "bankruptcies": manifest.get("bankruptcies"),
        "per_scenario_avg": manifest.get("per_scenario_avg"),
        "scenarios": manifest.get("scenarios"),
        "seeds": manifest.get("seeds"),
        "git": manifest.get("git_marker"),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "parent": parent,
    }
    idx.append(version)
    _write_index(idx)

    # Also write a standalone JSON for easy diffing/sharing
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    with open(VERSIONS_DIR / f"{name}_{slug}.json", "w") as f:
        json.dump(version, f, indent=2, default=str)
    return version


def list_versions() -> list[dict]:
    return _read_index()


def show_version(name: str) -> dict | None:
    v = _find(name)
    if not v:
        return None
    return v


def run_version(name: str, **bench_kwargs: Any) -> dict:
    """Re-run a stored version's overlay against the live server."""
    v = _find(name)
    if not v:
        raise KeyError(f"unknown version {name!r}")
    overlay = v.get("overlay") or {}
    label = bench_kwargs.pop("label", f"replay {name} ({v.get('label','')})")
    res = run_benchmark(overlay=overlay, label=label, **bench_kwargs)
    return {"version": v, "rerun": res}


def diff_versions(a_name: str, b_name: str) -> dict:
    a = _find(a_name)
    b = _find(b_name)
    if not a or not b:
        raise KeyError("unknown version")
    only_a, only_b, changed = {}, {}, {}
    ao = a.get("overlay") or {}
    bo = b.get("overlay") or {}
    for k in sorted(set(ao) | set(bo)):
        if k in ao and k not in bo:
            only_a[k] = ao[k]
        elif k in bo and k not in ao:
            only_b[k] = bo[k]
        elif ao[k] != bo[k]:
            changed[k] = (ao[k], bo[k])
    return {"only_in_a": only_a, "only_in_b": only_b, "changed": changed,
            "delta_robust": (b.get("robust_score") or 0) - (a.get("robust_score") or 0),
            "delta_mean": (b.get("mean_score") or 0) - (a.get("mean_score") or 0)}


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("(no versions yet)")
        return
    print(f"{'name':<6}  {'mean':>9} {'robust':>9} {'bk':>3}  {'label':<30}  run_id")
    print("─" * 90)
    for r in rows:
        mean = r.get("mean_score") or 0
        robust = r.get("robust_score") or 0
        bk = r.get("bankruptcies") or 0
        print(f"{r['name']:<6}  {mean:>9.0f} {robust:>9.0f} {bk:>3}  "
              f"{(r.get('label') or '')[:30]:<30}  {r.get('run_id','')[:24]}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List all versions.")
    p_show = sub.add_parser("show", help="Show one version's overlay + scores.")
    p_show.add_argument("name")
    p_save = sub.add_parser("save", help="Snapshot a run as a version.")
    p_save.add_argument("run_id")
    p_save.add_argument("--label", default="")
    p_save.add_argument("--parent", default=None)
    p_run = sub.add_parser("run", help="Re-run a version against the live server.")
    p_run.add_argument("name")
    p_run.add_argument("--scenarios", default="")
    p_run.add_argument("--seeds", default="")
    p_run.add_argument("--smoke", action="store_true")
    p_run.add_argument("--single", action="store_true")
    p_run.add_argument("--parallel", type=int, default=5)
    p_diff = sub.add_parser("diff", help="Diff two versions.")
    p_diff.add_argument("a")
    p_diff.add_argument("b")
    p_best = sub.add_parser("best", help="Show the highest-robust version.")

    args = p.parse_args()

    if args.cmd == "list":
        _print_table(list_versions())
    elif args.cmd == "show":
        v = show_version(args.name)
        if not v:
            print(f"no version {args.name!r}")
            return
        print(json.dumps(v, indent=2, default=str))
    elif args.cmd == "save":
        v = save_version(args.run_id, label=args.label, parent=args.parent)
        print(f"Saved {v['name']} ({v['slug']}) — mean={v['mean_score']:.0f} robust={v['robust_score']:.0f}")
    elif args.cmd == "run":
        from .benchmark import DEFAULT_SCENARIOS, DEFAULT_SEEDS, DEFAULT_SMOKE_SEEDS
        scenarios = args.scenarios.split(",") if args.scenarios else DEFAULT_SCENARIOS
        if args.single:
            scenarios, seeds = ["baseline"], [42]
        elif args.smoke:
            seeds = DEFAULT_SMOKE_SEEDS
        else:
            seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else DEFAULT_SEEDS
        out = run_version(args.name, scenarios=scenarios, seeds=seeds, parallel=args.parallel, quiet=True)
        print_summary(out["rerun"])
    elif args.cmd == "diff":
        d = diff_versions(args.a, args.b)
        print(f"Diff {args.a} → {args.b}")
        print(f"  Δrobust = {d['delta_robust']:+.0f}")
        print(f"  Δmean   = {d['delta_mean']:+.0f}")
        if d["changed"]:
            print("  changed:")
            for k, (av, bv) in d["changed"].items():
                print(f"    {k}: {av!r}  →  {bv!r}")
        if d["only_in_a"]:
            print("  removed:")
            for k, v in d["only_in_a"].items():
                print(f"    {k} = {v!r}")
        if d["only_in_b"]:
            print("  added:")
            for k, v in d["only_in_b"].items():
                print(f"    {k} = {v!r}")
    elif args.cmd == "best":
        vs = list_versions()
        if not vs:
            print("(none)")
            return
        best = max(vs, key=lambda v: (v.get("robust_score") or float("-inf")))
        print(json.dumps(best, indent=2, default=str))


if __name__ == "__main__":
    main()
