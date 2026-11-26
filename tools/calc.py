#!/usr/bin/env python3
"""Interactive probability calculator — Python fallback for the UI's
methodology block.

Walks one prop at a time: you give it the handful of numbers the queue
is already showing you, it applies the METHODOLOGY.md Week 12 formula
(R-016: EWMA base from the queue, negative-binomial count distribution)
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

# ---------------------------------------------------------------------------
# LIVE (W12) constants — must match METHODOLOGY.md "Week 12" AND ui.html's
# METHODOLOGY block. R-016: my_p = P(X on the queued side of the line) for
# X ~ NegBin(mean = base, size = NB_SIZE[position]). No matchup, pace,
# script or sigma rules. NB_SIZE fit by method of moments on 2023-2025
# slot residuals of the R-016 base, pregame information only
# (plans/R-016-count-dist-ewma.md). Variance = mean + mean^2 / size.
# ---------------------------------------------------------------------------
METH_VER = "W12-R016"
NB_SIZE = {"RB": 12.5, "WR": 26.7}
P_CLAMP = (0.02, 0.98)

# ---------------------------------------------------------------------------
# LEGACY (W4-W11) constants below. Retired from the live formula in W12;
# kept so validate.py's compute_myp() can still reproduce old versions.
# ---------------------------------------------------------------------------
SIGMA = {"rushing": 5.5, "receiving": 2.5}
SHRINK_THRESHOLD = {"rushing": 3.0, "receiving": 1.5}  # retired W9 (R-001 dropped)
SHRINK_FACTOR = 0.5                                   # retired W9 (R-001 dropped)

# R-002 constants. Position-specific EPA-allowed rank: 1 = best defense,
# 32 = worst. Bottom-K defenses get bumped UP (easier matchup → more
# volume on the queued over side). Top-K defenses get bumped DOWN.
MATCHUP_BUMP = {"rushing": 1.0, "receiving": 0.5}
MATCHUP_WORST_K = 8
MATCHUP_BEST_K = 8

# R-005c (W10) upgraded sigma scaling. Bumped from W9's 0.60/0.65 to
# 0.70/0.75 after the W10 backtest showed sigma_cap20 passed strict
# policy (worst +0.0018, mean +0.0031). Still inside PlayerProfiler's
# field-median CV band (RB ~0.5-0.7, WR ~0.6-0.75).
#   - RB floor binds for base < 7.86 car/g; scale binds above.
#   - WR floor binds for base < 3.33 rec/g; scale binds above.
SIGMA_SCALE = {"RB": 0.70, "WR": 0.75}
# Legacy backtest callers that pass a scalar sigma_scale (e.g.,
# validate.py's sweep) still work — compute_myp accepts a float too.


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
                apply_r001: bool = False,
                sigma_scale: float | None = None,
                position: str | None = None) -> dict:
    """LEGACY (W4-W11) formula — NOT the live W12 formula; see
    compute_myp_r016. Kept so validate.py can reproduce old versions.

    Pure form of the Week 9 probability formula. `one_prop` is the
    interactive wrapper; validate.py reuses it to backtest variants.

    Composition (W9):
      - `base` is already composed in slate.py as R-003→R-010→R-008 —
        pass that value here. Backtest variants pass earlier compositions.
      - R-002 adds a position-specific matchup bump.
      - R-005' widens sigma using the position-specific SIGMA_SCALE.
      - R-001 is retired on live methodology; keyword kept for backtest
        callers that still want to toggle the old threshold shrinkage.

    sigma_scale:
      - None (default): live W9 behavior — use SIGMA_SCALE[position];
        defaults to RB value if position missing.
      - 0.0: pre-W7 behavior — fixed SIGMA_floor only.
      - >0 float: explicit override (backtest sweeps).

    Returns {my_p, expected, raw_expected, deviation, r001_fired,
             r002_fired, matchup_adjust, sigma_eff}.
    """
    sigma_base = SIGMA[market]
    if sigma_scale is None:
        # Live W9: per-position SIGMA_SCALE. Default to RB if caller
        # omitted position (safer wider sigma).
        sc = SIGMA_SCALE["WR" if position == "WR" else "RB"]
        sigma_eff = max(sigma_base, base * sc)
    elif sigma_scale > 0:
        sigma_eff = max(sigma_base, base * sigma_scale)
    else:
        sigma_eff = sigma_base
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


def nb_cdf(k: int, mean: float, size: float) -> float:
    """P(X <= k) for X ~ NegBin(mean, size), by the pmf recursion
    p0 = q^size, p_j = p_{j-1} * (j-1+size)/j * (1-q), q = size/(size+mean).
    ui.html's nbCdf is the same loop line for line."""
    if k < 0:
        return 0.0
    mean = max(float(mean), 0.05)
    q = size / (size + mean)
    p = q ** size
    total = p
    for j in range(1, int(k) + 1):
        p *= (j - 1 + size) / j * (1 - q)
        total += p
    return min(1.0, total)


def compute_myp_r016(side: str, base: float, line: float, position: str,
                     expected_override: float | None = None) -> dict:
    """LIVE W12 formula (R-016). `base` is the queue's base_car_r008 /
    base_rec_r008 (the R-016 EWMA base). A finite `expected_override`
    replaces the mean (UI's 'mean override' field)."""
    pos = "RB" if position == "RB" else "WR"
    size = NB_SIZE[pos]
    expected = float(expected_override) if expected_override is not None else float(base)
    expected = max(expected, 0.05)
    p_over = 1.0 - nb_cdf(math.floor(line), expected, size)
    my_p = p_over if side == "over" else 1.0 - p_over
    my_p = max(P_CLAMP[0], min(P_CLAMP[1], my_p))
    sd = math.sqrt(expected + expected * expected / size)
    return {"my_p": my_p, "expected": expected, "p_over": p_over,
            "size": size, "sd": sd}


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
    base = ask("base (R-016 base from queue: base_car_r008 for rushing, "
               "base_rec_r008 for receiving)", float)
    pos = ask("position (RB/WR)", str.upper, choices=("RB", "WR"))
    line = ask("line (FanDuel)", float)

    r = compute_myp_r016(side, base, line, pos)
    print()
    print(f"  X ~ NegBin(mean = {r['expected']:.3f}, size = {r['size']:g})   "
          f"sd = {r['sd']:.3f}")
    print(f"  p_over = P(X > {line:g}) = 1 − F({math.floor(line)}) = {r['p_over']:.4f}")
    print()
    print(f"  →  enter my_p = {r['my_p']:.4f}   (side = {side})")
    print(f"     p_over = {r['p_over']:.4f}, p_under = {1 - r['p_over']:.4f}")
    if abs(r["my_p"] - 0.5) < 0.03:
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
