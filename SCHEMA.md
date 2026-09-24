# SCHEMA.md

Canonical schema for `predictions.csv`. This file is the source of
truth. Three places must stay in sync with it:

- `log.py` — the `FIELDS` list at top of module
- `slate.py` — the queue dict fields in `stub()` inside the `--json`
  path
- The "Prediction Log" artifact UI — the `CSV_COLS` list

If any code touches predictions.csv, read this file first. Schema drift
is the failure mode this project keeps running into, and each drift
event silently corrupts the record because pandas fills unknown columns
with `NaN` on merge.

---

## Column order and types

**46 columns, in this exact order.** Do not reorder; downstream tools
(and human readers of the CSV) rely on it. New columns go at the end.

### Identifiers & timing (7)

| # | column | type | source | notes |
|---|---|---|---|---|
| 1 | `id` | string | UI/log.py | 10 hex chars, unique per row |
| 2 | `logged_at` | ISO-8601 UTC | UI/log.py | when the prediction was written |
| 3 | `season` | int | slate | 2026 |
| 4 | `week` | int | slate | 3–18 |
| 5 | `game_date` | YYYY-MM-DD | slate | game's calendar day |
| 6 | `kickoff` | ISO-8601 with tz | slate | real kickoff, used for lock check |
| 7 | `weekday` | string | slate | `Thursday`, `Sunday`, `Monday` etc. |

### Game & mechanism (5)

| # | column | type | source | notes |
|---|---|---|---|---|
| 8 | `game` | string | slate | `AWAY @ HOME` |
| 9 | `team` | string | slate | the team the prediction is about |
| 10 | `side_of_mechanism` | enum | slate | `trailing` or `leading` |
| 11 | `tier` | enum | slate | `core_rush`, `control_rush`, `core_pass`, `control_pass` |
| 12 | `expect` | enum | slate | `over` or `under` — the queued side |

### Player & market (5)

| # | column | type | source | notes |
|---|---|---|---|---|
| 13 | `player` | string | slate/manual | player display name |
| 14 | `position` | enum | slate | `WR`, `RB`, `TE`, `QB` |
| 15 | `market` | enum | slate | `receptions`, `rush_attempts`, `targets`, `receiving_yards`, `rushing_yards` |
| 16 | `line` | float | user | half-point sportsbook line |
| 17 | `side` | enum | user | `over` or `under` — should match `expect` |

### Prediction (2)

| # | column | type | source | notes |
|---|---|---|---|---|
| 18 | `my_p` | float 0–1 | user | strictly `> 0` and `< 1`, four decimals |
| 19 | `why` | string | user | ≥ 4 chars, names the mechanism |

### Opening price (4)

| # | column | type | source | notes |
|---|---|---|---|---|
| 20 | `odds` | int | user | American, your side, e.g. `-115` |
| 21 | `opp_odds` | int | user | American, other side, e.g. `-105` |
| 22 | `market_p` | float 0–1 | derived | `devig(odds, opp_odds)` |
| 23 | `edge` | float | derived | `my_p − market_p`, four decimals |

### Closing price (5) — recorded ~1 hour before kickoff

| # | column | type | source | notes |
|---|---|---|---|---|
| 24 | `close_odds` | int | user | American, your side, at kickoff |
| 25 | `close_opp_odds` | int | user | American, other side, at kickoff |
| 26 | `market_p_close` | float 0–1 | derived | `devig(close_odds, close_opp_odds)` |
| 27 | `edge_close` | float | derived | `my_p − market_p_close` |
| 28 | `closed_at` | ISO-8601 UTC | UI | when the close was captured |

Blank if the close wasn't captured. Score functions treat blank
`market_p_close` as "no close on this row."

### Controls (13) — game-level context, from the slate

| # | column | type | notes |
|---|---|---|---|
| 29 | `own_def_rank` | int 1–32 | shrunk defensive rank of the team the player is on |
| 30 | `def_rank_opp` | int 1–32 | shrunk defensive rank of the opponent |
| 31 | `def_gap` | int | absolute difference in def ranks |
| 32 | `implied_total` | float | team's implied points |
| 33 | `deficit` | float | absolute expected margin from the market |
| 34 | `spread` | float | nflverse spread, home-relative |
| 35 | `total` | float | game total |
| 36 | `roof` | string | `dome`, `outdoors`, `closed`, `open`, `unknown` |
| 37 | `pace` | float | team's plays/game YTD |
| 38 | `base_tgt_share` | float 0–1 | player's recent target share |
| 39 | `base_rec` | float | player's recent receptions/game |
| 40 | `base_car` | float | player's recent carries/game |
| 41 | `base_snap` | float 0–1 | player's recent snap share |

### Skip & amendment audit (2)

| # | column | type | notes |
|---|---|---|---|
| 42 | `skip_reason` | string | if non-empty, this row was skipped (see below) |
| 43 | `amendments` | JSON | list of `{at, from: {...}}`, JSON-encoded; empty string if none |

### Outcome (3) — written only by `resolve.py`

| # | column | type | notes |
|---|---|---|---|
| 44 | `actual` | float | the actual stat value from nflverse |
| 45 | `outcome` | see below | `1`, `0`, `push`, `dnp`, `skip`, or empty |
| 46 | `resolved_at` | ISO-8601 UTC | when `resolve.py` wrote the outcome |

---

## Row types

Every row is one of three types, distinguished by populated columns:

| type | how to identify | scored? |
|---|---|---|
| **predicted** | `my_p` is non-empty | yes, if `outcome ∈ {0, 1}` |
| **skipped** | `skip_reason` is non-empty, `my_p` is empty | no; kept as record of what was on the queue but not predicted |
| **pending** | `my_p` non-empty, `outcome` empty | not yet; `resolve.py` will fill later |

Skip rows carry all the queue context (game, player, market, tier,
side_of_mechanism, controls) so the contamination check can reference
them, but have blank prediction and price columns.

---

## Outcome encoding

| `outcome` value | meaning | included in scoring? |
|---|---|---|
| `1` | your predicted side hit | yes |
| `0` | your predicted side missed | yes |
| `push` | actual stat landed exactly on the line | no |
| `dnp` | game played but player recorded no stats row | no |
| `skip` | row was skipped at the queue stage | no |
| `""` (empty) | not yet resolved | no |

`resolve.py` writes 1, 0, `push`, or `dnp`. `skip` is written by the
UI when a queued prop is skipped. Never hand-edit `outcome`.

---

## Which columns each writer touches

| writer | new rows | existing rows |
|---|---|---|
| UI (queue) | all columns 1–41 | `close_*` (24–28), `amendments` (43) |
| UI (manual) | columns 1–41 (populates what it can from the loaded slate; blanks otherwise) | same |
| UI (skip) | columns 1–14, 29–41, `skip_reason` (42), `outcome="skip"` | never |
| `log.py` CLI | columns 1–23, controls it has access to (29–41 where the slate provides them) | never |
| `resolve.py` | never | `actual` (44), `outcome` (45), `resolved_at` (46) |
| `slate.py` | never | never — read-only |
| `score.py` | never | never — read-only |

`resolve.py` is the only writer to existing rows, and it touches only
three columns. Everything else on an existing row is frozen from write
time.

---

## Devig math

```python
def american_to_prob(odds: float) -> float:
    return abs(odds) / (abs(odds) + 100) if odds < 0 else 100 / (odds + 100)

def devig(a: float, b: float) -> float:
    x, y = american_to_prob(a), american_to_prob(b)
    return x / (x + y)
```

Verified: `american_to_prob(-110) = 0.5238`, `devig(-110, -110) = 0.5`,
`devig(-115, -105) = 0.5108`. Getting the sign wrong on either odds
silently corrupts every edge.
