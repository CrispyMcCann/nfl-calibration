# Plan — R-012 game-script adjustment (spread / implied total in the formula)

Status: **draft, not shipped.** Written 2026-11-14 (Saturday of week 10).
Read this whole file before writing code. It is meant to be picked up
cold by a new session.

## Why

The study's mechanism is game script: the team expected to trail throws
more, the team expected to lead runs more. Today the spread and implied
total are used only to **select** games (`slate.build_slate`). The
probability formula never sees them:

```
expected = base × (pace/60) × usage_factor + matchup_adjust_r002
```

So two leading RBs with the same trailing base get the same expected
carries whether their team is a 1.5- or a 10-point favorite. R-012 puts
the game-script expectation into `expected`.

## Facts a new session needs

- Formula lives in three synced places: `METHODOLOGY.md`, the
  `/* === METHODOLOGY :: <ver>` block in `ui.html`, and
  `tools/calc.py::compute_myp`. Change all three in one commit and bump
  `METH_VER` (see CLAUDE.md "Methodology sync contract").
- `base` is composed in `slate.py` (R-003 → R-010 → R-010cap → R-008)
  and emitted per queue entry as `base_*_r008`. The UI and calc.py take
  it as given.
- The queue already carries `spread` (home-relative: positive = home
  favored), `total`, `implied_total` (this team), `deficit`, `team`,
  `side_of_mechanism`. `nflcal/data.py::implied_totals()` converts.
- nflverse schedules have `spread_line` and `total_line` for every game
  back to 1999, so historical game-script context is available for
  fitting.
- `predictions.csv` is 74 columns today (SCHEMA.md). New audit columns
  append at 75+. `log.py FIELDS`, `slate.py` queue stub and the UI's
  `CSV_COLS` must stay identical (SCHEMA.md is authoritative).
- Backtest harness: `python validate.py --mode brier --seasons 2023,2024,2025`.
  Lines there are a synthetic proxy; read **deltas between variants**,
  not levels. Strict ship policy used since W10: vs the current live
  variant, worst-season delta ≥ −0.0001 **and** mean delta ≥ +0.003.

## The one design point that matters most

`base` is a trailing average. Those trailing games were played under
their **own** game scripts. A back whose last three games were blowout
wins already has "leading script" carries baked into his base. So the
adjustment must be **relative**:

```
Δmargin = this week's expected margin (team perspective)
          − mean expected margin across the games that built the base
Δtotal  = this week's implied team total − mean implied team total in those games
```

Using this week's spread alone would double-count script, the same
mistake as the pace multiplier (`base × pace/60`, see "Related open
items").

## Model

Multiplicative, per position/market, fit on history:

```
script_mult = exp(b_margin · Δmargin + b_total · Δtotal)
expected    = base × script_mult [× pace term, if it survives W11] + matchup_adjust_r002
```

- Fit `b_margin`, `b_total` separately for RB carries and WR receptions
  with a Poisson GLM (log link) on player-weeks:
  `actual ~ offset(log(base)) + Δmargin + Δtotal`.
- Sample: the same player pool the slate uses (depth-chart RB1 / WR1–2,
  base ≥ 8 car or ≥ 2.5 rec), weeks 4+.
- **Fit on 2019–2022 only. Test on 2023–2025.** The backtest seasons
  must not be used to fit the coefficients, or the backtest is
  in-sample and proves nothing.
- Expected signs: RB carries `b_margin > 0` (favored → more runs);
  WR receptions `b_margin < 0` (underdog → more throws); `b_total > 0`
  for both (more scoring → more plays). If a fitted sign is wrong or
  its CI spans zero, set that coefficient to 0. Don't force it.
- Clip `script_mult` to [0.85, 1.15] so a single extreme line can't
  dominate. Report how often the clip binds; if often, the clip is
  doing the work and should be questioned, as with R-010cap.

### Data to build

`nflcal/data.py`:

1. `team_game_script(season)` → per team-game: `expected_margin`
   (team perspective, from `spread_line` + home/away) and
   `implied_team_total` (via `implied_totals`).
2. `base_script_context(season, player, through_week, n=3)` → mean
   `expected_margin` / `implied_team_total` over the games the player
   actually played that built his trailing-3 base (skip byes and DNPs,
   the same games `base` uses).
3. `script_mult_r012(position, market, d_margin, d_total)` → clipped
   multiplier, with coefficients as module constants plus a comment
   giving the fit window and CIs.

A one-off fitting script, `tools/fit_r012.py` (like
`tools/fit_sigma.py`), prints the coefficients, CIs and N, so the
constants are reproducible.

## Backtest

Add variants to `validate.py --mode brier`, each on top of whatever
W11 stack is live by then:

- `r012`: live stack × script_mult
- `r012_margin_only`, `r012_total_only`: ablations
- `r012_noclip`: to see what the clip costs

Compare against the live variant with the strict policy. Also report
the sub-Brier on props where |Δmargin| ≥ 3, since that's where R-012
should matter.

### Combination grid (declared in advance; do not add cells after seeing results)

R-012 interacts with the W11 candidates: the implied-total term
overlaps with what the pace multiplier was trying to do, and the σ rule
sets how much any change to `expected` moves `my_p`. So R-012 is tested
in combination, on a fixed grid of 8 variants:

| base stack ↓ / R-012 → | none | margin only | total only | margin + total |
|---|---|---|---|---|
| **A**: live W10 (+ `w11_nostack` if it passed) | A0 | A1 | A2 | A3 |
| **B**: best W11 stack from the 2026-11-14 run | B0 | B1 | B2 | B3 |

Selection protocol, to keep the backtest from being fitted:

1. R-012 coefficients are fit on 2019–2022 only (above).
2. Pick the winner among the 8 using **2023–2024 only**, applying the
   strict policy vs the live stack on those two seasons.
3. Run **only the winner** on **2025** as the confirmation check. It
   ships only if its 2025 delta vs live is also ≥ −0.0001. If it fails
   2025, nothing from the grid ships; record the result in
   METHODOLOGY.md as a dead end, the same way W10-SHARE was recorded.
4. Commit this grid (this section) before the grid is run, so the
   commit history shows it was declared in advance.

## Schema / UI / sync (only once the backtest passes)

- Append columns 75–78: `script_d_margin`, `script_d_total`,
  `script_mult`, `r012_notes`. Update SCHEMA.md, `log.py FIELDS`,
  `slate.py` stub, UI `CSV_COLS`.
- `slate.py` computes `script_mult` per queue entry and emits it.
  `ui.html` `estimateProb` and `calc.py` `compute_myp` multiply `base`
  by it. UI chip: `R-012 ×1.08 (fav by 7.5 vs 2.0 in base games)`.
- METHODOLOGY.md: "Between Week 10 and Week 11" section plus an
  R-012 registry entry. Bump `METH_VER`.
- Republish the UI artifact. `ui.html` and the live page must stay
  identical.

## Interpretation caveat for the write-up

The spread and total are market information. Putting them into `my_p`
makes the forecast more market-like. That's legitimate for calibration,
but it weakens H5 (does my_p add information beyond the market price):
part of any agreement with the market is now by construction. State it
in the write-up and split H5 by methodology version.

## This-week decision (week 10)

The goal is to know before tomorrow's 1 PM kickoff whether to ship for
the rest of week 10:

1. Implement the data functions and fitting script; fit on 2019–2022.
2. Add the backtest variants; run 2023–2025.
3. If R-012 passes strict policy vs the live stack **and** there's time
   to sync all three formula locations, regenerate `week_10.json`, and
   republish the UI before the owner logs the remaining props → ship as
   a new `METH_VER` for the unlogged week 10 props.
4. Otherwise R-012 targets week 11, and week 10 finishes on whatever
   W11 fixes passed (below).
5. **Never re-run already-logged props through a new formula.** Logged
   rows stay at the version they were logged under; the version tags
   exist so the write-up can split bands.

## Related open items from the 2026-11-14 W10 review

Backtest variants are already in `validate.py` (Table 9, uncommitted
when this was written; results recorded below once the run finishes):

- `w11_nostack`: R-008 subtracts only what R-010 didn't already remove
  (`base = r010 − max(0, d_r008 − max(0, trailing − r010))`). Fixes the
  Woody Marks double count (10.64 → 14.34).
- `w11_nopace`: drop `× pace/60`. 2023–2025 data: it raises MSE and the
  slope of actual/base on pace is −0.61 (RB) / −0.39 (WR); the base is
  already produced at the team's own pace.
- `w11_flatsig`: fixed σ floors (5.5 car / 2.5 rec) instead of
  σ = 0.70/0.75 × base. Residual SD of actual − trailing is ~flat in the
  mean (RB 5.3–6.2, WR 2.0–2.8); W10 σ puts 88% (RB) / 83% (WR) of
  outcomes inside ±1σ, against 68% for a correct σ.
- `w11_nopace_flatsig`: both.
- Not tested yet: R-010's shrinkage strength (cap binds on 23/36 week 10
  props) and R-003 being diluted by R-010. Each needs its own study.

### W11 backtest results

_(filled in when the run finishes)_
