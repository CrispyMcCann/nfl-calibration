# NFL calibration log

Testing one hypothesis: **conditional on the market price, does defensive
mismatch still predict receiving and rushing volume?**

Not "does game script affect production" — it does, and the line already
knows. The question is whether anything is left over after the price.

## Setup

```bash
pip install nflreadpy pandas pyarrow scikit-learn matplotlib
git init && git add -A && git commit -m "start"
```

The git repo is not optional. A self-reported forecasting record with no
timestamps is unverifiable, and the commit history is what makes yours
evidence rather than a claim. Commit after every logging session.

## Weekly loop

**Tuesday/Wednesday — pick the games**

```bash
python slate.py --week 3 --top 5 --detail
```

Ranks games by how determined the script is: large gap between the two
defenses AND a large expected margin. Games where the spread disagrees
with the defensive gap are dropped — if the market thinks the weak-defense
team wins, the mechanism isn't in play and the row is noise.

Defensive ratings are shrunk toward last season (`K_PRIOR_GAMES` in
`nflcal/data.py`). At week 3, two games is not a defensive rating; the
prior does most of the work and hands over as the season goes on.

**Before kickoff — predict, then price**

```bash
python log.py --week 3 -i
```

Guided prompts. It shows you the ranked games, then the candidate players
with their usage, and fills in defensive rank, implied total, spread and
roof from the slate so you never type them.

You supply three things: **your probability, your reasoning, and the odds.**
In that order — the prompt asks for your number and your reasoning *before*
it asks for the odds, deliberately. Anchoring on the line before forming
your own estimate is the one mistake that invalidates the whole record, so
the tool is built to make it awkward.

Devigging is automatic; you type the two American prices off the screen
(`-115` and `-105`) and it normalises them.

Predict **volume** — receptions, rush attempts — over yards wherever the
book offers it. Yards are volume times efficiency, and efficiency is noise
you are not trying to measure.

Four props per game across five games is twenty rows a week. Fifteen weeks
is three hundred resolutions, which is roughly where calibration starts
meaning something.

There is also a flag form (`python log.py --week 3 --game ... --my-p ...`)
if you ever want to script it.

**Monday — resolve**

```bash
python resolve.py --dry-run   # see what it would score
python resolve.py             # write the outcomes
```

Pulls actual results from nflverse and fills `outcome` by comparing the
real stat to your line. It is the only thing that ever touches an existing
row, and it writes only `outcome` and `resolved_at` — your probability and
your reasoning are frozen the moment you log them. A stat landing exactly
on the line is marked `push` and dropped from scoring.

**Never open `predictions.csv` in Excel.** It reformats dates and numbers
silently, and hand-editing destroys the one property that makes this record
worth showing anyone.

## Scoring

```bash
python score.py --plot calibration.png
```

- **brier naive** — base rate on everything. The floor. Beat it or you know nothing.
- **brier market** — the devigged line. The real bar.
- **brier you** — yours.

Then the calibration table (positive gap = underconfident) and the edge
test, which regresses outcome on `[market_p, edge]`. If the coefficient on
`edge` isn't clearly positive, your read is already in the price. That's a
result, not a failure — and a better thing to say out loud than a vague
claim of edge.

Don't read the numbers before 100 rows. Early results will be confident
and wrong, and adjusting to them means fitting noise.

## The specific claim worth testing

Defensive ratings lag reality early in a season — the market is still
working off last year's priors. If there's an inefficiency here, weeks 2–6
is the likely place:

> Among games with a large defensive mismatch in weeks 2–6, the market's
> implied probability on the trailing team's receiving-volume props is
> systematically too low.

Falsifiable, and testable with data you're collecting anyway.

## Files

| | |
|---|---|
| `nflcal/data.py` | nflverse pulls, shrunk defensive ratings, implied totals, usage |
| `slate.py` | ranks the week's games, shows usage for the selected ones |
| `log.py` | appends one prediction, devigs the odds |
| `score.py` | Brier, calibration curve, edge test |
| `predictions.csv` | the log (created on first write) |

Data is nflverse via `nflreadpy`, free and no API key. Lines come off a
sportsbook screen by hand — one number twice a week isn't worth an API
dependency, and a consensus across books beats any single feed.
