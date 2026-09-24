# PROCEDURE.md

Step-by-step operating manual for the weekly cycle. `PREREGISTRATION.md`
is the spec; `SCHEMA.md` is the column-level source of truth; `CLAUDE.md`
names the invariants; this file names the button-presses. If a step
here contradicts the preregistration, the preregistration wins.

**Prediction Log UI:** https://claude.ai/code/artifact/b7a4cd06-950a-4e2f-a850-8412e3ef93ad
The UI is the primary path for logging predictions each week; the CLI
paths in `log.py` are a fallback for when the UI is unreachable.

---

## One-time setup

1. Python 3.10 or newer.
2. `pip install nflreadpy pandas pyarrow scikit-learn matplotlib`
3. `git config` your name and email so commits are attributed to you.
4. Verify nflverse is reachable:
   `python -c "import nflreadpy as nfl; print(nfl.load_schedules([2025]).height)"`
5. Confirm `PREREGISTRATION.md` is committed to `main` and pushed. The
   commit's timestamp *is* the pre-registration; if it is not in the
   public history before your first prediction, there is no
   pre-registration.

---

## The weekly cycle — five gated stages

Each stage has a window, a command, an input spec, and a commit. Do them
in order. Do not skip a commit.

---

### Stage 1 — Tuesday or Wednesday. Generate the slate.

**Window:** after Monday Night Football has resolved, before Thursday
kickoff.

```bash
python slate.py --week N --detail             # human view, review it
python slate.py --week N --json > week_N_slate.json
```

**Inputs:** none. Everything is derived from nflverse.

**What to verify before moving on:**
- 6–12 games qualified. Fewer than 4 means the week is unusually
  consensus-heavy — investigate before treating it as normal; more than
  12 means the filter is misfiring.
- Every game shows four props in the queue (leading RB rush over,
  trailing RB rush under, trailing WR rec over, leading WR rec under).
- Each prop's `own_def_rank`, `def_rank_opp`, `deficit`, `spread`,
  `implied_total` look plausible for that game.

**Commit:** none required — `week_N_slate.json` is derived and
regeneratable. Do not commit it.

---

### Stage 2 — Before Thursday kickoff. Log every prop in ONE sitting.

**Window:** after Stage 1, and before the earliest kickoff of the week
(usually Thursday 20:15 ET). Must be a single uninterrupted session so
the information set is constant across the week. If you cannot finish in
one sitting, skip the week entirely and record why.

**Path:** paste `week_N_slate.json` into the artifact logger UI, hit
"Work the queue," go through every prop in the order the UI presents
them (soonest kickoff first).

**Per-prop inputs, in the exact order the UI enforces:**

| field | what it is | format | example |
|---|---|---|---|
| `line` | DraftKings' posted line for the market | half-point float | `4.5` |
| `my_p` | your probability the queued side hits | float, strictly between 0 and 1, four decimals | `0.6100` |
| `why` | one or two sentences naming the mechanism | free text | `WAS 30th D, implied 16.8 pts, script forces throwing; McLaurin 22% tgt share L3` |
| `odds` | DraftKings American price for the side you predicted | integer with explicit sign | `-115` |
| `opp_odds` | DraftKings American price for the opposite side | integer with explicit sign | `-105` |

**Odds format rules — get these wrong and every edge is silently
corrupted:**
- American odds. Negative = laying to win 100 (bet `|odds|` to win 100).
  Positive = risking 100 to win `odds`.
- Enter the sign. `-115` not `115`. `+105` not `105`.
- Enter **both** sides. The devig requires both to strip the margin.
- If DraftKings has one side unpriced or suspended, skip the prop.

**Probability format rules:**
- Decimal, not percent. `0.61`, never `61`.
- Strictly between 0 and 1. If your read is "certain," write `0.98`, not
  `1.00`; certainty is a probability claim you cannot back up in a game
  that has not happened yet.

**Side rules:**
- The queue tells you which side to predict (over on core, under on
  control). Your job is the probability that *that side* hits, not
  whichever side you like better.

**Skip protocol:**
- Any prop you don't log needs a written reason: injury out, prop not
  offered on DraftKings, DraftKings suspended, technical error. The
  reason belongs in the write-up notes; skipping silently contaminates
  the record.

**When the sitting ends:**

```bash
# export the CSV from the logger UI, replace predictions.csv with it
git add PREREGISTRATION.md predictions.csv
git commit -m "week N logged before kickoff"
git push
```

The `git push` is the timestamp that predates the earliest game. Without
it — or if the commit lands *after* Thursday kickoff — the week is
invalid for H5/H6 purposes. Push anyway with an honest message
(`"week N logged late — invalid for scoring"`) and note it in the
write-up. Do not backdate.

---

### Stage 3 — Shortly before each individual kickoff. Closing price.

**Window:** within one hour of each game's real kickoff time.

For each open row in the UI belonging to a game about to start, hit
"Close price" and enter DraftKings' price at that moment.

**Fields written:** `close_odds`, `opp_close_odds`, `market_p_close`.
Nothing else changes. Your `my_p`, `why`, opening `odds`, `market_p`,
and `edge` remain frozen at Stage 2's values.

**Commit after every game-day batch of closing prices:**

```bash
git add predictions.csv && git commit -m "week N close prices <day>"
git push
```

Beating the closing line is the stronger claim than beating the opening
line, because it means the market moved toward you after you were locked
in. The game-day commit is what makes that claim verifiable.

---

### Stage 4 — Tuesday morning. Resolve.

**Window:** any time after Monday Night Football has finished and
nflverse has ingested the box scores (typically Tuesday morning US
time).

```bash
python resolve.py --dry-run    # preview what will be written
python resolve.py              # write outcomes
```

**Outcome encoding, all set by `resolve.py`:**

| value | meaning |
|---|---|
| `1` | your predicted side hit |
| `0` | your predicted side missed |
| `push` | actual stat landed exactly on the line — dropped from scoring |
| `dnp` | game played but player recorded no stats row (usually inactive) — dropped from scoring |
| `""` (empty) | game hasn't played yet, or resolver couldn't match — stays pending |

**Never fill an outcome by hand.** If `resolve.py` can't do it, it stays
unresolved. Hand-editing outcomes is the single most destructive thing
you can do to this record.

**Commit:**

```bash
git add predictions.csv && git commit -m "week N resolved"
git push
```

---

### Stage 5 — Read the numbers. Not before ~100 rows.

**Window:** after week 6 or so, once ~100 rows are resolved. Before
that, running `score.py` is fine; adjusting anything based on it is not.

```bash
python score.py
python score.py --plot calibration_wN.png --bootstrap 1000
git add calibration_wN.png && git commit -m "week N calibration snapshot"
```

Commit `calibration_wN.png` each week so the calibration curve has a
timestamped snapshot per week; the arc of that image sequence is itself
part of the research artifact.

**Read the output in this order:**

1. **Brier scores:**
   - `brier naive` (base rate) is the floor. Beat it or you know nothing.
   - `brier market` (devigged line) is the real bar. Expected to be
     close to or better than yours.
   - `brier you` is you.
2. **Calibration table** (10 deciles): does observed frequency track
   stated probability? Positive `gap` = underconfident, negative =
   overconfident.
3. **Edge test:** coefficient on `edge` in `logit(outcome) ~ market_p +
   edge`. Positive means your read adds signal beyond the price. `≤ 0`
   is the pre-registered expected result.
4. **By market type:** receptions vs rush_attempts, separately.
5. **Core vs control tiers:** you should win on core in the predicted
   direction AND on control in the mirror direction. Winning only on
   core is a general bias toward overs, not skill.
6. **Contamination check** (post-season): distribution of
   `|my_p − market_p|` on rushing vs receiving props. A materially
   smaller spread on receiving is evidence that the disclosed 2024–25
   backtest exposure moved you toward the price. Report either way.
7. **Clustered bootstrap CIs** (from `--bootstrap N`, default 1000): the
   `lo` and `hi` columns on the calibration table are 95% percentile
   intervals from resampling *games* (not rows) with replacement. Four
   props on one game share a script and can't be treated as independent,
   so a row-level CI would be misleadingly tight. Read the interval as
   "if the season replayed itself, this decile's observed frequency
   would land in [lo, hi] roughly 95% of the time." Wide intervals early
   in the season are honest, not a code bug.

**Do not** change the filter, the prop set, or the analysis in response
to what you read. Ideas for changes get written down as new
pre-registrations for next season.

---

## Season timeline

| when | what | commit? |
|---|---|---|
| before week 3 | `PREREGISTRATION.md` on `main`, pushed | yes |
| every Tue/Wed, weeks 3–18 | generate slate | no |
| every week before Thu kickoff | log all props, one sitting | yes |
| before every kickoff | close prices | yes, per day |
| every Tue morning | resolve | yes |
| after week 18 | full score run, write H1–H6 verdicts | yes (write-up) |
| after write-up | independent backtest replication | yes |
| after replication | modelling and comparison | yes |
| next season | proposed changes become new pre-registration | yes |

---

## Input format cheatsheet

| field | format | example |
|---|---|---|
| `my_p`, `market_p`, `edge` | float, four decimals | `0.6100` |
| American odds | integer with sign | `-115`, `+105` |
| line | half-point float | `4.5`, `27.5` |
| side | `over` or `under` (lowercase) | `over` |
| outcome | `1`, `0`, `push`, `dnp`, or empty | `1` |
| game | `AWAY @ HOME` | `SEA @ WAS` |
| week | integer 3–18 | `3` |
| position | `WR`, `RB`, `TE` | `WR` |
| market | `receptions`, `rush_attempts`, `receiving_yards`, `rushing_yards` | `receptions` |
| logged_at, resolved_at | ISO-8601 UTC | `2026-09-24T19:47:12+00:00` |

---

## Failure modes and what to do

- **Missed the pre-kickoff window on a game.** Skip that game for the
  week. Never log a prop whose game has any information leak (injury
  news post-kickoff, weather updates, active/inactive report).
- **DraftKings unpriced or suspended.** Skip the prop with a written
  reason. Do not substitute another book — the preregistration names
  DraftKings.
- **Weeks with a qualifying Thursday game AND some weekend lines not
  yet posted.** The one-sitting rule wins. Log everything before
  Thursday kickoff; skip any prop whose line is not yet posted with
  reason `"no line posted at TNF-lockdown time"`. Do not log those
  props after TNF plays — post-TNF information (injuries, weather,
  market reaction) contaminates them relative to the props you already
  logged. On weeks with no qualifying Thursday game (like Week 3
  2026), the deadline slides to Sunday morning; wait until Saturday
  when weekend lines are more likely posted, and log the whole slate
  in one sitting then.
- **Player ruled out between logging and kickoff.** The prediction
  stands as logged. Real-world exposure is part of the record; hedging
  it moves the log from evidence to reconstruction. `resolve.py` will
  mark it `dnp` and drop it.
- **Two-sitting week (couldn't finish in one).** The week is
  contaminated for the contamination-check analysis and should be
  flagged in the write-up. Still log everything and still score.
- **UI crashes mid-session.** Finish via CLI (`python log.py --week N
  -i`) but flag the week — the "one sitting, one information set"
  assumption is weakened.
- **Resolved row looks wrong.** Check the nflverse row directly. If
  nflverse is wrong, note it in your write-up. Do not hand-edit the
  outcome.
- **Forgot to commit before kickoff.** Commit anyway with an honest
  message. Flag the week as invalid for H5/H6 scoring. Do not
  backdate.
- **Realized the queue is wrong** (e.g., a listed player was traded).
  Regenerate the slate before Stage 2 finishes. Once Stage 2 is
  committed, the queue is frozen.
