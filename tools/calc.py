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

# Constants — must match METHODOLOGY.md Week 7 AND ui.html's METHODOLOGY block.
METH_VER = "W7-R001-R002-R003b-R005-R006"
SIGMA = {"rushing": 5.5, "receiving": 2.5}
SHRINK_THRESHOLD = {"rushing": 3.0, "receiving": 1.5}
SHRINK_FACTOR = 0.5

# R-002 constants. Position-specific EPA-allowed rank: 1 = best defense,
# 32 = worst. Bottom-K defenses get bumped UP (easier matchup → more
# volume on the queued over side). Top-K defenses get bumped DOWN.
MATCHUP_BUMP = {"rushing": 1.0, "receiving": 0.5}
MATCHUP_WORST_K = 8
MATCHUP_BEST_K = 8

# R-005: variable-sigma scaling. sigma_eff = max(SIGMA[market], base × k).
# For a slot WR (base ~5), the SIGMA floor binds. For a WR1 on a hot
# stretch (base ~13), sigma scales up (13 × 0.30 = 3.9), which widens the
# distribution and softens the formula's over-confidence on high-base
# props — the Nacua Week 6 class of case. Value chosen from first-principle
# empirical variance/base ratio across the backtested seasons.
SIGMA_SCALE = 0.30


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


def compute_myp(market: str, side: str, base: float, pace: float,
                line: float, pass_rank_opp: int | None = None,
                rush_rank_opp: int | None = None,
                usage_factor: float = 1.0,
                apply_r002: bool = True,
                apply_r001: bool = True,
                sigma_scale: float = 0.0) -> dict:
    """Pure form of the Week 6 probability formula. `one_prop` is the
    interactive wrapper around this; validate.py's --mode brier reuses it
    to backtest variants (apply_r001/apply_r002 off = earlier methodologies;
    sigma_scale > 0 = R-005 variable-sigma candidate).

    R-003 is applied in slate.py BEFORE the formula runs — pass the
    already-elevated base here. For backtesting a baseline that doesn't
    include R-003, pass the raw base. The function has no notion of
    which rule produced `base`.

    sigma_scale (R-005 candidate, not codified in live methodology):
      0.0 → sigma = SIGMA[market] (current behavior)
      >0  → sigma = max(SIGMA[market], base × sigma_scale). Floor binds for
            low-base props; scale binds for high-base ones. The backtest
            uses this; live calc/UI do not until codification.

    Returns {my_p, expected, raw_expected, deviation, r001_fired,
             r002_fired, matchup_adjust, sigma_eff}.
    """
    sigma_base = SIGMA[market]
    sigma_eff = max(sigma_base, base * sigma_scale) if sigma_scale > 0 else sigma_base
    threshold = SHRINK_THRESHOLD[market]
    matchup_adjust = matchup_adjust_r002(market, pass_rank_opp, rush_rank_opp) \
        if apply_r002 else 0.0
    raw_expected = base * (pace / 60.0) * usage_factor + matchup_adjust
    deviation = raw_expected - base
    r001_fired = apply_r001 and abs(deviation) > threshold
    expected = (base + SHRINK_FACTOR * deviation) if r001_fired else raw_expected
    z = (line - expected) / sigma_eff
    p_over = 1 - phi(z)
    my_p = p_over if side == "over" else 1 - p_over
    my_p = max(0.02, min(0.98, my_p))
    return {
        "my_p": my_p,
        "expected": expected,
        "raw_expected": raw_expected,
        "deviation": deviation,
        "r001_fired": r001_fired,
        "r002_fired": matchup_adjust != 0,
        "matchup_adjust": matchup_adjust,
        "sigma_eff": sigma_eff,
    }


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
    base = ask("base (post-R-003 post-R-006 base from queue: "
               "base_car_r006 for rushing, base_rec_r006 for receiving)", float)
    pace = ask("pace (team plays/game from queue)", float)
    usage_factor = ask("usage_factor", float, default=1.0)
    pass_rank_opp = ask("pass_def_rank_opp (1=best D, blank if unknown)",
                        _opt_int, default=None)
    rush_rank_opp = ask("rush_def_rank_opp (1=best D, blank if unknown)",
                        _opt_int, default=None)
    line = ask("line (FanDuel)", float)

    sigma = result["sigma_eff"]
    threshold = SHRINK_THRESHOLD[market]

    # Composition: R-003 is already baked into `base` (user typed the
    # queue's base_*_r006 value, which is R-003 then R-006 blended).
    # R-002 populates matchup_adjust. Formula then runs with R-005's
    # sigma scaling, and R-001 shrinks against the (fresh) base.
    result = compute_myp(market, side, base, pace, line,
                         pass_rank_opp=pass_rank_opp,
                         rush_rank_opp=rush_rank_opp,
                         usage_factor=usage_factor,
                         sigma_scale=SIGMA_SCALE)
    matchup_adjust = result["matchup_adjust"]
    raw_expected = result["raw_expected"]
    deviation = result["deviation"]
    r001_fired = result["r001_fired"]
    expected = result["expected"]
    my_p = result["my_p"]
    z = (line - expected) / sigma
    p_over = 1 - phi(z)

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
