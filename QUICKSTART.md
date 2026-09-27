# QUICKSTART.md

The one-page cheat sheet. If you forget everything else, do these
things in this order. Longer explanations live in `PROCEDURE.md`.

**Prediction Log UI:** https://claude.ai/code/artifact/b7a4cd06-950a-4e2f-a850-8412e3ef93ad

---

## The checklist — tick these off, don't skip a git push

```
Week N cycle
──────────────────────────────────────────────────────────────
[ ] 1. Tue/Wed  · generated slate JSON, spot-checked players
[ ] 2. Before Thu kickoff · logged every queue prop in one sitting
[ ] 2. Before Thu kickoff · Copy all as CSV → ui_export.csv → python merge.py
[ ] 2. Before Thu kickoff · git commit + git push  ← WITHOUT THIS THE WEEK IS INVALID
[ ] 3. ~1h before each game · captured FanDuel closing price in UI
[ ] 3. After each game-day batch of closes · ui_export.csv → merge.py, git commit + push
[ ] 4. Tue morning · python resolve.py --dry-run → then python resolve.py
[ ] 4. Tue morning · git commit + git push
[ ] 5. Any time after ~100 total rows · python score.py --plot calibration_wN.png --bootstrap 1000
[ ] 5. Any time · git commit calibration_wN.png + push
──────────────────────────────────────────────────────────────
```

Every stage ends with `git push`. If your terminal doesn't say
`Your branch is up to date with 'origin/main'`, you're not done.

## Every week — five things, in this order

### 1. Tuesday/Wednesday · generate the slate

```bash
cd ~/source/repos/nfl-calibration
python slate.py --week N --json > week_N.json
python slate.py --week N --detail | less   # look it over
```

### 2. Before Thursday kickoff · log all props in one sitting

- Open the UI (link above).
- In **Step 1**, pick `week_N.json` with the file picker (or paste it),
  hit **Load slate**. The queue starts on its own.
- Reloading the page is safe: it restores the slate, your logged rows,
  and your place in the queue. Never re-log a row.
- For each prop the queue hands you:
  - **line** — the FanDuel number (e.g. `4.5`)
  - **over/under** — locked to the queued side; your probability is for
    that side
  - **your probability** — decimal, 0–1 (e.g. `0.61`), before you look
    at the price
  - **why** — one sentence naming the mechanism
  - **odds** — FanDuel American, your side, with sign (`-115`)
  - **opp odds** — FanDuel American, other side (`-105`)
  - hit **Log & next**
- To skip: write the reason in **why**, hit **Skip this one**
- When the queue empties, hit **Copy all as CSV**, paste into
  `ui_export.csv` in the repo (create it the first time; after that
  replace its contents — never paste into `predictions.csv` directly),
  then:

```bash
python merge.py --dry-run   # N new rows, nothing REFUSED
python merge.py
git add predictions.csv
git commit -m "week N logged before kickoff"
git push
```

**The commit before kickoff is what makes it evidence.** Missed
kickoff = week is invalid for scoring, log it anyway with an honest
message.

### 3. ~1 hour before each game · closing price

- Open the UI, find each row for a game about to start.
- Hit **Close price**, enter both FanDuel prices at that moment.
- After every game-day batch:

```bash
# Copy all as CSV → paste into ui_export.csv
python merge.py
git add predictions.csv && git commit -m "week N close prices <day>" && git push
```

### 4. Tuesday morning · resolve

Monday night's closing-price export must already be merged and committed.

```bash
python resolve.py --dry-run    # preview
python resolve.py              # write outcomes
git add predictions.csv && git commit -m "week N resolved" && git push
```

### 5. Any time after ~100 rows · score

```bash
python score.py                                                        # read the numbers
python score.py --plot calibration_wN.png --bootstrap 1000             # weekly snapshot
git add calibration_wN.png && git commit -m "week N calibration snapshot" && git push
```

Do not change the filter, prop set, or analysis based on what you read.
Ideas for changes go in a notes file for next season's pre-registration.

---

## Input format cheatsheet

| what | format | example |
|---|---|---|
| probability | decimal 0–1, four decimals | `0.6100` |
| American odds | integer with sign | `-115`, `+105` |
| line | half-point float | `4.5` |
| over/under | locked by the queue to `expect` | `over` |
| reasoning | one sentence naming the mechanism | see PROCEDURE.md |
| skip reason | one sentence: injury, no line, suspended | required for every skip |

---

## Common rules

- **One sitting per week** for logging, before Thursday kickoff.
- **Never look at the odds before entering probability + reason.** The
  UI enforces this; don't circumvent it.
- **Never open `predictions.csv` in Excel** — silent reformatting
  destroys the record.
- **Never paste the UI's CSV into `predictions.csv`.** Paste into
  `ui_export.csv` and run `python merge.py`; a direct paste erases
  resolved outcomes.
- **Never hand-edit outcomes.** Only `resolve.py` writes them.
- **Never amend a row after its game has started.** The UI enforces
  this; commits before-and-after kickoff are the audit trail.
- **Commit after every session.** Git timestamps are the whole point.
