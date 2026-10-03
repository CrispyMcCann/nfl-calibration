#!/usr/bin/env python3
"""Interactive probability calculator for the weekly logging flow.

Walks one prop at a time: you give it the handful of numbers the queue
is already showing you (base, pace, line, side, market), it applies the
METHODOLOGY.md Week 4 formula including R-001 shrinkage, and prints the
my_p you then type into the UI. Loops until you Ctrl-C.

    python tools/calc.py

The formula and constants in this file must match METHODOLOGY.md. If
one changes, update the other in the same commit.
"""
from __future__ import annotations
import math
import sys

# Constants — must match METHODOLOGY.md Week 4
SIGMA = {"rushing": 5.5, "receiving": 2.5}
SHRINK_THRESHOLD = {"rushing": 3.0, "receiving": 1.5}
SHRINK_FACTOR = 0.5


def phi(z: float) -> float:
    """Standard normal CDF via math.erf (no scipy dependency)."""
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def ask(label: str, caster=str, default=None, choices=None) -> object:
    """Prompt until the user gives a valid value. EOF exits the program."""
    while True:
        prompt = f"  {label}"
        if default is not None:
            prompt += f" [{default}]"
        prompt += ": "
        try:
            raw = input(prompt).strip()
        except EOFError:
            print()
            sys.exit(0)
        if not raw and default is not None:
            return default
        try:
            v = caster(raw)
        except Exception:
            print("    ...can't parse that, try again")
            continue
        if choices and v not in choices:
            print(f"    ...pick one of {choices}")
            continue
        return v


def one_prop() -> None:
    print("\n" + "─" * 60)
    market = ask("market (rushing/receiving)", str.lower,
                 choices=("rushing", "receiving", "r", "rec"))
    market = "rushing" if market in ("rushing", "r") else "receiving"
    side = ask("side (over/under)", str.lower, choices=("over", "under"))
    base = ask("base (trailing 3-game avg from queue)", float)
    pace = ask("pace (team plays/game from queue)", float)
    usage_factor = ask("usage_factor", float, default=1.0)
    matchup_adjust = ask("matchup_adjust", float, default=0.0)
    line = ask("line (FanDuel)", float)

    sigma = SIGMA[market]
    threshold = SHRINK_THRESHOLD[market]

    raw_expected = base * (pace / 60.0) * usage_factor + matchup_adjust
    deviation = raw_expected - base
    r001_fired = abs(deviation) > threshold
    expected = (base + SHRINK_FACTOR * deviation) if r001_fired else raw_expected

    z = (line - expected) / sigma
    p_over = 1 - phi(z)
    my_p = p_over if side == "over" else 1 - p_over
    my_p = max(0.02, min(0.98, my_p))  # clamp per methodology

    print()
    print(f"  raw_expected = {base:.2f} × ({pace:.1f}/60) × {usage_factor:.2f}"
          f" + {matchup_adjust:+.2f}  =  {raw_expected:.3f}")
    print(f"  deviation    = {deviation:+.3f}   "
          f"(threshold ±{threshold:.1f} for {market})")
    if r001_fired:
        print(f"  R-001 fires  → expected = {base:.2f} + 0.5×{deviation:+.3f}"
              f" = {expected:.3f}")
    else:
        print(f"  R-001 skipped → expected = raw_expected = {expected:.3f}")
    print(f"  z            = ({line:.1f} − {expected:.3f}) / {sigma} = {z:+.3f}")
    print()
    print(f"  →  enter my_p = {my_p:.4f}   (side = {side})")
    print(f"     p_over = {p_over:.4f}, p_under = {1 - p_over:.4f}")
    if abs(my_p - 0.5) < 0.03:
        print("     (near coin-flip — no edge in either direction)")
    print("─" * 60)


def main() -> None:
    print("NFL calibration prop probability calculator")
    print("Enter one prop at a time. Ctrl-C to exit.")
    try:
        while True:
            try:
                one_prop()
            except (ValueError, KeyboardInterrupt):
                raise
            except Exception as e:
                print(f"  error: {e}")
    except KeyboardInterrupt:
        print("\n\nbye")


if __name__ == "__main__":
    main()
