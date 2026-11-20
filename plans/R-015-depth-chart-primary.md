# Plan — R-015 depth-chart primary (for week 12)

Status: **pre-declared, not built.** Written 2026-11-19 (Thursday of
week 11, after W11 TNF logged), before any R-015 result exists. Build
and test it for week 12. Don't change the trigger, variants or ship bar
below after seeing results; add a new dated section instead.

## Problem

`primary_at(season, week-1, team, position)` picks the queued player by
**trailing-3 share** — carries for RB, target share for WR, computed
over the player's last 3 games played. That answers "who got the
touches lately." When the real lead back missed trailing-3 and returns
this week, trailing-3 picks the backup who filled in. The slate then
queues the wrong player for the whole prop chain.

Concrete misses this season:

- **W11 HOU RB, logged 2026-11-19**: Slate queued Emari Demercado
  (base 12.25 car/g from Marks + Demercado filling in while David
  Montgomery was out). Montgomery was active and the team-listed
  starter. The prop was skipped on the live record with reason
  "primary misassignment."
- User flagged **"a few of these a week slip through"** — the
  prop-selection step is a recurring source of noise the formula-side
  rules (R-003b, R-008, R-010, R-014) can't fix because they operate
  on the wrong player's base.

R-008 (teammate-return deflation) deflates the queued player's base
when a teammate returns — it doesn't change *who* the queued player
is. R-014 (role-change prior) adjusts R-010's prior anchor — also
leaves the player selection alone. R-015 is the missing piece at the
selection step.

## Rule (R-015)

On every (team, position) queue slot, consult the formal depth chart
(nflverse `load_depth_charts`). If the listed depth-1 starter was
actually active recently, queue them. Otherwise fall back to the
current trailing-3 share pick.

**Data source — schema split**:

nflverse changed its depth-chart schema between 2024 and 2025:

- **2023, 2024**: weekly rows with columns `season, club_code, week,
  depth_team, position, football_name/last_name/first_name`. Filter
  `season==Y AND club_code==T AND week==W AND depth_team==1 AND
  position==P` → 0-or-1 player name. Positions used: `RB`, `WR`.
  `depth_team` may be a string.
- **2025+**: daily snapshots with columns `dt, team, pos_abb,
  pos_rank, player_name`. For a given `(season, week, team, pos)`,
  pick the latest `dt` ≤ kickoff for that team/position, filter
  `pos_rank == 1` → 0-or-1 player name. Positions used: `pos_abb`
  values `RB`, `WR`.

The helper `depth_primary(season, week, team, position, kickoff_dt)`
returns the depth-1 player name or None.

**Mode selection (pre-declared variants, three only)**:

| variant | fallback trigger |
|---|---|
| `r015_strict` | always use depth-chart depth-1 when found; no fallback. If depth chart missing, fall back to trailing-3. |
| `r015_safe` | use depth-chart depth-1 IF that player has ≥1 game played in trailing-3 window; else fall back to trailing-3. |
| `r015_season` | use depth-chart depth-1 IF that player has ≥1 game played season-to-date; else fall back to trailing-3. |

Downstream pipeline unchanged. Once the player is chosen, the full
W11 chain runs on their numbers: R-003b → R-014 role-change check
→ R-010 ebayes → R-010cap → R-011 (R-008 net of R-010) → R-002
matchup → formula (no pace) → R-005c σ.

## Pre-declared test

Base for comparison: the live stack **W11** (`w11_nopace` plus
`r014_r13` in `validate.py`: R-011 + R-013 + R-014). The three R-015
variants run as alternate slate-generation modes, each producing its
own (player, actual, line) per slot.

**Protocol** (same shape as R-014 and R-012):

1. For each (season, week, game, side, pos, market) slot, generate up
   to four queued-player picks: `trailing_3` (live) + the three R-015
   modes. Each pick's row carries `primary_mode` plus the full
   downstream `w11_nopace` my_p computed against its own line.
2. Compute per-season mean Brier under the `w11_nopace` formula for
   each `primary_mode`. Compare R-015 modes vs `trailing_3` live.
3. Pick among the three on **2023–2024** only.
4. Bar on 2023–24: worst-season delta vs `trailing_3` ≥ −0.0001
   **and** mean delta > 0.
5. Run **only the pick** on **2025**: ships if 2025 delta ≥ −0.0001.
6. Also report (a) how often each mode disagrees with `trailing_3`,
   (b) sub-Brier on those disagreement rows (does depth-chart beat
   trailing-3 when they actually disagree? the aggregate dilutes
   because most slots agree), (c) how often depth-chart has no data
   and falls back.

**Why the bar matches R-014's**: R-015 touches a minority of props
(the ones where depth-chart and trailing-3 disagree). Averaging over
all props dilutes any subset rule. No-harm in every season plus a
positive mean, with a separate 2025 confirmation. It is declared here,
before any result, so it can't be tuned to the outcome.

If nothing passes, record R-015 in METHODOLOGY.md as a dead end and
leave W11 live.

## Build checklist (week 12)

1. `nflcal/data.py`:
   - `depth_primary(season, week, team, position, kickoff_dt=None)`
     — handles both pre-2025 and 2025+ schemas; returns player name
     or None.
   - `primary_at(..., primary_mode: str = "trailing_3")` — accepts
     `trailing_3` (default, reproduces current live), `depth_strict`,
     `depth_safe`, `depth_season`. The mode is a selection switch on
     the primary player; downstream fields (`rec`, `car`, `snap`,
     `share`, `gms`) still come from the chosen player's own trailing-3.
2. `validate.py`:
   - Generate rows under each mode; tag each row with its
     `primary_mode`.
   - Add Table 12 comparing `w11_nopace` Brier per season per mode,
     applying the protocol above.
3. If it ships: `slate.py` passes `primary_mode=<winner>` to
   `primary_at`. `r010_notes` gains a `primary_source=<depth-chart|
   trailing-3>` tag for audit. `methodology_version` bumped in
   `ui.html` and `tools/calc.py` (no formula change in either; the
   change is player selection upstream of the slate).
4. METHODOLOGY.md: "Between Week 11 and Week 12" ship section +
   R-015 registry entry.
5. Regenerate the slate for the next unplayed week, republish UI,
   **replace the page's stored slate** (`meta/slate`) so the new tag
   never rides on old picks (same as the W10b and W11 ships).

## Caveats worth flagging in the ship entry

- Depth charts can be wrong for committee backfields (teams list a
  "starter" who gets 50% of snaps). The `depth_safe` and `depth_season`
  modes partially mitigate by requiring recent activity. Residual
  misses still get caught by R-008 deflation and R-014 role-change on
  the base side.
- 2023+2024 schema uses `depth_team` as the rank field (may be a
  string like "1"); 2025+ uses `pos_rank` (integer). The helper
  normalizes to integer.
- 2025+ depth charts are daily snapshots dated in UTC; use the latest
  `dt` ≤ kickoff for the slate week.
- Depth charts sometimes include multiple WR slots (WR1/WR2/WR3) by
  slot rather than a single rank. `depth_primary` picks pos_rank=1 at
  the position, which in 2025+ nflverse data is an overall rank
  within the position (WR1 of all WRs, not just at the formation
  slot). That matches what the slate wants for the "primary WR."

## Related, not declared yet

- Depth-chart-based WR2 / WR3 queue generation (currently only
  primary WR is used). Out of scope.
- Using snap-count rank instead of depth chart. Explicit alternative;
  not tested here because snap counts are post-hoc; depth chart is
  pre-game.
