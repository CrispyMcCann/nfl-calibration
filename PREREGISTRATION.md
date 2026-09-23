# Pre-registration — NFL game script and player volume

**Registered 23 September 2026, before week 3 of the 2026 season.**
Commit this file before logging a single prediction. Its git timestamp is
what separates a confirmatory analysis from one reconstructed afterwards.

---

## Disclosure, first, because it matters

Before this was written, a backtest was run over 2024 and 2025 — 356 games
passing the selection filter — and **I was told the result.** I know, going
in, that:

- the designated trailing team actually trailed about 68% of the time
- the defensive-rank gap correlates +0.076 with trailing, while the
  market's implied deficit correlates +0.302
- leading-team carries rise monotonically with expected deficit
- trailing-team pass attempts do **not**: a spike in the 3–6 point band,
  and a reversal past 9 points where blowouts make both teams run

**My 2026 forecasts are therefore not naive**, and the receiving half is
the more exposed one. I know the aggregate direction, though not what it
implies for any individual game.

My own read is that I did not absorb those numbers in any working way and
that I am forecasting from the mechanism as I reasoned it out beforehand.
That is recorded as what I believe, not as evidence — this whole project
exists because self-assessment of one's own judgment is unreliable, and it
would be inconsistent to exempt this one claim from that. Exposure is not
the same as influence, and neither is introspection a measurement of it.

**So it gets measured instead.** If I am shading toward the market on
receiving props, the signature is a systematically smaller `|edge|` there
than on rushing props, where I have no reason to hedge. Comparing the
distribution of `|my_p − market_p|` across the rushing and receiving tiers
turns an unresolvable argument about my mental state into a number. It is
added to the analysis plan below and will be reported whichever way it
comes out.

The season is being run wide anyway — every qualifying game, both positions
— because the narrowing was derived by slicing the historical data five
ways, and testing a data-mined restriction is a weaker exercise than
testing the mechanism I reasoned out before touching a dataset.

---

## The mechanism, as reasoned from first principles

A team with a weak defense concedes points, therefore trails, therefore
throws more to catch up. Its opponent leads, therefore runs to drain the
clock. Player volume follows team volume.

This is a causal chain with four separately testable links, and the season
tests all four in both directions.

## H1 — rushing, the leading team

> In games where the defensive-rank gap and the spread agree on who trails,
> the leading team's primary back takes more rush attempts than his recent
> per-game average.

**Predicted: holds.** Direction: over.

## H2 — rushing, the trailing team (control)

> The trailing team's primary back takes fewer.

**Predicted: holds.** Direction: under. Exists so that a general bias
toward overs cannot masquerade as insight — if H1 beats the market and H2
does not, I am not seeing game script, I am just taking overs.

## H3 — receiving, the trailing team

> The trailing team's WR1 records more receptions than his recent average.

**Predicted: fails, or holds only in a narrow band of expected deficit.**
This is the half the backtest contradicted. Logged at full weight anyway,
because finding it prospectively is the point.

## H4 — receiving, the leading team (control)

> The leading team's WR1 records fewer.

**Predicted: fails for the same reason as H3.**

## H5 — the tradeable claim

> Conditional on the DraftKings devigged price, my stated probability adds
> information: in a logistic regression of outcome on `[market_p, edge]`,
> the coefficient on `edge` is positive.

**Predicted: it is not.** Recorded in advance so that a null result counts
as the expectation rather than a disappointment.

## H6 — calibration, which is independent of all of the above

> Across all logged predictions, my stated probabilities are well
> calibrated: in each decile bucket, observed frequency falls within
> sampling error of stated probability.

This is the one that pays off whether or not any structural claim survives,
and the one nobody who talks confidently about probability has usually
bothered to check about themselves.

---

## Design, fixed in advance

**Selection.** Every game where the defensive-rank gap and the spread agree
on who trails. No deficit filter, no week filter, no discretion — about ten
games a week. `slate.py` generates the list; I predict every item on it.
Skips require a written reason and are recorded.

**Props per game.** Four: leading RB1 rush attempts (over), trailing RB1
rush attempts (under), trailing WR1 receptions (over), leading WR1
receptions (under).

**Probabilities.** Written before the odds screen is opened. The logging
tool does not reveal the price fields until the probability and the
reasoning are entered.

**Prices.** DraftKings only, devigged by normalising both sides. Recorded
at the moment of prediction; the closing price recorded separately before
each game's own kickoff.

**Locking.** Rows lock at their real kickoff. Amendments before kickoff are
kept in the record rather than overwritten.

**Analysis, specified now.**

- Brier for me, the market, and the base rate.
- Calibration curve in deciles, with the plot.
- Logistic regression of outcome on `[market_p, edge]` for H5.
- Rush and pass tiers scored **separately**, never pooled.
- Core and control scored separately within each.
- Results split by expected-deficit band (0–3, 3–6, 6–9, 9–12, 12+) and by
  early season (weeks 3–6) versus late (7+). These splits are named here so
  they are not chosen after seeing the answer.
- Effective sample size is the **game** count, not the row count, because
  props within a game share one script.
- **Contamination check:** mean and distribution of `|my_p − market_p|` on
  rushing props versus receiving props. A materially smaller spread on
  receiving is evidence that the disclosed exposure moved me toward the
  price, whatever I believed at the time. Reported either way.

**Stopping rule.** Run to week 18 regardless of interim results. No
mid-season changes to the filter, the prop set, or the analysis. Anything I
want to change gets written down and tested next season.

---

## After the season

1. Score the prospective record against every hypothesis above.
2. **Then** re-run the historical backtest independently and check whether
   history reaches the same conclusions. Agreement is replication;
   disagreement is the interesting case and gets investigated rather than
   explained away.
3. Only then start modelling — logistic regression on the recorded
   features, shrinkage on small-sample usage — and compare the model's
   Brier against both my own judgment and the market's.

The order matters. The mechanism was reasoned out before any data was
examined; the prospective test comes next; the backtest is a replication of
that test, not its source. Doing it the other way round means confirming a
hypothesis that the same data produced.
