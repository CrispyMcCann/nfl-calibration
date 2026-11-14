# Plan — R-014 role-change prior (for week 11)

Status: **pre-declared, not built.** Written 2026-11-14 (Saturday of
week 10), before any R-014 result exists. Build and test it for week 11.
Don't change the trigger, variants or ship bar below after seeing
results; add a new dated section instead.

## Problem

R-010 shrinks a player's current-season rate toward his **previous
season's** full-year rate, with a pseudo-count of 29.5 games (RB) /
13.5 (WR). That treats volume as a stable trait. Volume is mostly
**role**, and roles change with the depth chart, so R-010 drags a
backup-turned-starter toward his old backup workload:

- Bhayshul Tuten, W10: 2025 backup 5.44 car/g (16 g); 2026 starter
  15.25 car/g (8 g, never below 12). R-010 → 8.30, capped to 11.2.
  With the positional prior (14.0) instead → ≈ 14.2.
- Woody Marks, W10: same pattern (2025 12.7, recent 18.5).
- R-010cap binds on 23 of 36 week 10 props, which suggests the shrinkage
  is mostly fighting the data.

## Rule (R-014)

When a player's role has changed since last season, R-010 uses the
**positional prior** (`R010_POSITIONAL_PRIOR_RB` = 14.0 car/g,
`_WR` = 5.0 rec/g, the existing rookie fallback) instead of his
prior-season mean. Nothing else in R-010 changes: same pseudo-count,
same recency blend, same R-010cap, then R-011/R-008, R-002, R-013, σ.

**Role change, declared trigger:** current-season games played ≥ 4
**and** `current_mean / prior_mean ≥ R` or `≤ 1/R` (promotion or
demotion), using the player's own stat for the market (carries for RB,
receptions for WR), both through week−1. Prior-season mean needs ≥ 5
games, as R-010 already requires; otherwise R-010 already uses the
positional prior.

## Pre-declared test

Base for comparison: the live stack **W10b** (`w11_nopace` in
`validate.py`: R-011 + R-013, σ 0.70/0.75).

Variants (exactly these three; no others are added after results):

| variant | R |
|---|---|
| `r014_r13` | 1.3 |
| `r014_r15` | 1.5 (primary) |
| `r014_r20` | 2.0 |

Protocol, the same shape as R-012's:

1. Pick among the three on **2023–2024** only.
2. Bar on 2023–24: worst-season delta vs W10b ≥ −0.0001 **and** mean
   delta > 0.
3. Run **only the pick** on **2025**: ships if its 2025 delta ≥ −0.0001.
4. Also report the sub-Brier on props where R-014 fires, and how often
   it fires and how often R-010cap still binds.

**Why the bar is looser than W10's (mean ≥ +0.003):** R-014 fires on a
minority of props, and averaging over all props dilutes any rule that
touches a subset. The bar is no-harm in every season plus a positive
mean, with a separate 2025 confirmation. It is declared here, before
any result, so it can't be tuned to the outcome.

If nothing passes, record R-014 in METHODOLOGY.md as a dead end and
leave W10b live.

## Build checklist (week 11)

1. `nflcal/data.py`: `role_changed(season, player, through_week,
   market, ratio)`; `ebayes_base` gets a `use_positional_prior` flag
   (default off, so W10b reproduces exactly).
2. `validate.py`: the three variants on top of `w11_nopace`'s base,
   plus a Table 11 applying the protocol above (pick on 2023–24,
   confirm on 2025), as Table 10 does for R-012.
3. If it ships: `slate.py` passes the flag, and `r010_notes` says
   `pos-prior (role change x{ratio:.1f})`. Sync contract:
   METHODOLOGY.md section and R-014 registry entry, `METH_VER` bump in
   `ui.html` and `tools/calc.py` (no formula change in either; the
   change is in the slate's base), regenerate the slate, republish the
   UI, **and replace the page's stored slate** (`meta/slate`) so the
   new tag never rides on old bases (see the W10b ship).
4. Log week 11 only after the ship decision.

## Related, not declared yet

The RB-only game-script margin term (R-012's one surviving
coefficient) needs its own pre-declaration before it can be tested on
top of W10b. It is not part of this plan.
