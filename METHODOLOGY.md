# METHODOLOGY.md

Personal record of how the probability numbers in `predictions.csv`
were generated, week by week. This file is a **methodology log**, not
the pre-registration — the pre-registration is in
`PREREGISTRATION.md` and constrains the analysis plan, not the way
probabilities are formed. Changes here are allowed and expected;
what matters is that each change is dated and the reasoning is on
the record so the calibration curve can be read against a known
sequence of methods rather than an opaque black box.

Nomenclature note: "Week N" throughout this file means NFL Week N
(the column in `predictions.csv`). The 2026 season log starts at
Week 3.

---

## Keeping methodology code in sync

The formula below lives in three places that **must stay identical**:

1. **this file** — authoritative statement in "Week N — the current
   strategy" and the rules registry.
2. **`ui.html`** — a JS block headed `/* === METHODOLOGY :: <ver> ==`.
   The `METH_VER` constant in that block is what the UI writes to each
   row's `methodology_version` column.
3. **`tools/calc.py`** — Python fallback. Constants `SIGMA`,
   `SHRINK_THRESHOLD`, `SHRINK_FACTOR` and the body of `one_prop`
   must match the JS.

**Change any of them → change all three in the same commit, and bump
`METH_VER` to a new tag** (convention: `W<week-first-active>-<rule-list>`;
current: `W9-R002-R003b-R005b-R008-R010`). Rows logged under the old tag keep it. New rows get
the new tag. The write-up splits calibration by `methodology_version`
band, so divergence between the three sources silently corrupts the
record. If you're a future Claude and only one of the three looks like
it needs editing, that's a mistake — edit all three.

---

## The template every week's method should fit

```
For each prop:
  1. estimate the mean expected value (carries, receptions)
  2. estimate the sigma of that estimate
  3. probability the queued side hits =
        1 - Φ((line - mean) / sigma)   for over
        Φ((line - mean) / sigma)       for under
  4. enter as my_p (four decimals, strictly 0 < my_p < 1)
```

Everything below is one of: (a) how `mean` is computed, (b) how
`sigma` is computed, (c) rules for structured adjustments to `mean`.

---

## Week 3 — first week, unstructured

**Mean estimation:**

- **Rushing (RB):**
  `expected_carries = (team_offensive_plays / 2 ± tendency_bump) × player_snap_share ± vibe_adjust`
- **Receiving (WR):**
  `expected_receptions = (team_offensive_plays / 2 ± tendency_bump) × player_target_share ± vibe_adjust`

The `tendency_bump` was a few plays shifted toward run or pass
depending on whether I thought the team was run-heavy or pass-heavy.
The `vibe_adjust` was carries or receptions added or subtracted when
the resulting expected value felt "far" from the player's weekly
average, in whichever direction felt right.

**Sigma:** constant, guessed.

- Rushing: `sigma = 3.66`
- Receptions: `sigma = 2.15`

**Probability:** normal CDF of `(line - mean) / sigma`, taken on a
calculator.

**Known problems with the Week 3 method** (documented after Week 3
resolved, before Week 4 log):

1. Sigma was underestimated for rushing. Empirical residual SD under
   a trailing-3-game predictor across 2023–2025 is 5.6 for RB
   carries; using 3.66 meant every `my_p` was pulled harder toward
   0 or 1 than the data supports. Contributed to Brier misses on
   confident-but-wrong rushing calls (Justin Jefferson-style unders
   said at 0.11 that came in at 100%).
2. Sigma was slightly underestimated for receptions (2.15 vs
   empirical 2.35), less material.
3. The vibe adjustments are unauditable — I can't tell six months
   from now whether a Week 3 bump on a specific prop was driven by
   defensive matchup, script signal, or noise.
4. No explicit use of the pre-registered mechanism inputs
   (`def_rank_opp`, `def_gap`, `implied_total`). The whole point of
   the record is testing whether those inputs beat the market. If
   they only enter my probabilities through unstructured intuition,
   I can't tell after the season whether the mechanism was doing
   any work.

---

## Between Week 3 and Week 4 — what changed and why

**Change 1: Empirical sigma from 2023–2025 residuals.**

Pulled all RB and WR player-weeks from 2023–2025 regular season.
For each row, computed `residual = actual − trailing_3_game_avg`.
Fit both a constant-sigma model and a sqrt-law model
(`sigma = k × √expected`). Results:

| stat | Week 3 σ | empirical constant σ | sqrt-law k | log-log slope | verdict |
|---|---|---|---|---|---|
| RB carries | 3.66 | **5.62** | 1.70 | 0.19 | constant σ ≈ 5.5 fits; sqrt-law overshoots |
| WR receptions | 2.15 | **2.35** | 1.18 | 0.14 | constant σ ≈ 2.5; very mild scaling |

The log-log slope tells us the sqrt-law (which would show slope 0.5)
does *not* describe rushing residuals. The empirical SD is roughly
flat across the 5–25 carry range (5.0 at 7 carries, 5.8 at 22
carries), which means using a constant sigma is not a bad
simplification. Same holds for receptions except that the SD grows
mildly with expected volume (2.1 at 3 receptions, 2.7 at 6, 3.1 at 11)
— still close enough to constant for this week.

Caveat: these residuals are under a trailing-3-average predictor,
which is dumber than the `plays × share ± mechanism` model. A
smarter predictor would have smaller residuals. So the empirical
constants above are an **upper bound** on the sigma I should use.
If my Brier over Weeks 3–8 says I'm systematically better than
constant sigma predicts, I can tighten later — but not before
seeing evidence. Overconfidence is the failure mode I already
committed on Week 3.

**Change 2: Formalize the mean formula.**

Same shape as Week 3, but the components are named so they can be
audited later.

```
expected_carries = base_carries × pace_factor × usage_factor + matchup_adjust
expected_receptions = base_receptions × pace_factor × usage_factor + matchup_adjust
```

- `base_*` = the queue's `base_car` or `base_rec` (recent per-game
  average from the last three games).
- `pace_factor` = team's YTD plays/game divided by 60. So a
  Detroit-style 70-play offense gets a pace_factor of 1.17; a
  60-play offense gets 1.00.
- `usage_factor` = 1.0 as a starting point. Bump only via a rule
  from the registry below.
- `matchup_adjust` = additive; 0 by default. Bump only via a rule
  from the registry below.

**Change 3: No more vibes bumps.**

If I feel like adjusting a prop, I write the reason down. If the
reason isn't in the rules registry below, I don't apply it — I
propose it to Claude, we discuss, and if we agree we add it as a
rule and use it *from that point forward*. Anything I bump mid-week
without a rule gets flagged in the `why` column with the token
`[vibe]` so post-season I can measure whether vibes helped or hurt.

**Change 4: Explicit hooks for the mechanism inputs.**

The queue's `def_gap`, `def_rank_opp`, `implied_total`, `deficit`
are the ingredients the pre-registered hypothesis says should
predict volume. They enter Week 4's formula only via `matchup_adjust`,
initially set to 0. Rules that use them will accumulate below.

---

## Between Week 5 and Week 6 — what changed and why

Three changes land together for the Week 6 slate:

**Change 1: Queue picker uses depth chart + snap-pct, not just
trailing-3 target share.** (Code fix, not formula.) Observed Week 5:
LA Rams `control_pass` queued Davante Adams (0.260 share, 0.81 snap)
over Puka Nacua (0.240 share, 0.90 snap) because Nacua missed early-
season games and had a thinner trailing-3 mean. `slate.py`'s picker
now consults nflverse's depth-chart `pos_rank` for authoritative WR1
/ RB1 identification, falling back to a composite
`0.6 × snap_pct + 0.4 × share` score when the depth-chart leader
doesn't appear in trailing-3 usage with ≥ 0.4 snap. Players listed
Out this week are skipped by the picker (fed through `exclude_out_week`),
so the slot falls to the next-eligible player instead of being queued
and then self-elevated by R-003. Shipped in commit "slate: use depth
chart + snap-pct for queue player picker" ahead of this methodology
bump.

**Change 2: R-002 — position-specific defense matchup.** The
hypothesis says game script is determined by defensive quality, and
the Week 3–5 analysis uses the composite `def_rank`. But a pass-
leaky defense isn't the same as a rush-leaky one, and the queued
prop is position-specific. R-002 uses `pass_def_rank_opp` for
receiving props and `rush_def_rank_opp` for rushing props, both
read from the already-shrunk defense_ratings() table. Bump sizes
are small on purpose (1.0 carry, 0.5 reception) — below sigma/5 so
R-001 can still rein in anything that stacks the wrong way.

**Change 3: R-003 — teammate-injury base elevation.** Chris flagged
this during Week 5 logging: R-001 shrinks toward the trailing-3
base, which is stale when a position teammate has been out and
targets have redistributed. Rather than fight R-001, R-003 replaces
the stale base with a fresh one at the slate stage — slate.py reads
nflreadpy's weekly injury report AND the weekly roster snapshot to
find same-position teammates who are effectively out this week (see
R-003 condition below), filters to those with trailing-3 `snap_pct
≥ 0.25`, and adds 0.4 × out-WR rec/g (receiving) or 0.6 × out-RB
car/g (rushing) to the picked player's base. The UI and tools/calc.py
then consume `base_car_r003` / `base_rec_r003`; R-001 shrinks toward
this elevated base, which was the composition order the ordering
was designed to achieve.

**Wednesday Oct 14 amendment (R-003b):** Chris identified during the
Week 6 Thursday-prep that R-003 as originally shipped only consulted
the weekly injury report (`report_status == 'Out'`), which misses
players on IR / Reserve — those come off the weekly injury report
once they're placed. Concrete case: Jadarian Price started Weeks
1-3 for SEA, went Out in Week 4, moved to Reserve in Week 5. Emanuel
Wilson's Week 6 trailing-3 (16.7 car/g) still included Price's
committee games. R-003 as shipped did not fire for Wilson because
Price was no longer on the Week 6 injury report. The trigger was
expanded to the **union** of two signals:
  - `report_status == 'Out'` on the week's injury report, OR
  - `status != 'ACT'` on the week's `load_rosters_weekly` snapshot
    (catches RES/IR, INA, PUP, CUT, TRD, EXE, DEV, RET).
Inheritance rates (0.4 / 0.6), the ≥ 0.25 snap filter, and the
composition order are unchanged. METH_VER bumped from
`W6-R001-R002-R003` to `W6-R001-R002-R003b`; no Week 6 rows had
been logged under the original tag, so no methodology-version split
is introduced into the record.

**Backtest.** `validate.py --mode brier` was run against 2023, 2024,
and 2025 (540-582 synthetic props per season; line = `round(2 ×
season median)/2 − 0.5` for over, which makes absolute Brier values
not comparable to the live record — only variant deltas on the same
proxy are meaningful). Results:

```
Brier (lower = better):
season  baseline  r002    r003    all
2023    0.2517    0.2485  0.2530  0.2497
2024    0.2396    0.2390  0.2368  0.2357
2025    0.2580    0.2581  0.2578  0.2580

Delta vs baseline (positive = variant improved calibration):
variant   2023      2024      2025      worst     mean
r002      +0.0032   +0.0006   −0.0001   −0.0001   +0.0012
r003      −0.0013   +0.0027   +0.0002   −0.0013   +0.0005
all       +0.0020   +0.0038   −0.0000   −0.0000   +0.0019
```

**Codification decision.** The implementation plan called for
dropping any variant with Brier delta < 0 in any season. Taken
literally that drops all three variants on rounding dust (r002:
−0.0001; all: −0.0000) alongside R-003's one real −0.0013 miss on
2023. The honest reading is that the combined "all" variant has
the best mean improvement (+0.0019), is never meaningfully negative
in any season, and the R-003 2023 miss is washed out by R-002's
2023 win. Both rules ship together as `W6-R001-R002-R003` (and
after the Wednesday Oct 14 trigger-expansion amendment, as
`W6-R001-R002-R003b` — no logged rows carry the original tag). The
strict-policy variant (drop R-003, bump to `W6-R001-R002`) was
considered and rejected on the grounds that the combined variant
dominates it on mean delta and matches it on worst-season delta.
Policy relaxation itself is documented here as part of the record.

**What is NOT on the record.** No change to:
- the game-qualification filter (`build_slate` still uses composite
  def_rank × spread agreement; MIN_WEEK/MIN_DEFICIT/MAX_DEFICIT
  unchanged).
- the prop set (still four props per game, same tiers).
- sigma, SHRINK_THRESHOLD, SHRINK_FACTOR (R-001 unchanged).

---

## Between Week 6 and Week 7 — what changed and why

Week 6 scored poorly (Brier 0.2711 vs market 0.2484 across 88
cumulative rows). About half the deficit was one row: Puka Nacua
UNDER 7.5 receptions logged at `my_p = 0.02` (clamped). That value
was the formula's honest output given the inputs — Nacua's
trailing-3 base of 13.0 fed through pace-adjustment, R-002, and
R-001 and landed at `z = −2.7`. The weak link turned out to be the
INPUTS, not the formula arithmetic.

Two things broke on the Nacua row and both have back-pressure fixes:

**1. The trailing-3 base was an average of 2 games, not 3.** Nacua
was inactive weeks 2-3, played weeks 1, 4, 5. `usage()` divides by
games actually played, so his trailing-3 base = (9 + 17) / 2 = 13.0,
weighted heavily by a 17-target monster game right before the slate
was built. His season-to-date average across all 3 played games was
10.3. The thin denominator and high-variance recent sample compounded.

**2. σ = 2.5 is too tight for a WR with base 13.** Game-to-game
variance in receptions scales with workload — a 4-rec/g slot WR has
~2 rec variance, a 13-rec/g WR1 has 4-5. Keeping σ constant made the
formula overconfident whenever the base drifted high.

Two candidate rules addressed these — R-005 and R-006 — raised
2026-10-18 and deferred for backtest. The backtest ran against 2023,
2024, and 2025 (~540-580 synthetic props per season); both passed
strict policy against the current W6 methodology:

```
Brier delta vs W6-R001-R002-R003b (positive = variant improved calibration):
variant         2023      2024      2025      worst     mean
+ R-005         +0.0017   +0.0015   +0.0016   +0.0015   +0.0016
+ R-006         +0.0013   +0.0019   +0.0038   +0.0013   +0.0023
+ R-005 + R-006 +0.0027   +0.0031   +0.0051   +0.0027   +0.0036
```

Zero negatives anywhere. The combined variant dominates both single
variants on worst-season AND mean. Both rules ship together as
`W7-R001-R002-R003b-R005-R006`.

A σ_scale sweep probed 0.20 through 1.00 and showed the delta
continues to grow with σ, but that is a regression-to-0.5 artifact
(large σ pulls predictions toward the mean, which saves bad-call
cost but destroys good-call signal). SIGMA_SCALE = 0.30 is the honest
first-principles choice (empirical variance/base ratio for WR/RB
stats); larger values would be backtest-fitting.

**R-004 (cross-position inheritance for receiving props) remains
deferred** — a candidate from 2026-10-14 awaiting its own backtest.
Not shipped in W7 for lack of implementation time; still worth
testing when the next backtest window opens.

Nacua recompute under W7 methodology (same inputs):
```
base_rec     = 13.0 trailing, gms=2, season_avg = 10.3
R-006 blend  : (2/3)*13.0 + (1/3)*10.3 = 12.1
R-003        : no out WR teammate, no change
base_r006    = 12.1
raw_expected = 12.1 × (69.4/60) × 1.0 + 0.5 (R-002) = 14.5
R-001 fires  : expected = 12.1 + 0.5 × (14.5 − 12.1) = 13.3
σ_eff        = max(2.5, 12.1 × 0.30) = 3.63   ← R-005
z            = (7.5 − 13.3) / 3.63 = −1.60
p_under      = 0.055                           ← no longer clamped
```

Still a bold call. No longer at the floor. If Nacua went over, Brier
0.003 instead of 0.023. If he went under (as he did), Brier 0.893
instead of 0.9604. Both honestly less confident.

**What is NOT on the record.** No change to:
- the game-qualification filter (`build_slate` still uses composite
  def_rank × spread agreement; MIN_WEEK/MIN_DEFICIT/MAX_DEFICIT
  unchanged).
- the prop set (still four props per game, same tiers).
- `SHRINK_THRESHOLD`, `SHRINK_FACTOR` (R-001 arithmetic unchanged).
- `MATCHUP_BUMP`, `MATCHUP_WORST_K`, `MATCHUP_BEST_K` (R-002 unchanged).
- R-003 trigger (still the `W6-R001-R002-R003b` union of injury-report
  `Out` and non-ACT weekly roster).

---

## Between Week 7 and Week 8 — what changed and why

Week 7 scored poorly on top of a soft Week 6: cumulative Brier through
Week 7 is 0.2778 vs market 0.2488 (naive 0.2481). Week 7 by itself
was 0.3002 vs market 0.2504. The deficit is heavily concentrated in
rule misfires on four rows:

| row | my_p | actual | brier | which rule |
|---|---|---|---|---|
| Aaron Jones over 10.5 car | 0.962 | 8 | 0.925 | R-006 kept base at 21.5 — his Mason-less-trailing-base, but Mason returned Week 7 |
| Tyler Allgeier under 9.5 car | 0.028 | 8 | 0.946 | R-003 pumped base 9.7 → 17.5 off Love's 13 car/g — committee RB inheritance overshoots |
| Ollie Gordon II under 14.5 car | 0.783 | 25 | 0.613 | mechanism bet busted (MIA led/ran despite being "trailing" by model) |
| Xavier Worthy over 4.5 rec | 0.721 | 3 | 0.520 | R-003 pumped base 5.5 → 7.5 off Thornton — dispersed-targets WR1 overshoot |

With these four removed Week 7 Brier is 0.218 — would have beaten
market. On rows where neither R-003 nor R-006 fired, Week 7 Brier is
0.215. The *formula* works; a subset of rule fires are stale.

**Chris's observation (2026-10-29):** the Jones row has a specific
structural cause. His trailing-3 base (22.5) was built in weeks 4–5
while Jordan Mason was on IR (weeks 2–6). Mason returned active for
Week 7 and immediately took 15 carries; Jones got 8. R-003 fires only
when a teammate is OUT this week; it has no backup-return branch to
pull the queued player's base back down when the backup becomes active.
R-006 blends toward season average, but Jones's season average is also
mostly Mason-less games, so it couldn't rescue the row.

**Two candidate rules were formulated and backtested together:**

- **R-007 (cap on R-003 inheritance):** cap R-003's bump at 30% WR /
  50% RB of the queued player's own raw trailing base, to stop a single
  committee-RB vacancy or dispersed-targets WR vacancy from doubling
  the queued player's base.
- **R-008 (backup-return deflation):** the mirror of R-003. When a
  same-position teammate was effectively out in some of the trailing-3
  weeks AND is active this week, deflate the queued player's base by
  `inherit_rate × teammate.season_rate × fraction_of_trailing_window_out`,
  capped at the same symmetric 30% / 50% of raw trailing base.

**Backtest** (2023–2025, synthetic-line proxy, Table 3 deltas vs
current `all_r005_r006` baseline):

```
variant    2023      2024      2025      worst     mean
r007      +0.0002   -0.0005   -0.0003   -0.0005   -0.0002
r008      +0.0054   +0.0052   -0.0001   -0.0001   +0.0035
r007_r008 +0.0054   +0.0044   -0.0006   -0.0006   +0.0030
```

Where the rules actually fire (sub-Brier):
```
R-008 fire subset: 2023 0.2688 → 0.2395  (-0.0293)
                   2024 0.2568 → 0.2302  (-0.0266)
                   2025 0.1921 → 0.1929  (+0.0008)
```

**Codification decision.**
- **R-007 is dropped.** The backtest doesn't support it: 2 of 3
  seasons hurt (mean −0.0002), and on R-003 fires specifically it
  helps only 2023. The intuition is that most R-003 fires are
  legitimate next-up inheritances; capping them loses more signal than
  it saves. The Allgeier/Worthy misfires are the exception, not the
  rule.
- **R-008 is shipped.** Strict policy ("worst-season delta ≥ −0.0001")
  technically fails R-008 by a rounding hair on 2025 (−0.0001), but
  the precedent is R-003 itself: shipped under combined-variant policy
  relaxation when its isolated 2023 delta was −0.0013. R-008's
  worst-season of −0.0001 is tighter than R-003's −0.0013 was.
  Additionally, R-008 is the **only** variant that beats the raw
  baseline in every one of the three backtest seasons — all other
  rules we've shipped (R-002/R-003/R-005/R-006, individually and
  combined) lose to baseline on 2023 by 0.002–0.008 Brier. That
  across-season robustness is the deciding signal.

`METH_VER` bumps to `W8-R001-R002-R003b-R005-R006-R008`.

**Nacua / Jones recompute under W8 methodology.**

Jones (W7 inputs, had Mason been returning as he was):
```
base_car trailing-3     = 22.5 (gms=2)
R-003                   : no out teammate this week, base unchanged
R-006 blend             : (2/3) × 22.5 + (1/3) × 19.4 = 21.47
R-008 fires             : Mason OUT 3/3 trailing weeks, active W7
                          Mason season rate = 15 car (W1 only)
                          deflation = 0.6 × 15 × 1.0 = 9.0
                          cap = 0.5 × 22.5 = 11.25; deflation stands
base_car_r008           = 21.47 − 9.0 = 12.47
raw_expected            = 12.47 × (58.4/60) × 1.0 + 1.0 (R-002, rush_rk 26)
                        = 13.14
R-001 shrink            : |13.14 − 12.47| = 0.67 < 3; no shrink
sigma_eff (R-005)       = max(5.5, 12.47 × 0.30) = 5.5
z                       = (10.5 − 13.14) / 5.5 = −0.48
p_over                  = 0.68  (was 0.96 under W7)
```
If Jones had gone over as logged, Brier 0.10 vs the 0.92 we actually ate.

**What is NOT on the record.** No change to:
- the game-qualification filter.
- the prop set (still four props per game, same tiers).
- `MATCHUP_BUMP`, `MATCHUP_WORST_K`, `MATCHUP_BEST_K` (R-002 unchanged).
- R-003 trigger or inheritance rate (unchanged; R-007 is dropped).
- `SIGMA_SCALE`, `SIGMA`, `SHRINK_THRESHOLD`, `SHRINK_FACTOR`.
- R-006 blend weights or trigger (unchanged).

---

## Between Week 8 and Week 9 — what changed and why (shipped mid-W8)

Four rows logged for Week 8 TNF (CAR @ GB on 2026-10-29) landed on
`W8-R001-R002-R003b-R005-R006-R008`. On 2026-11-01, before the Sunday
slates kicked off, Chris surfaced a live example of overconfidence —
Bucky Irving over 16.5 car at `my_p = 0.721`, which the formula was
honestly reporting given its inputs but which clearly didn't match the
market or the player's full-season context.

Trace through Irving:
- 2026 season through W7: 7 games played, mean 17.1 car/g
- 2025 prior season: 10 games, mean 17.3 car/g
- Trailing-3 window (weeks 5-7): 21, 19, 24 → mean 21.3 (outlier hot stretch)
- Market line: 16.5

The W8 formula computed `base = 20.3` (R-008 Tucker-return deflation
of the 21.3 trailing mean), `σ = max(5.5, 20.3 × 0.30) = 6.09`,
`expected = 20.06`, giving `p_over = 0.721`. The market was at 0.514.
The claimed edge of +0.21 on a liquid market was a signal that
something in the formula was systematically off.

A sports-stats research pass the day before had already surfaced two
structural issues that explain this cleanly:

1. **σ_scale = 0.30 is roughly half the field standard.** PlayerProfiler's
   Variance Manifesto gives CV benchmarks of ~0.5-0.6 for RBs and
   ~0.6-0.7 for WRs at the fantasy-points level. Volume props are
   slightly less variable but not dramatically so. The W8 σ was tuned
   from a backtest sweep that found "σ_scale continues to improve
   Brier up to 0.70+" — but we dismissed that as regression-to-0.5
   artifact. The research says the field disagrees: the practitioner
   consensus is `σ ≈ 0.5-0.7 × base`.

2. **R-006 silently no-ops on returning-from-injury players.** It
   blends toward *this season's* average, which equals the trailing
   sample when the player only has a game or two. For Irving, both
   trailing_mean and season_mean = 17.1, so R-006 doesn't help; worse,
   the trailing-3 of 21.3 after R-003 inheritance is treated as the
   correct estimate of his true rate. The Efron-Morris regression
   literature (Bleacher Report / arXiv NFL win-% paper) is clear:
   RBs regress ~halfway toward prior-season baseline after ~29.5
   games. Our 7-of-7-played-this-season sample is far short of that.

A W9 draft slice was formulated on 2026-10-29, backtested the same
day, and the result was striking:

```
Table 6 — W9 DRAFT vs current W8 (r008)
variant     2023      2024      2025      worst     mean
sigma_up   +0.0114   +0.0059   +0.0102   +0.0059   +0.0091   KEEP
r010       +0.0112   +0.0051   +0.0039   +0.0039   +0.0067   KEEP
w9         +0.0129   +0.0064   +0.0106   +0.0064   +0.0099   KEEP
```

`w9` mean +0.0099 is ≈3× the biggest previous ship (R-005+R-006 at
+0.0036 vs 'all'; R-008 at +0.0035 vs W7). Worst-season +0.0064 vs
strict policy threshold of −0.0001 — a clean sweep by every metric.

**Mid-week ship rationale.** The record discipline rule says we don't
ship mid-week. The Irving row broke the symmetry: with a backtest
already showing +0.0099 and a live row showing exactly the predicted
failure mode, logging 24 more Sunday+Monday rows under W8 methodology
would knowingly pollute the record with mis-calibrated numbers. The
honest move is to ship now, tag W8 TNF as `W8-...` and W8
Sun+Mon as `W9-...` within the same calendar week. The write-up will
honor that split explicitly.

**Changes shipped as `W9-R002-R003b-R005b-R008-R010` on 2026-11-01:**

- **R-010 (new):** empirical-Bayes base. Replaces R-006. Shrinks
  current-season observation toward prior-season full-year with
  pseudo-count = Efron-Morris halflife (29.5 games for RB, 13.5 for
  WR). Fallback for rookies / no-prior-year: positional league-mean
  (RB1 ≈ 14 car/g, WR1 ≈ 5 rec/g). The shrunk estimate is then
  blended with the R-003-elevated trailing-3 using a recency
  pseudo-count of halflife/2. See the full registry entry below.

- **R-005' (upgraded):** `SIGMA_SCALE` is now position-specific:
  `0.60` for RB, `0.65` for WR (up from the single `0.30` constant).
  Field-median CV values from PlayerProfiler. The floor binds for
  base < 9.17 car/g RB or 3.85 rec/g WR, meaning the scale is
  effectively live on nearly every meaningful queue entry.

- **R-001 (retired):** threshold shrinkage fired on only 2% of live
  rows and was always redundant with R-010's continuous shrinkage.
  Dropped from the composition entirely to avoid double-shrinking the
  rare cases where it did fire. If a future candidate needs explicit
  threshold shrinkage we'll reintroduce it under a new rule number.

- **R-006 (retired as a formula input):** audit columns 63-66 are
  still populated by `slate.py` for continuity, but they no longer
  feed the formula. R-010 is the shrinkage mechanism going forward.

- **R-002, R-003b, R-008:** unchanged.

**Composition order for W9:**

```
raw trailing-3
  → R-003b (elevate for OUT teammate)
  → R-010 (ebayes shrink toward prior-season / positional)
  → R-008 (deflate for RETURNING teammate)
  → formula: expected = base × (pace/60) + R-002 matchup_bump
  → R-005' sigma = max(SIGMA_floor, base × SIGMA_SCALE[position])
```

**Irving recompute under W9 methodology:**

```
trailing_mean=21.3, trailing_n=3   (weeks 5-7)
season_mean=17.1,   season_n=7
prior_mean=17.3,    prior_n=10 (2025)
k = 29.5 (RB halflife)

shrunk_season = (7×17.1 + 29.5×17.3) / 36.5           = 17.27
recency_pseudo = 14.75
base_r010 = (3×21.3 + 14.75×17.27) / 17.75             = 17.95
base_r008 (Tucker return deflation of -1.0)            = 16.95
expected = 16.95 × (59.3/60) × 1.0 + 0 (ATL mid-tier)  = 16.75
σ_eff = max(5.5, 16.95 × 0.60)                         = 10.17
z = (16.5 - 16.75) / 10.17                             = -0.025
p_over(16.5)                                            = 0.510
```

0.721 → 0.510. Within rounding of the market's 0.514 — appropriate
hedging given the hot-stretch vs full-season tension.

---

## Week 9 — the current strategy

**Version tag:** `W9-R002-R003b-R005b-R008-R010`. Rows logged from
Week 9 forward (and Week 8 Sun+Mon after the 2026-11-01 mid-week
ship) carry this in `methodology_version`. Weeks 3–7 and Week 8 TNF
keep their original tags and are scored under their own methodology.

**Composition order.** Five rules stack (R-001 and R-006 retired):

```
  R-003b (base elevation)  —>  slate.py adds inherited rec/car when a
                                position teammate is effectively out
                                (injury-report Out OR non-ACT roster)
  R-010 (empirical Bayes)  —>  slate.py shrinks the R-003-elevated
                                trailing mean toward prior-season full-year
                                with pseudo-count = Efron-Morris halflife
                                (29.5 RB / 13.5 WR); output = base_*_r010
  R-008 (backup-return)    —>  slate.py deflates base_*_r010 when a
                                same-position teammate was out in some
                                trailing-3 weeks but is active this week;
                                output = base_*_r008, which is the
                                FORMULA INPUT
  R-002 (matchup_adjust)   —>  additive bump from opp's position-specific
                                EPA-allowed rank
  formula                  —>  expected = base_r008 × (pace/60) ×
                                           usage_factor + matchup_adjust
  R-005' (variable sigma)  —>  sigma_eff = max(SIGMA[market],
                                                base × SIGMA_SCALE[position])
```

R-010 subsumes R-001's shrinkage role (continuous empirical-Bayes
instead of threshold-gated halving). R-010 subsumes R-006's role
(prior-season fallback instead of same-season no-op).

**Sigma** — scales with base, per position (R-005'):
```
SIGMA_SCALE = { RB: 0.60, WR: 0.65 }
sigma_eff = max(SIGMA[market], base × SIGMA_SCALE[position])
```
- Rushing: floor `sigma = 5.5`; scale binds for base > 9.17 car/g.
- Receptions: floor `sigma = 2.5`; scale binds for base > 3.85 rec/g.

**Mean formula:**
```
expected = base_r008 × (pace / 60) × usage_factor + matchup_adjust_r002
```

- `base_r008` = `base_car_r008` for rushing, `base_rec_r008` for
  receiving. On W9+ rows this equals `base_*_r010` after any R-008
  deflation applies. On W8 rows the composition was R-003 → R-006
  → R-008.
- `pace` = team YTD plays/game ÷ 60.
- `usage_factor` = 1.0 by default.
- `matchup_adjust_r002` = R-002 output (see registry).

**Probability:**
```
z = (line - expected) / sigma_eff
my_p = 1 - Φ(z)   for over
my_p = Φ(z)       for under
```

**Clamp:** `my_p ∈ [0.02, 0.98]`.

---

## Week 8 — superseded strategy

**Version tag:** `W8-R001-R002-R003b-R005-R006-R008`. Rows logged for
Week 8 TNF (the four CAR @ GB props) carry this. Superseded mid-week
on 2026-11-01 by W9's R-010 + R-005' additions after Bucky Irving's
row surfaced live overconfidence. Rows logged for W8 Sun+Mon carry
the W9 tag.

Composition order was six rules (R-001 through R-008 as listed in the
"Between W7 and W8" entry above). Formula input was `base_*_r008`
built on top of R-006 blending. Sigma was `max(5.5/2.5, base × 0.30)`
market-tied (not position-aware).

See the "Between Week 8 and Week 9" entry above for the full
rationale, Irving worked example, and backtest results that drove
the mid-week ship.

---

## Week 7 — superseded strategy

**Version tag:** `W7-R001-R002-R003b-R005-R006`. Rows logged weeks 7
carry this in `methodology_version`. Superseded by W8's R-008 addition
on 2026-10-29.

Composition order was five rules: R-003b → R-006 → R-002 → formula →
R-005 → R-001. Formula input was `base_*_r006`. Sigma was variable per
R-005. See the "Between Week 7 and Week 8" entry above for the
specific rows that drove the methodology bump.

---

## Week 6 — superseded strategy

**Version tag:** `W6-R001-R002-R003b`. Rows logged weeks 6 (plus the
R-003b amendment within Week 6) carry this in `methodology_version`.
Superseded by W7's R-005/R-006 additions on 2026-10-22.

**Composition order:**

```
  R-003b (base elevation)  —>  base is set in slate.py; the UI/calc.py
                                consume base_car_r003 / base_rec_r003
  R-002 (matchup_adjust)   —>  additive bump from opp's position-specific
                                EPA-allowed rank
  formula                  —>  raw_expected = base × (pace/60) × usage_factor
                                             + matchup_adjust
  R-001 (shrinkage)        —>  if |raw_expected − base| > threshold:
                                 expected = base + 0.5 × (raw_expected − base)
                                else:  expected = raw_expected
```

**Sigma** — constant:
- Rushing: `sigma = 5.5` (constant)
- Receptions: `sigma = 2.5` (constant)

**Mean formula.** Same shape as Week 4, with R-002 populating
`matchup_adjust`:

```
expected = base_r003 × (pace / 60) × usage_factor + matchup_adjust_r002
         (then R-001 shrinks if deviation exceeds threshold)
```

**Probability:**
```
z = (line - expected) / sigma
my_p = 1 - Φ(z)   for over
my_p = Φ(z)       for under
```

**Clamp:** `my_p ∈ [0.02, 0.98]`.

---

## Week 4 — superseded strategy

**Sigma:**
- Rushing: `sigma = 5.5` (constant)
- Receptions: `sigma = 2.5` (constant)

**Mean formula (rushing):**
```
expected_carries = base_car × (pace / 60) × usage_factor + matchup_adjust
```

**Mean formula (receptions):**
```
expected_receptions = base_rec × (pace / 60) × usage_factor + matchup_adjust
```

Where `base_car`, `base_rec`, and `pace` come from the queue's
context row (last 3 games and YTD pace). `usage_factor` and
`matchup_adjust` default to 1.0 and 0 respectively, and are modified
only by rules in the registry below.

**Probability:**
```
z = (line - expected) / sigma
my_p = 1 - Φ(z)   for over
my_p = Φ(z)       for under
```

**Clamp:** never enter `my_p < 0.02` or `my_p > 0.98`. Extreme
probabilities are almost always overconfidence, and Brier penalizes
them heavily when wrong.

---

## Rules registry

Rules that modify `usage_factor` or `matchup_adjust`. Each rule is
dated, sourced (why we believe it), and applies from that week
forward. **Never edit past weeks' logged predictions to reflect a
new rule** — new rules apply to future logs only.

### R-001 — shrinkage toward trailing-3-game base  (added 2026-10-03, **retired 2026-11-01**)

**Retirement note (2026-11-01):** fired on only 2% of live rows (2 of
92 rows through Week 8 TNF). Thresholds (3 car / 1.5 rec) were wide
enough that the raw_expected almost never deviated from base enough
to trip them, and R-010's continuous empirical-Bayes shrinkage
subsumes the physical intent while actually firing in a useful
fraction of cases. Dropped from the composition in the W9 ship to
avoid double-shrinking the rare rows R-001 did fire on.

```
condition:   |raw_expected − base| > 3   for rushing props
             |raw_expected − base| > 1.5 for receiving props
applies to:  both
effect:      expected = base + 0.5 × (raw_expected − base)
             (keep half the deviation, discard half; only when the
              threshold is crossed — otherwise expected = raw_expected)
reasoning:   Shrinkage sweep on Week 3 residuals showed monotonic
             Brier improvement as raw_expected was pulled toward base.
             Threshold + halfway captures most of the gain (Brier 0.212
             vs 0.237 baseline, n=27) while only touching ~20% of rows.
             Matches an intuitive "±2-3 when far from average" bump
             with slightly tighter thresholds than Chris was using
             (3/1.5 beat 4-5/2-3 in the sweep).
source:      observed in Week 3 (2026-09-27). n=27; sample small.
             Review after Week 6: if Brier by tier isn't holding up
             better than the no-rule baseline, retire the rule and
             log the retirement in this file.
tooling:     `python tools/calc.py` applies this automatically.
```

### R-002 — position-specific defense matchup  (added 2026-10-10, active Week 6)

```
condition:   opp's position-specific EPA-allowed rank falls in top-8
             (1..8, best defenses) OR bottom-8 (25..32, worst defenses).
             Position-specific rank is pass_def_rank_opp for receiving
             props, rush_def_rank_opp for rushing props. The rank is
             the SHRUNK one from defense_ratings(), same as the game-
             qualification filter uses.
applies to:  both
effect:      matchup_adjust += MATCHUP_BUMP[market] when opp is in the
             bottom-8 of that market's defense;
             matchup_adjust -= MATCHUP_BUMP[market] when opp is in the
             top-8;
             otherwise matchup_adjust unchanged (zero).

             MATCHUP_BUMP = { rushing: 1.0 carry, receiving: 0.5 rec }

             Entry point: `matchupAdjustR002` in ui.html, mirrored by
             `matchup_adjust_r002` in tools/calc.py.
reasoning:   The structural hypothesis says a weak defense trails,
             which shapes game script and therefore volume. The game-
             qualification filter already uses the composite def_rank
             to pick qualifying games. R-002 refines that by letting
             the OPPONENT's position-specific weakness (pass or rush)
             shift the expected volume on the correct side of the
             prop. A pass-leaky defense means more rec for the
             trailing WR1; a rush-leaky defense means more car for
             the leading RB1 — mirrored in reverse for the controls.
             Bump sizes are intentionally < sigma/5 so a single
             mis-ranked defense cannot swing my_p dramatically; the
             rule is designed to adjust the mean by a defensible
             fraction of a game-level standard deviation.
source:      reasoned from the hypothesis itself + the Week 5 "why"
             column that proposed splitting defense by matchup. See
             the "Between Week 4 and Week 6" entry below. Not fit
             on Week 4 residuals.
tooling:     `python tools/calc.py` applies this automatically from
             pass_def_rank_opp / rush_def_rank_opp inputs. The UI
             reads them off the queue stub.
backtest:    2023 +0.0032, 2024 +0.0006, 2025 −0.0001 Brier delta
             vs baseline on synthetic-line proxy (540-582 props/season).
             Clear 2023 win, essentially flat in 2024/2025. The 2025
             negative is rounding noise. Shipped. See the "Between
             Week 5 and Week 6" section for the full table and the
             codification decision.
```

### R-003 — teammate-injury base elevation  (added 2026-10-10, active Week 6; trigger expanded 2026-10-14 → R-003b)

```
condition:   a same-position teammate on the player's team is
             EFFECTIVELY OUT for the upcoming week AND their
             trailing-3-game snap_pct >= 0.25. "Effectively out" is
             the UNION of two signals (introduced in the R-003b
             amendment to cover IR cases that fall off the weekly
             injury report):
               (a) report_status == 'Out' on the week's injury report
                   (short-term game-time designations), OR
               (b) status != 'ACT' on the week's load_rosters_weekly
                   snapshot (catches RES/IR, INA, PUP, CUT, TRD, DEV,
                   EXE, RET — season-ending or multi-week moves).
applies to:  both
effect:      base is replaced with an elevated base BEFORE the
             formula runs:
               WR  →  base_rec += 0.4 × sum(out_teammate.rec/g)
               RB  →  base_car += 0.6 × sum(out_teammate.car/g)

             R-003 fires in slate.py; the UI and tools/calc.py
             consume the elevated value as base_rec_r003 or
             base_car_r003. R-001 then shrinks toward the ELEVATED
             base, which is the whole point: without this ordering,
             R-001 would shrink the elevated expectation back toward
             a stale pre-injury trailing-3 average.

             Inheritance rates:
               - RB 0.6 reflects that one lead back inherits most of
                 an out RB's carries (next-up pattern); carries
                 concentrate.
               - WR 0.4 reflects that targets disperse among multiple
                 receivers when a WR1 misses; the WR2 only sees part
                 of the vacated share.
reasoning:   R-001 shrinks toward trailing-3 base, which breaks when
             the trailing-3 base is from games where the now-out
             player took targets/carries. R-003 replaces the stale
             base with a fresh one so R-001 shrinks against the right
             target. Noted by Chris in a Week 5 why column (Shakir
             row); the conflict with R-001 was his own observation.
source:      reasoned from the R-001 shrink target. Backtest in isolation
             mixed (see below).
tooling:     slate.py's --json emits base_car_r003 / base_rec_r003
             / r003_fires / r003_notes per stub. The UI shows an
             "R-003 fired" chip in the formula readout. Audit note
             is persisted to r003_notes in the row.
threshold:   The ≥ 0.25 trailing-3 snap_pct requirement filters out
             a scrub-RB going Out from artificially elevating the
             starter — a 4%-snap backup doesn't actually cede volume
             to the lead back when he's inactive.
scope:       Trigger is 'Out' on the injury report OR non-'ACT' on the
             weekly roster (R-003b). Doubtful and Questionable do not
             fire — those are probability-of-play reports, not known
             inactives, and sportsbooks reliably move lines only on
             known-inactive players. The R-003b expansion added IR /
             Reserve coverage because those designations come off the
             weekly injury report, so they would otherwise be invisible
             to R-003.
backtest:    2023 −0.0013, 2024 +0.0027, 2025 +0.0002 Brier delta vs
             baseline (synthetic-line proxy). In isolation this fails
             the strict codification policy on 2023; however, when
             combined with R-002 the 2023 loss is washed out (combined
             delta +0.0020 on 2023) and the combined "all" variant has
             the best mean improvement of any variant (+0.0019).
             Shipped jointly with R-002 under `W6-R001-R002-R003`
             (then `W6-R001-R002-R003b` after the Oct 14 trigger
             expansion; backtest numbers above are for the original
             injury-report-only trigger — R-003b catches strictly
             more cases, so the direction of effect is unchanged but
             re-backtesting against the expanded trigger is pending).
             See the "Between Week 5 and Week 6" section for the full
             table and the codification rationale.
```

### R-005 — variable-sigma scaling  (added 2026-10-22, active Week 7)

```
condition:   always active (replaces the fixed SIGMA constant in the
             z-score step). sigma_eff = max(SIGMA[market], base × 0.30).
applies to:  both markets.
effect:      z = (line − expected) / sigma_eff
             SIGMA floor stays at the Week 4 values (2.5 receiving,
             5.5 rushing). SIGMA_SCALE = 0.30 is the scaling constant;
             it binds (overrides the floor) when base × 0.30 > SIGMA:
               - rushing: scale binds for base > 18.3 car/g
               - receiving: scale binds for base > 8.3 rec/g
             For low-base props the formula is unchanged (floor binds).
             For high-base props sigma widens, softening overconfidence
             when the expectation drifts far from a thin line.
reasoning:   σ = 2.5 (receiving) and σ = 5.5 (rushing) were tuned for
             typical slot/early-down workloads. A WR averaging 13 rec/g
             (e.g., Nacua Week 6) has real game-to-game variance closer
             to 4-5 rec, not 2.5. Keeping σ constant made the formula
             over-confident on high-base props. The constant 0.30 is
             the first-principles choice from empirical variance/base
             ratios across WR/RB stat distributions. Larger values
             fit backtest Brier better (the sweep showed improvement
             up to σ_scale = 0.70+), but at the cost of regressing
             predictions toward 0.5 — that is a Brier-minimization
             artifact, not calibration improvement. 0.30 is the
             defensible stop.
source:      flagged by Chris 2026-10-18 after Puka Nacua UNDER 7.5
             rec clamped at 0.02 under W6 methodology. Written up as
             a candidate in memory 2026-10-18, backtested 2026-10-22,
             shipped same day.
tooling:     constant SIGMA_SCALE in tools/calc.py and ui.html. The
             compute_myp function reads it when the caller passes
             sigma_scale>0 (one_prop and estimateProb both do; the
             backtest in validate.py can pass 0.0 to recover the
             pre-R-005 behavior).
backtest:    2023 +0.0017, 2024 +0.0015, 2025 +0.0016 Brier delta vs
             the current W6 methodology (positive = improvement).
             Worst-season +0.0015, mean +0.0016. Passes strict
             codification policy on all three seasons.
```

### R-006 — small-N blend of trailing-3 base toward season average  (added 2026-10-22, active Week 7, **retired 2026-11-01 as formula input**)

**Retirement note (2026-11-01):** audit columns 63-66 still populated
by slate.py for continuity, but R-006 no longer feeds the formula on
W9+ rows. The silent-no-op failure mode on returning-from-injury
players (Jacobs W8, both trailing and season means equal to the one
returning-game observation) made it unreliable in exactly the thin-
sample case it was designed for. Replaced by R-010's empirical-Bayes
shrinkage with prior-season fallback.

```
condition:   the player's trailing-3 usage window contains fewer than
             3 games actually played (gms < 3 on the usage() row — i.e.,
             the player was inactive for one or more weeks of the
             window).
applies to:  both markets.
effect:      base_blended = (gms/3) × base_r003 + (1 − gms/3) × season_avg
             where season_avg is the player's mean of the stat across
             all games they actually played through the previous week
             (nflreadpy player_stats, filtered to the player's games).
             gms = 0 falls back to season_avg; gms = 1 weights 1/3 on
             the single-game trailing, 2/3 on the season prior; gms = 2
             weights 2/3 on the two-game trailing, 1/3 on the season
             prior; gms ≥ 3 is a no-op (base_r006 == base_r003).
             Applied in slate.py AFTER R-003 so the elevation (if any)
             is included in the trailing side of the blend, and the
             resulting base_*_r006 is what the UI and tools/calc.py
             consume as the formula input.
reasoning:   usage() divides sum-of-stat by games actually played, so
             a player who missed games in the trailing-3 window has a
             thinner, noisier base. In practice this biases the base
             toward "best recent games the player had on return,"
             which is often the hot stretch, not the sustainable
             level. The season-to-date average is a more stable prior.
             Weighting gms/3 is the natural effective-sample-size
             weighting (3 games is "full prior"; thinner samples get
             shrunk toward the longer-horizon average).
source:      flagged by Chris 2026-10-18 after confirming Nacua's
             Week 6 base of 13.0 rec/g was averaged over 2 games
             (weeks 4 and 5), skipping his inactive weeks 2-3.
             Written up 2026-10-18, backtested 2026-10-22, shipped.
tooling:     slate.py applies R-006 to each queue entry after R-003.
             blend_small_n() and season_avg() live in nflcal/data.py.
             r006_fires and r006_notes are persisted as audit; the
             formula input is base_*_r006. R-006 fires on roughly
             28% of queued props in a typical week (anyone who missed
             a game in the trailing-3 window).
backtest:    2023 +0.0013, 2024 +0.0019, 2025 +0.0038 Brier delta vs
             the current W6 methodology (positive = improvement).
             Worst-season +0.0013, mean +0.0023. Passes strict
             codification policy on all three seasons. Sub-Brier on
             the subset where R-006 fires (gms<3): 2025 improves
             0.2566 → 0.2420 — the largest effect of any rule we've
             shipped. Combined with R-005: 2023 +0.0027, 2024 +0.0031,
             2025 +0.0051, worst-season +0.0027, mean +0.0036.
```

### R-008 — teammate-return base deflation  (added 2026-10-29, active Week 8)

```
condition:   a same-position teammate on the player's team was
             EFFECTIVELY OUT (R-003b criteria: injury-report Out OR
             non-ACT weekly roster) in at least one of the trailing-3
             weeks (`week-3`, `week-2`, `week-1`) AND is ACTIVE on
             the weekly roster THIS week AND their season-to-date
             rate ≥ 5 car/g (RB) or 2 rec/g (WR). The rate floor
             filters out scrub-level teammates whose return doesn't
             really recapture volume.
applies to:  both markets.
effect:      base is DEFLATED after R-006 (so slate order is
             R-003 → R-006 → R-008, and the result is base_*_r008 as
             the formula input):
               WR  →  base_rec -= 0.4 × sum(returning_tm.season_rate × fraction_window_out)
               RB  →  base_car -= 0.6 × sum(returning_tm.season_rate × fraction_window_out)

             `season_rate` is the teammate's mean of the stat across
             games they played through `week-1`. `fraction_window_out`
             is the fraction of trailing-3 weeks the teammate was
             effectively out — a teammate who was out 2 of 3 trailing
             weeks and plays this week deflates by (2/3) of what R-003
             would have added if they were out this week.

             Deflation is capped at 30% WR / 50% RB of the queued
             player's RAW trailing base, so a single returning backup
             can't zero the base.

             Inheritance rates mirror R-003's (0.4 WR / 0.6 RB) because
             R-008 undoes the "backup absent" inflation that R-003 would
             have otherwise added when positions swap.
reasoning:   R-003 shrinks the base UP toward a "teammate out" context
             but has no complementary rule for a teammate RETURNING.
             R-006 can't close that gap because the season average
             blended toward is itself mostly "teammate out" games.
             Chris's Week 7 Aaron Jones row is the canonical case:
             Mason OUT weeks 2-6, Jones averaged 20+ carries in those
             games, Mason returned W7 and took 15, Jones got 8. Our
             W7 methodology said 0.96 on over 10.5 — the formula was
             honest given its inputs, but the inputs assumed Mason
             was still out.
source:      proposed by Chris 2026-10-29 after W7 scoring. Backtested
             same day across 2023-2025 (synthetic-line proxy). Codified
             same day.
tooling:     slate.py applies R-008 to each queue entry after R-006.
             `deflation_r008()` in nflcal/data.py returns the audit
             struct; r008_fires and r008_notes are persisted. The UI
             shows an "R-008 fired" chip in the formula readout.
backtest:    2023 +0.0054, 2024 +0.0052, 2025 -0.0001 Brier delta vs
             W7-R001-R002-R003b-R005-R006 baseline (synthetic-line proxy;
             540-582 props/season). Worst-season -0.0001 is rounding
             noise; mean +0.0035. On the subset where R-008 fires, sub-
             Brier improves 0.2688 → 0.2395 in 2023 and 0.2568 → 0.2302
             in 2024 — the largest effect of any rule we've shipped
             next to R-006 on its sub-Brier.

             Strict codification policy fails on the 2025 -0.0001 by
             rounding; shipped under the precedent of R-003 (which
             failed strict policy on 2023 by -0.0013 and was shipped
             anyway when its combined-variant pattern was favorable).
             R-008 additionally is the only variant that beats the raw
             no-rules baseline in every one of the three backtest
             seasons — all other rules we've shipped (R-002/R-003/R-005/
             R-006) lose to baseline on 2023.

             R-007 (cap on R-003 inheritance) was considered jointly;
             dropped because its backtest showed 2 of 3 seasons slightly
             negative (mean -0.0002) and R-007+R-008 together is
             essentially identical to R-008 alone. The W7 Allgeier /
             Worthy misfires that motivated R-007 are acknowledged
             anomalies, not a shippable rule change.
```

### R-010 — empirical-Bayes shrinkage toward prior-season (added 2026-11-01, active Week 9; shipped mid-W8)

```
condition:   always runs on W9+ rows (replaces R-006 as the shrinkage
             mechanism). The ebayes composition no-ops to the R-003-
             elevated trailing mean only when:
               - current-season n = 0 AND prior-season n < 5
                 (fallback to positional league-mean prior)
               - trailing_n = 0 AND there's no current-season sample
                 (fallback to the shrunk season estimate)
             Otherwise the base moves by at least a few percent and
             r010_fires = True on the audit column.
applies to:  both markets.
effect:      shrink the current-season observation toward the player's
             prior-season full-year average with a pseudo-count equal
             to the Efron-Morris regression halflife, then blend with
             the R-003-elevated trailing-3 using a recency pseudo-
             count of halflife/2.

             HALFLIFE_RB = 29.5 games    (Bleacher Report / Efron-Morris)
             HALFLIFE_WR = 13.5 games
             POSITIONAL_PRIOR_RB = 14 car/g  (fallback for rookies / new)
             POSITIONAL_PRIOR_WR =  5 rec/g

             prior      = prior_season_mean if prior_n >= 5
                          else positional_prior
             shrunk     = (season_n × season_mean + k × prior)
                            / (season_n + k)
             base_r010  = (trailing_n × trailing_mean
                             + (k/2) × shrunk)
                            / (trailing_n + k/2)   when trailing_n > 0
             base_r010  = shrunk                   when trailing_n = 0

reasoning:   R-006 shrank toward the current-season average, which is
             a no-op when the player's current-season sample is thin
             (the trailing and season means are the same observations).
             The Jacobs W8 row surfaced this: 1 game played all season,
             both trailing_mean and season_mean = 11, no shrinkage
             possible. R-010's prior-season fallback actually anchors
             to Jacobs's 2025 15.8 car/g, which is what the market
             priced in and what the physical reality of a returning
             starter supports.

             The Efron-Morris halflife numbers are from empirical
             sports-stats literature (Bleacher Report cited; practitioner
             consensus): RBs regress ~halfway toward prior after ~29.5
             games, WRs after ~13.5. Using these as pseudo-counts in a
             conjugate-prior shrinkage gives the right qualitative
             behavior at every n: thin samples pull hard toward prior,
             dense samples barely move.

             The halflife/2 recency pseudo-count is a practical choice
             — the shrunk current-season estimate is itself a composite
             of observation and prior, so it's worth about half the
             raw prior's evidence weight. Backtest sweeps weren't done
             on this specific constant; a future candidate could tune it.

source:      research pass 2026-10-29 (PlayerProfiler Variance Manifesto,
             Bleacher Report / Efron-Morris regression halflife). Backtest
             2026-10-29 across 2023-2025 with full-season prior fallback.
             Shipped mid-W8 on 2026-11-01 after Bucky Irving row surfaced
             live overconfidence that the formula couldn't fix on its own.
tooling:     slate.py applies R-010 to each queue entry after R-003
             elevation. ebayes_base() and prior_full_season() live in
             nflcal/data.py. The UI shows an "R-010 ebayes" chip in the
             formula readout.
backtest:    2023 +0.0112, 2024 +0.0051, 2025 +0.0039 Brier delta vs
             the current W8 methodology (positive = improvement). Worst-
             season +0.0039, mean +0.0067. Passes strict codification
             policy on every season. Combined with R-005' (SIGMA bump)
             as the W9 slice: 2023 +0.0129, 2024 +0.0064, 2025 +0.0106
             — ≈3× the biggest previous ship.
```

### R-005' — upgraded sigma scaling (field-median per position)  (added 2026-11-01, active Week 9; shipped mid-W8)

```
condition:   always runs on W9+ rows (replaces R-005's single 0.30 constant).
applies to:  both markets.
effect:      SIGMA_SCALE = { RB: 0.60, WR: 0.65 }
             sigma_eff = max(SIGMA[market], base × SIGMA_SCALE[position])

             Floor binds for base < 9.17 car/g (RB) or 3.85 rec/g (WR).
             For everything above those thresholds the scale is live.
reasoning:   The W7 R-005 was tuned off a Nacua-class overconfidence
             fix with σ_scale = 0.30. A sweep had hinted that higher
             values kept improving Brier, but we read that as regression-
             to-0.5 artifact. The 2026-10-29 sports-stats research pass
             surfaced that the practitioner consensus is substantially
             higher: PlayerProfiler's Variance Manifesto gives CV
             benchmarks of ~0.5-0.6 for RB and ~0.6-0.7 for WR at the
             fantasy-points level (volume props are slightly tamer but
             not dramatically so). Our 0.30 was roughly half the field
             standard, which translates directly to systematic
             overconfidence on every high-base prop.
             Chris chose field-median 0.60/0.65 over my conservative
             0.50/0.55 draft on the principle that "occasionally wrong
             is OK; systematically overconfident is not."
source:      sports-stats research pass 2026-10-29 (PlayerProfiler
             Variance Manifesto). Backtest same day; shipped 2026-11-01.
tooling:     tools/calc.py's SIGMA_SCALE constant is now a dict keyed
             by position; ui.html mirrors. compute_myp accepts an
             explicit `position` kwarg; defaults to RB values when
             omitted.
backtest:    sigma_up isolate: 2023 +0.0114, 2024 +0.0059, 2025 +0.0102.
             Worst-season +0.0059, mean +0.0091. Passes strict policy
             on every season by a wide margin — this change alone
             carries most of the W9 Brier gain.
```

### Placeholder format for future rules:

```
### R-NNN  (added week N)
condition:   e.g., "def_rank_opp >= 25 AND deficit > 6"
applies to:  rushing | receiving | both
effect:      e.g., "usage_factor *= 1.10"
reasoning:   e.g., "trailing team with big point deficit tends to
             abandon the run; opponent's back sees more clock-kill
             volume when facing a bottom-8 defense."
source:      "reasoned from first principles" | "observed in weeks X-Y" | "external"
```

When you notice a pattern mid-week and want to bump a prop for a
reason not in the registry, don't just do it — propose the rule
to Claude in the running conversation, we agree on the formulation,
and it goes in this section before it's applied.

---

## Contamination log

Every deviation from the current week's stated method gets logged
here so the write-up can quantify how much unlogged discretion
touched the record.

**Week 3:** all 27 predicted rows are `[vibe]`-adjusted by definition
(the Week 3 method includes ad-hoc adjustments). The whole week is
one contamination category and will be treated as such in the
write-up.

**Week 4:** no deviations logged.

**Week 5:** no methodology deviations. All 29 predicted rows carry
`methodology_version = W4-R001`. One queue-selection concern was
identified post-logging and documented in the "Between Week 5 and
Week 6" entry above: the depth-chart-aware picker was not yet active
when Week 5 was logged, so BUF @ LA `control_pass` was Davante Adams
(0.260 share) rather than Puka Nacua (0.240 share, higher snap).
Chris's logged `my_p` for Adams is valid *for Adams* — the record is
not contaminated — but the pre-registered prop for that slot was
"LA leading WR under receptions", and Nacua is the depth-chart WR1.
This is a data-selection issue, not a formula deviation; the write-up
should note that a handful of Week 5 rows prop'd a WR2 under this
rubric. Running `python slate.py --week 5 --json` after commit
`3653d3f` will show the queue picks the depth-chart WR1 for the same
games, which is how to audit which rows were affected.

**Week 6:** no deviations logged yet.

**Week 7:** no methodology deviations. All logged rows carry
`methodology_version = W7-R001-R002-R003b-R005-R006`. Four rule-driven
rows were notably miscalibrated and prompted the W8 methodology bump
(see "Between Week 7 and Week 8" above). Those rows stay as they were
logged — the record is honest about what the W7 methodology output,
even when the inputs were stale.

**Week 8:** W8 split across two methodology versions within a single
calendar week due to the mid-W8 ship on 2026-11-01. TNF rows (4 CAR @
GB props logged 2026-10-29) carry `methodology_version =
W8-R001-R002-R003b-R005-R006-R008`. All other W8 rows (Sun 1 PM,
Sun 4:25 PM, MNF — logged 2026-11-01 onward) carry
`methodology_version = W9-R002-R003b-R005b-R008-R010`. The split is
explicit on each row and the write-up will treat the two tags as
separate calibration buckets. The Irving row (first Sunday pick,
pre-ship) was the trigger; see "Between Week 8 and Week 9" above.
