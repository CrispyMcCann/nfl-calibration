# Plan — R-016 count distribution + EWMA base (for week 12)

Status: **pre-declared, not built.** Written 2026-11-26 (Thanksgiving,
week 12), before the fair backtest below exists. Don't change the
candidates, metric or ship rule after seeing results; add a new dated
section instead.

## Disclosure — what was already seen

This plan is not blind. Before writing it, an exploratory scratch
backtest (simplified base, not the live stack; 2023-2025, 4,281
player-weeks) and a read of the live W9-W11 rows produced:

- z-score SD under R-005c sigma: 0.62 RB / 0.72 WR (backtest),
  0.63 / 0.79 (live W10-11). Sigma is ~30-40 % too wide.
- NegBin (size r fit on 2023-24: 8.7 RB, 14.4 WR) beat R-005c normal
  on 2025 at most line offsets: avg Brier RB 0.2239 vs 0.2294,
  WR 0.1585 vs 0.1645.
- EWMA base beat a trailing-3 + R-010cap approximation by ~2 % RMSE
  in every season (RB hl 3-4; WR hl 4 + 3 prior pseudo-games).
- On the 85 live W9-11 props, NegBin around the live formula mean
  scored **worse** (0.2637 vs 0.2552). Recorded here so the write-up
  can't forget it.

Those numbers chose the candidates. The test below is the fair one:
the real live W11 chain as the baseline, dispersion fit on 2023-24
only, 2025 held out as confirmation.

## Owner constraint

The FanDuel line is never an input to the formula. The model has to
stand on its own; the goal is to beat the line, not copy it.

## Why the current harness can't see this

`validate.py --mode brier` scores one synthetic line: the player's
trailing median, rounded to a half point. That line sits next to the
model's mean, the one place where a too-wide sigma costs nothing.
Live lines differ from our mean by a few units. R-005c (0.70 / 0.75)
was chosen on that proxy after the W9 Swift/Allgeier misses. R-016
scores across a band of lines instead.

## Candidates

**Mean (base before R-002).** Selected on 2023-24 from this grid only:

| id | definition |
|---|---|
| M0 | live W11 chain: R-003 → R-010 (R-014 prior) → cap 20 % → R-011 net R-008 |
| M1 | EWMA of all games played this season, half-life `h` games |
| M1+P | M1 shrunk toward prior-season mean with `k` pseudo-games (none if no prior ≥ 5 games) |
| +inj | add the live R-003 elevation delta and subtract the R-008 deflation delta to the EWMA |

Grid: `h ∈ {3, 4}`, `k ∈ {0, 3}`, `inj ∈ {off, on}`, per position.

**Matchup.** R-002 on / off, on whichever base wins.

**Distribution.**

| id | definition |
|---|---|
| D0 | live: Normal, σ = max(floor, base × 0.70 RB / 0.75 WR) |
| D1 | Negative binomial, mean = expected, size `r` per position fit by method of moments on 2023-24 residuals of the chosen mean |
| D2 | Normal, constant σ per position = 2023-24 residual RMSE of the chosen mean |

Clamp stays [0.02, 0.98].

## Metric

- **Primary:** mean Brier over seven half-point lines at proxy-line
  offsets −3 … +3, per slot. Same slots as `--mode brier` (slate
  selection, `primary_at`, all four roles, weeks 4+).
- **Secondary:** log score of the exact count (normal discretised at
  ±0.5).

## Ship rule

Fit and select on 2023-24. Then, on 2025 (held out):

1. The chosen combo ships if its primary metric beats M0+D0 on 2025
   **and** on each of 2023 and 2024.
2. If the combo fails, each component swap (mean alone, distribution
   alone, R-002 removal alone) is judged by the same rule against
   M0+D0, and only passing swaps ship.
3. The live W9-11 rows are reported, not a gate (about 40 games; the
   standard error of a Brier difference there is larger than the
   effects in question). The game-clustered bootstrap CI is reported
   with them.

If nothing passes, nothing ships and W11 runs on.

## If it ships

- `nflcal/data.py` / `slate.py` compute the new base; it is written to
  the existing `base_rec_r008` / `base_car_r008` formula-input fields
  (precedent: R-011, R-014), audited in `r010_notes`. No schema change.
- METHODOLOGY.md, `ui.html` (plus the live artifact), and
  `tools/calc.py` change in one commit; `METH_VER` → `W12-R016…`.
- NegBin in JS uses the pmf recursion
  `p0 = q^r`, `p_k = p_{k-1} · (k-1+r)/k · (1-q)`, `q = r/(r+μ)`,
  so no gamma function is needed.

## 2026-11-26 — owner override of the ship rule

Written before the fair backtest finished. The owner decided to replace
the methodology for week 12 regardless of whether the ship rule above
passes: "lets go through our full methodology, tear down the faulty
parts, and replace with a new researched methodology … I'm the one
writing the rules."

Consequences for the write-up:

- The W12 methodology is **chosen by evidence** (the R-016 backtest
  tables) but **not gated by a pre-declared test**. The ship rule
  above is reported as passed or failed, but it did not decide.
- The W12 version band therefore can't be presented as a
  pre-registered improvement. It is a new method, scored prospectively
  from week 12 on, like every earlier version change.
- Unaffected: the selection filter, prop set, analysis plan, and every
  logged row (CLAUDE.md rules 1-5). Only how `my_p` is formed changes.

## 2026-11-26 — results and what shipped

Full tables in METHODOLOGY.md, "Between Week 11 and Week 12". Summary:

- **Pre-declared ship rule (look-ahead data, as written above):** the
  pre-declared combo M1* + D1 beat M0 + D0 in all three seasons
  (+0.0019 / +0.0042 / +0.0053), and so did D1 alone (+0.0042 /
  +0.0016 / +0.0024). The rule passed. The owner override above means
  it did not decide.
- **Departures from the plan, made after seeing results, disclosed:**
  1. Added `--pregame-info` (no game-day inactives) after finding the
     look-ahead in `_effectively_out` / R-008. Final choices use it.
  2. Judged by pooled 2023-25 with game-clustered CIs instead of the
     train/hold split. The split was too noisy to separate rules.
  3. Table 1's selection dropped M0 by construction. For RB, M0 beat
     every M1 variant on train. The audit found plain EWMA h=3 ties
     the full chain for RB, so EWMA shipped for RB on simplicity and
     calibration, not on a Brier win.
  4. R-002 removal was not in the plan as a standalone test; the
     ablation showed no signal for it in any configuration.
- **Shipped as W12-R016:** RB EWMA h=3, no prior; WR EWMA h=3 + prior
  3 pseudo-games; NegBin size 12.5 RB / 26.7 WR (fit 2023-25 pregame);
  no R-002, R-003, R-008, R-010, R-010cap, R-011, R-014, R-005c.
