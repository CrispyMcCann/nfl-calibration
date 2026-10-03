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

## Week 4 — the current strategy

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

### R-001 — shrinkage toward trailing-3-game base  (added 2026-10-03)

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

### Placeholder format for future rules:

```
### R-001  (added week N)
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

**Week 4:** no deviations logged yet.
