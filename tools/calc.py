#!/usr/bin/env python3
"""Interactive probability calculator — Python fallback for the UI's
methodology block.

Walks one prop at a time: you give it the handful of numbers the queue
is already showing you, it applies the METHODOLOGY.md Week 6 formula
(R-003 base elevation, R-002 position-specific matchup, R-001 shrinkage)
and prints the my_p you can type into the UI. Loops until Ctrl-C or EOF.

    python tools/calc.py

=============================================================================
SYNC CONTRACT. This file is one of three identical statements of the
weekly probability formula. The other two:

  1. METHODOLOGY.md     — the authoritative description
  2. ui.html            — the JS block headed `=== METHODOLOGY :: <ver> ==`
                          inside <script>. METH_VER there is the version
                          string written to each row's methodology_version
                          column.
  3. this file          — the Python fallback

Any change here MUST also change METHODOLOGY.md and ui.html in the same
commit, and bump the version tag (`METH_VER` in ui.html, "Week N"
heading plus the rule registry here). Rows logged under the old tag
keep it; new rows get the new tag. CLAUDE.md's "Methodology sync
contract" has the full rule.
=============================================================================
"""
from __future__ import annotations
import math
import sys

# Constants — must match METHODOLOGY.md Week 6 AND ui.html's METHODOLOGY block.
METH_VER = "W6-R001-R002-R003"
SIGMA = {"rushing": 5.5, "receiving": 2.5}
SHRINK_THRESHOLD = {"rushing": 3.0, "receiving": 1.5}
SHRINK_FACTOR = 0.5

# R-002 constants. Position-specific EPA-allowed rank: 1 = best defense,
# 32 = worst. Bottom-K defenses get bumped UP (easier matchup → more
# volume on the queued over side). Top-K defenses get bumped DOWN.
MATCHUP_BUMP = {"rushing": 1.0, "receiving": 0.5}
MATCHUP_WORST_K = 8
MATCHUP_BEST_K = 8


def phi(z: float) -> float:
    """Standard normal CDF via math.erf (no scipy dependency)."""
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def matchup_adjust_r002(market: str,
                        pass_rank_opp: int | None,
                        rush_rank_opp: int | None) -> float:
    """R-002: additive bump based on the opposing defense's position-
    specific EPA-allowed rank. Receiving props read pass_rank_opp;
    rushing props read rush_rank_opp."""
    rank = rush_rank_opp if market == "rushing" else pass_rank_opp
    if rank is None:
        return 0.0
    bump = MATCHUP_BUMP[market]
    if rank >= 33 - MATCHUP_WORST_K:  # 25..32  → bottom-8 defenses
        return +bump
    if rank <= MATCHUP_BEST_K:        # 1..8    → top-8 defenses
        return -bump
    return 0.0


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


def _opt_int(raw: str) -> int | None:
    """Caster: blank → None, else int."""
    s = raw.strip().lower()
    if s in ("", "-", "none", "na"):
        return None
    return int(s)


def one_prop() -> None:
    print("\n" + "─" * 60)
    market = ask("market (rushing/receiving)", str.lower,
                 choices=("rushing", "receiving", "r", "rec"))
    market = "rushing" if market in ("rushing", "r") else "receiving"
    side = ask("side (over/under)", str.lower, choices=("over", "under"))
    base = ask("base (R-003 elevated base from queue: "
               "base_car_r003 for rushing, base_rec_r003 for receiving)", float)
    pace = ask("pace (team plays/game from queue)", float)
    usage_factor = ask("usage_factor", float, default=1.0)
    pass_rank_opp = ask("pass_def_rank_opp (1=best D, blank if unknown)",
                        _opt_int, default=None)
    rush_rank_opp = ask("rush_def_rank_opp (1=best D, blank if unknown)",
                        _opt_int, default=None)
    line = ask("line (FanDuel)", float)

    sigma = SIGMA[market]
    threshold = SHRINK_THRESHOLD[market]

    # Composition: R-003 is already baked into `base` (user typed the
    # elevated value from the queue). R-002 populates matchup_adjust.
    # Formula then runs, and R-001 shrinks against the (fresh) base.
    matchup_adjust = matchup_adjust_r002(market, pass_rank_opp, rush_rank_opp)
    raw_expected = base * (pace / 60.0) * usage_factor + matchup_adjust
    deviation = raw_expected - base
    r001_fired = abs(deviation) > threshold
    expected = (base + SHRINK_FACTOR * deviation) if r001_fired else raw_expected

    z = (line - expected) / sigma
    p_over = 1 - phi(z)
    my_p = p_over if side == "over" else 1 - p_over
    my_p = max(0.02, min(0.98, my_p))  # clamp per methodology

    print()
    r002_tag = ""
    if matchup_adjust != 0:
        rk = rush_rank_opp if market == "rushing" else pass_rank_opp
        r002_tag = f"  R-002 fires  → matchup_adjust = {matchup_adjust:+.2f} (opp pos-rank {rk})"
    else:
        r002_tag = "  R-002 skipped → matchup_adjust = 0.00"
    print(r002_tag)
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
    print(f"NFL calibration prop probability calculator ({METH_VER})")
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
