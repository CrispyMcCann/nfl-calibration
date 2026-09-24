# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A preregistered forecasting log. The `README.md` explains the weekly loop; `PREREGISTRATION.md` fixes the hypotheses, selection rule, and analysis plan before any 2026 row is written; `SCHEMA.md` is the canonical column definition for `predictions.csv`; `PROCEDURE.md` names the button-presses. Read all of them before changing behavior — several design choices that look like awkwardness (interactive prompt order, append-only CSV, `--narrow` off by default) exist to protect the record and are not cleanup candidates.

**Prediction Log UI:** https://claude.ai/code/artifact/b7a4cd06-950a-4e2f-a850-8412e3ef93ad — the primary weekly logging path. Its source is mirrored in `ui.html` at the repo root. If you edit either, update both.

The git history itself is load-bearing evidence: a self-reported forecasting record is only credible because each `predictions.csv` mutation is timestamped by a commit that predates the games it references. Anything that rewrites history on this repo (rebase, amend of pushed commits, force-push) invalidates the artifact.

## Setup and commands

```bash
pip install nflreadpy pandas pyarrow scikit-learn matplotlib
```

No test suite, no linter, no build. Every entry point is a script at the repo root:

| | |
|---|---|
| `python slate.py --week N [--top K] [--detail] [--json]` | Rank week N's games by how determined the script is. `--json` emits a slate+queue payload for external UIs. |
| `python log.py --week N -i` | Interactive prediction entry. Also has a flag form (see `--help`) for scripting. |
| `python resolve.py [--dry-run]` | Fill `outcome`/`resolved_at` from nflverse for pending rows. |
| `python score.py [--min-n N] [--plot path.png]` | Brier scores, calibration table, edge regression. |
| `python validate.py --season YYYY` | Backtest the mechanism (not the market) on a completed season. |

`SEASON = 2026` is hardcoded in `slate.py`; `log.py` defaults `--season 2026`. Change both if the year rolls over.

## Architecture

**`nflcal/data.py` is the only data layer.** Every script imports from it. It wraps `nflreadpy` calls in `functools.lru_cache` (per-process, keyed by season) so a single command's repeated calls hit nflverse once. `slate.py` and `log.py` share this cache implicitly through the import.

**Defensive ratings are shrunk, not raw.** `defense_ratings()` blends the current-season allowed metric with the prior season using `K_PRIOR_GAMES = 4`: `(n * current + k * prior) / (n + k)`. Early in the season the prior dominates; by ~week 10 the current data wins. Any code that consumes `def_rank` should be aware this is a shrunk estimate, not "what teams have done this year."

**Selection rule (`slate.build_slate`)** is the hypothesis stated as a filter. A game qualifies only if the defensive-rank gap and the spread agree on who trails (`s.agrees`). Games where they disagree are dropped — they would be noise, not observations. `--narrow` further restricts to a backtested deficit band (`MIN_DEFICIT`/`MAX_DEFICIT`/`MIN_WEEK`); it is deliberately OFF by default because narrowing on the backtest is what the prospective season is meant to test, not assume.

**The four-prop-per-game structure** (`slate.py --json` and the interactive logger) is fixed: two core props state the mechanism (trailing WR receptions over, leading RB carries over) and two control props state its mirror image (leading WR under, trailing RB under). The controls exist so a general bias toward overs cannot masquerade as insight. Do not drop the controls to "simplify."

**Append-only CSV, canonical schema in `SCHEMA.md`.** `log.py` and the UI both append; `resolve.py` is the sole writer to existing rows, and touches only `actual`, `outcome`, `resolved_at`. All other columns (`my_p`, `why`, `odds`, `market_p`, `edge`, etc.) freeze at log time. Never introduce a second writer to those columns. Three places must stay in sync: `log.py`'s `FIELDS` list, `slate.py`'s `--json` queue stub, and the UI's `CSV_COLS` — SCHEMA.md is authoritative for all three.

**Interactive prompt ordering is intentional.** `log.interactive()` asks for the user's probability and reasoning *before* revealing the odds field. That ordering prevents anchoring on the line and is the reason the interactive path exists — do not "streamline" it into a single form.

## Data hazards

- `predictions.csv` is git-tracked in principle but excluded by `.gitignore` in this working copy — the user commits it explicitly. Never open it in Excel (silent date/number reformatting destroys the record).
- Push outcomes and DNPs are stored as strings (`"push"`, `"dnp"`) in the `outcome` column alongside 0/1 ints. `score.load()` filters them out. Any new consumer of `outcome` must handle the string cases before casting to float.
- nflverse `spread_line` is home-relative (positive = home favored). `implied_totals()` and the "who trails" logic in `build_slate` depend on that convention.

# CLAUDE.md — NFL calibration project

Read this before touching anything. `PREREGISTRATION.md` is the
authoritative spec; this file is the operating manual.

## What this is

A prospective forecasting experiment run across the 2026 NFL season. The
owner logs his own probability on player volume props before kickoff,
records DraftKings' price at the same moment, and scores both after the
season. It is **not** a betting system and no money is wagered.

Two things are being measured:

1. **Calibration** — do his 70% calls land 70% of the time. This pays off
   regardless of anything else and is the primary deliverable.
2. **A structural hypothesis** — teams with weak defenses trail, therefore
   throw more; their opponents lead, therefore run more; and player volume
   follows. Whether that adds anything *conditional on the market price*
   is the open question. The pre-registered expectation is that it does
   not.

The output is a research artifact: a calibration curve, three Brier
scores, and an honest write-up — including of the null results.

---

## RULES THAT MUST NOT BE BROKEN

These exist because the entire value of the record is that it cannot have
been massaged after the fact. Breaking any of them destroys the project,
silently and irreversibly.

1. **Never edit `predictions.csv` except through `resolve.py`.** Not to fix
   formatting, not to tidy columns, not to "clean" it. `resolve.py` writes
   only `outcome` and `resolved_at`. Nothing else may ever write to an
   existing row.
2. **Never alter `my_p`, `why`, `logged_at`, `market_p`, or `edge` on a row
   that already exists.** Those are frozen at logging time. If the owner
   asks to change one, refuse and explain why.
3. **Never backfill a missing outcome by hand.** If `resolve.py` can't
   resolve it, it stays unresolved or gets marked `dnp`.
4. **Never change the selection filter, the prop set, or the analysis plan
   mid-season.** The stopping rule is week 18. Ideas for changes get
   written down as new hypotheses for next season, not applied now.
5. **Never look at results and then choose an analysis.** Every split is
   named in advance in `PREREGISTRATION.md` (deficit bands, early vs late,
   core vs control, rush vs pass). Any new slice is exploratory and must be
   labelled as such in the write-up.
6. **Commit after every logging session.** The git history is the timestamp
   evidence. A record with no commit history is unverifiable and worthless.

If asked to do any of the above, say no and point at this section.

---

## Weekly procedure

### Tuesday or Wednesday — generate the slate

```bash
python slate.py --week N                  # look at it
python slate.py --week N --detail         # with usage tables
python slate.py --week N --json > week_N.json
```

Every game where the defensive-rank gap and the spread agree on who trails.
Roughly 8–10 games, 4 props each. **No filtering, no discretion** — the
queue is generated precisely so the owner cannot select which predictions
to make. `--narrow` exists for post-season analysis only; do not use it for
collection.

### Before the first kickoff — log every prop

Paste the JSON into the logger UI (a Claude artifact — the owner has the
link), hit "Work the queue," and go through all of them. The tool refuses
to show the price fields until a probability and a reason are entered. That
ordering is deliberate; do not build anything that circumvents it.

Per prop the owner enters: the line, his probability, his reasoning, then
DraftKings' two American prices. Devigging is automatic.

All predictions happen in **one sitting**, even when a Thursday game is on
the slate, so the information set is constant across the week. The queue is
sorted by kickoff so the soonest-locking game comes first. Each row locks
at its own real kickoff time.

Skips require a written reason and are recorded.

### Shortly before each kickoff — closing price

Use the "Close price" button on each open row. It records
`close_odds` / `market_p_close` without touching the forecast. Beating the
closing line is the stronger claim than beating the price at prediction
time, because it means the market moved toward him afterward.

### Monday — resolve

Copy the CSV out of the logger, paste into `predictions.csv`, then:

```bash
python resolve.py --dry-run
python resolve.py
git add -A && git commit -m "week N resolved"
```

### Any time — score

```bash
python score.py                        # don't read it before ~100 rows
python score.py --plot calibration.png
```

---

## The season timeline

| when | what |
|---|---|
| now | commit `PREREGISTRATION.md` before the first prediction |
| weeks 3–18 | log, resolve, commit. No design changes. |
| any point | extend `validate.py` across 2019–2023 for historical sample — this is allowed because it touches history, not the live record |
| after week 18 | score the prospective record against H1–H6 |
| then | re-run the backtest independently; agreement is replication, disagreement is the interesting case |
| then | start modelling: logistic regression on recorded features, shrinkage on small-sample usage, compared against both his judgment and the market |
| next season | anything he wanted to change becomes a new pre-registration |

The order matters. The mechanism was reasoned out before any data was
examined, the prospective test comes next, and the backtest is a
replication of that test rather than its source.

---

## Files

| file | role |
|---|---|
| `PREREGISTRATION.md` | authoritative spec. H1–H6, design, analysis plan, stopping rule |
| `nflcal/data.py` | nflverse pulls, shrunk defensive ratings, implied totals, usage, pace |
| `slate.py` | ranks the week's games, builds the prop queue, `--json` for the UI |
| `log.py` | CLI logging (`-i` for guided prompts). The UI is the usual path |
| `resolve.py` | fills outcomes from nflverse. The only writer to existing rows |
| `score.py` | Brier, calibration curve, edge test, core-vs-control split |
| `validate.py` | backtests the mechanism on completed seasons |
| `predictions.csv` | the log. Append-only. See the rules above |

## Environment

```bash
pip install nflreadpy pandas pyarrow scikit-learn matplotlib
```

`nflreadpy` is the current nflverse package — `nfl_data_py` is deprecated
and archived, do not use it. It returns Polars; call `.to_pandas()`.

## Things that will bite you

- **Effective sample size is the game count, not the row count.** Four
  props on one game share a single game script, so their errors are
  correlated. `score.py` prints both; use the smaller one.
- **Predict volume, not yards.** Receptions and rush attempts sit one step
  from the causal claim. Yards add an efficiency term that is pure noise
  for this hypothesis.
- **`spread_line` in nflverse is the home team's line.** Positive means
  home favored.
- **Kickoffs come from `gametime` in the schedule**, converted from
  Eastern. Do not hardcode a time — Thursday and Monday games kick at
  20:15 and a wrong timestamp would leave a played game editable.
- **A player with no stats row is ambiguous** — either the game hasn't
  happened or he was inactive. `resolve.py` checks whether the week
  completed before marking `dnp`.
- **American odds conversion:** negative odds are `abs(o)/(abs(o)+100)`,
  positive are `100/(o+100)`. Getting the sign wrong silently corrupts
  every edge calculation while still producing plausible numbers.

## Owner context

Chris McCann — mechanical engineering master's at Ohio State moving into
quantitative research. This project is both calibration practice and the
research artifact he intends to discuss in quant internship interviews, so
the honesty of the record matters more than the result. A clean null
result presented with its numbers is the expected and acceptable outcome.
