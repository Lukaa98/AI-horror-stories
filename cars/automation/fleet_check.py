"""Replay every finished build's script through the current house rules.

A change to the rules used to be validated against whichever car happened
to be on screen -- which is how "fixed" kept turning into "the next one
broke differently". This replays a directory of result.json manifests and
prints how often each rule fails across all of them, so a change is judged
on the fleet instead of on one Porsche.

    python cars/automation/fleet_check.py <dir-of-result.json> [--repair]

--repair also applies _repair_script to each manifest and reports what the
deterministic fixes would have caught, without touching anything on disk.
"""
import argparse
import json
from collections import Counter
from pathlib import Path

from single_car_short import WORD_CAP, _repair_script, _script_violations

RULE_LABELS = (
    ("no number", "hook has no number"),
    ("names the car", "hook names the car"),
    ("not end on a question", "no closing question"),
    ("not until after scene", "horsepower late"),
    ("never states the car's horsepower", "horsepower missing"),
    ("never states the car's torque", "torque missing"),
    ("banned phrase", "banned phrase"),
    ("never says", "comparison hp not spoken"),
    ("expresses a value", "filler tail"),
    ("what people actually argue", "no reputation beat"),
)


def failures(package, make, model):
    """Every rule this build breaks, as short stable labels."""
    found = set()
    for violation in _script_violations(package, make, model):
        for needle, label in RULE_LABELS:
            if needle in violation:
                found.add(label)
                break
    if (package.get("word_count") or 0) > WORD_CAP:
        found.add(f"over {WORD_CAP} words")
    if not (package.get("key_specs") or {}):
        found.add("no key_specs")
    if not any(scene.get("rival_make") for scene in package.get("scenes") or []):
        found.add("no rival scene")
    return found


def load_builds(root):
    builds = []
    for path in sorted(Path(root).rglob("result.json")) or sorted(Path(root).glob("*.json")):
        try:
            package = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if package.get("scenes"):
            car = package.get("car") or {}
            builds.append((path.name, package, car.get("make", ""), car.get("model", "")))
    return builds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="Directory of finished builds (searched recursively).")
    parser.add_argument("--repair", action="store_true",
                        help="Also report what _repair_script would fix.")
    parser.add_argument("--list", action="store_true", help="Print each build's failures.")
    args = parser.parse_args()

    builds = load_builds(args.root)
    if not builds:
        print(f"No manifests with scenes found under {args.root}")
        return
    before, after = Counter(), Counter()
    for name, package, make, model in builds:
        found = failures(package, make, model)
        for label in found:
            before[label] += 1
        if args.repair:
            repaired = failures(_repair_script(json.loads(json.dumps(package)), make, model), make, model)
            for label in repaired:
                after[label] += 1
        if args.list:
            print(f"  {name[:52]:52s} {package.get('word_count'):>4}  {', '.join(sorted(found)) or 'clean'}")

    total = len(builds)
    print(f"\n{total} builds")
    header = f"\n  {'rule':24s} {'fails':>6s}" + ("  after repair" if args.repair else "")
    print(header)
    for label, count in before.most_common():
        line = f"  {label:24s} {count:3d}/{total}"
        if args.repair:
            line += f"      {after[label]:3d}/{total}"
        print(line)
    clean = sum(1 for _n, p, mk, md in builds if not failures(p, mk, md))
    print(f"\n  clean builds: {clean}/{total}")
if __name__ == "__main__":
    main()
