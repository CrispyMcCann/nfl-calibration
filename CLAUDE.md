# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A prospective, preregistered forecasting experiment run across the 2026 NFL
season. The owner logs his own probability on player volume props before
kickoff, records FanDuel's price at the same moment (DraftKings until
Amendment 1 in `PREREGISTRATION.md`), and scores both after
the season. It is **not** a betting system and no money is wagered.

Two things are being measured:

1. **Calibration** — do his 70% calls land 70% of the time. This is the
   primary deliverable and pays off regardless of anything else.
2. **A structural hypothesis** — weak-defense teams trail, so they throw more;
   their opponents lead, so they run more; player volume follows. The open
   question is whether that adds anything *conditional on the market price*.
   The preregistered expectation is that it does not.

The output is a research artifact: a calibration curve, three Brier scores
(naive / market / owner), and an honest write-up, null results included.

Doc roles — read them before changing behavior:

| file | role |
|---|---|
| `PREREGISTRATION.md` | authoritative spec: H1–H6, selection rule, analysis plan, stopping rule. Wins any conflict. |
| `SCHEMA.md` | canonical 46-column definition of `predictions.csv`, including which writer may touch which column |
| `PROCEDURE.md` | the weekly button-presses, failure modes, TNF-week protocol |
| `QUICKSTART.md` | one-page weekly checklist |

Several design choices that look awkward (interactive prompt order,
append-only CSV, `--narrow` off by default, the control props) exist to
protect the record and are not cleanup candidates.

**Prediction Log UI:** https://claude.ai/code/artifact/b7a4cd06-950a-4e2f-a850-8412e3ef93ad —
the primary weekly logging path; `log.py` is the fallback. Its source is
mirrored in `ui.html` at the repo root. If you edit either, update both.

---

## RULES THAT MUST NOT BE BROKEN

The value of the record is that it cannot have been massaged after the fact.
Breaking any of these destroys the project, silently and irreversibly. If
asked to do any of them, say no and point at this section.

1. **Never edit `predictions.csv` by hand or with ad-hoc code** — not to fix
   formatting, tidy columns, or "clean" it. The only sanctioned writers to
   existing rows are those in SCHEMA.md's writer table: `resolve.py`
   (`actual`, `outcome`, `resolved_at` only) and the UI's close-price /
   pre-kickoff amendment actions, which reach the CSV only via `merge.py`.
   Never add another, and never paste a UI export over `predictions.csv`.
2. **Never alter `my_p`, `why`, `logged_at`, `odds`, `market_p`, or `edge` on
   an existing row.** Frozen at log time. If the owner asks, refuse and
   explain why.
3. **Never backfill a missing outcome by hand.** If `resolve.py` can't
   resolve it, it stays unresolved or gets marked `dnp`.
4. **Never change the selection filter, the prop set, or the analysis plan
   mid-season.** Stopping rule is week 18. Ideas become hypotheses for next
   season's preregistration, not changes now.
5. **Never look at results and then choose an analysis.** Every split is
   named in `PREREGISTRATION.md` (deficit bands, early vs late, core vs
   control, rush vs pass). Any new slice is exploratory and must be labelled
   so in the write-up.
6. **Commit and push after every logging session.** Each `predictions.csv`
   commit must predate the games it references; that timestamp is the
   evidence. Never rewrite history on this repo (rebase, amend of pushed
   commits, force-push) and never backdate — a late commit gets an honest
   message instead.

---

## Commands

```bash
pip install nflreadpy pandas pyarrow scikit-learn matplotlib   # Python 3.10+
```

No test suite, no linter, no build. Every entry point is a script at the repo root:

| | |
|---|---|
| `python slate.py [--week N] [--top K] [--detail] [--json] [--narrow]` | Qualifying games for week N (default: next unplayed week). `--json` emits the slate+queue payload the UI loads. |
| `python log.py --week N -i` | Interactive prediction entry. Flag form for scripting: see `--help`. |
| `python merge.py [--dry-run] [export.csv]` | Fold the UI's "Copy all as CSV" (pasted into `ui_export.csv`) into `predictions.csv`. The only path from UI to CSV. |
| `python resolve.py [--dry-run]` | Fill `actual`/`outcome`/`resolved_at` from nflverse for pending rows. |
| `python score.py [--min-n N] [--plot path.png] [--bootstrap B]` | Brier scores, calibration table, edge regression. `--min-n` defaults to 100. |
| `python validate.py --season YYYY [--from-week W]` | Backtest the mechanism (not the market) on a completed season. Defaults to 2025. |

`SEASON = 2026` is hardcoded in `slate.py`; `log.py` defaults `--season 2026`.
Change both if the year rolls over.

`nflreadpy` is the current nflverse package — `nfl_data_py` is deprecated and
archived; do not use it. `nflreadpy` returns Polars; call `.to_pandas()`.

---

## Architecture

**`nflcal/data.py` is the only data layer.** Every script imports from it. It
wraps `nflreadpy` loads in `functools.lru_cache` (per-process, keyed by
season), so one command's repeated calls hit nflverse once. (`resolve.py` and
`validate.py` also call `nflreadpy` directly.)

**Defensive ratings are shrunk, not raw.** `defense_ratings()` blends the
current-season allowed metric with the prior season using `K_PRIOR_GAMES = 4`:
`(n * current + k * prior) / (n + k)`. Early in the season the prior
dominates; by ~week 10 current data wins. Anything consuming `def_rank` is
reading a shrunk estimate.

**Selection rule (`slate.build_slate`)** is the hypothesis stated as a
filter. A game qualifies only if the defensive-rank gap and the spread agree
on who trails; disagreeing games are dropped as noise. **No discretion** —
the queue is generated so the owner cannot pick which predictions to make.
`--narrow` further restricts to the backtested band
(`MIN_DEFICIT`/`MAX_DEFICIT`/`MIN_WEEK`); it is off by default and for
post-season analysis only, because narrowing on the backtest is what the
prospective season tests, not assumes.

**Four props per game, fixed.** Two core props state the mechanism
(trailing WR receptions over, leading RB carries over); two controls state
its mirror (leading WR under, trailing RB under). The controls stop a general
bias toward overs from masquerading as insight. Do not drop them.

**Interactive prompt ordering is intentional.** Both `log.interactive()` and
the UI take the probability and reasoning *before* revealing the odds fields,
to prevent anchoring on the line. Do not "streamline" into a single form or
build anything that circumvents it.

**Schema sync.** SCHEMA.md is authoritative for three lists that must stay
identical: `log.py`'s `FIELDS`, `slate.py`'s `--json` queue stub, and the
UI's `CSV_COLS` (in both the artifact and `ui.html`).

**Data flow.** `slate.py --json` → loaded into UI → rows live in the
artifact's `db` (`predictions` collection) → "Copy all as CSV" →
`ui_export.csv` → `merge.py` → `predictions.csv` → commit/push →
`resolve.py` fills outcomes → commit/push → `score.py` reads. The UI never
sees outcomes, which is why the export must be merged, not pasted. Both
`merge.py` and `resolve.py` treat every CSV value as text so committed
bytes never change. The page reads `db` via `onSnapshot`, which delivers
a QuerySnapshot (`snap.docs`, `d.data()`), not an array.

---

## Data hazards

- **Never open `predictions.csv` in Excel** — silent date/number reformatting destroys the record.
- `outcome` mixes ints and strings: `1`, `0`, `push`, `dnp`, `skip`, or empty. `score.load()` filters the strings; any new consumer must handle them before casting to float.
- nflverse `spread_line` is home-relative (positive = home favored). `implied_totals()` and the who-trails logic depend on it.
- **American odds:** negative → `abs(o)/(abs(o)+100)`, positive → `100/(o+100)`. A sign error silently corrupts every edge while still producing plausible numbers. Devig and verified values are in SCHEMA.md.
- **Kickoffs come from schedule `gametime`**, converted from Eastern. Never hardcode a time — TNF/MNF kick at 20:15, and a wrong timestamp leaves a played game editable.
- **A player with no stats row is ambiguous** (unplayed vs inactive). `resolve.py` checks the game completed before marking `dnp`.
- **Effective sample size is the game count, not the row count** — four props on one game share one game script. `score.py` prints both; use the smaller.
- **Predict volume, not yards.** Yards add an efficiency term that is noise for this hypothesis.
- Weekly `week_N*.json` slate files are derived and should not be committed — stage files explicitly (`git add predictions.csv …`) rather than `git add -A`.

---

## Season timeline

| when | what |
|---|---|
| before week 3 | `PREREGISTRATION.md` committed and pushed before the first prediction |
| weeks 3–18 | slate (Tue/Wed), log in one sitting before first kickoff, close prices per game day, resolve (Tue). Commit + push each stage. No design changes. |
| any point | extend `validate.py` across 2019–2023 — allowed because it touches history, not the live record |
| after week 18 | score the prospective record against H1–H6 |
| then | re-run the backtest independently; agreement is replication, disagreement is the interesting case |
| then | modelling: logistic regression on recorded features, shrinkage on small-sample usage, compared against both judgment and market |
| next season | desired changes become a new preregistration |

The order matters: mechanism reasoned first, prospective test next, backtest
as replication rather than source.

## Owner context

Chris McCann — mechanical engineering master's at Ohio State moving into
quantitative research. This project is calibration practice and the research
artifact he intends to discuss in quant internship interviews, so the honesty
of the record matters more than the result. A clean null result presented
with its numbers is the expected and acceptable outcome.
