#!/usr/bin/env python3
"""Fill in outcomes automatically from nflverse once games have played.

    python resolve.py            # resolve everything it can
    python resolve.py --dry-run  # show what it would do, change nothing

This is the only thing that ever writes to an existing row, and it only
ever writes `outcome` and `resolved_at` — never your probability, never
your reasoning. Those are frozen the moment you log them.

A line landing exactly on the number is a push at the book; here it is
dropped rather than scored, because there is no outcome to score.
"""

from __future__ import annotations
import argparse, datetime as dt, pathlib
import pandas as pd
import nflreadpy as nfl

CSV = pathlib.Path(__file__).parent / "predictions.csv"

# market name in the log -> column in nflverse player stats
STAT = {
    "receptions": "receptions",
    "targets": "targets",
    "rush_attempts": "carries",
    "carries": "carries",
    "receiving_yards": "receiving_yards",
    "rushing_yards": "rushing_yards",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not CSV.exists():
        raise SystemExit("no predictions.csv yet")
    df = pd.read_csv(CSV)
    # all-empty columns read as float64, which then refuse strings
    for c in ("outcome", "resolved_at", "actual"):
        if c in df:
            df[c] = df[c].astype(object)
        else:
            df[c] = ""

    # skip rows are recorded but never scored; leave them alone
    unresolved = df.outcome.isna() | (df.outcome == "")
    not_skip = df.get("skip_reason", pd.Series([""] * len(df))).fillna("").eq("")
    pending = df[unresolved & not_skip & df.my_p.notna()]
    if pending.empty:
        print("nothing pending")
        return

    filled = pushes = waiting = dnp = 0
    for season in sorted(pending.season.unique()):
        ps = nfl.load_player_stats([int(season)]).to_pandas()
        sch = nfl.load_schedules([int(season)]).to_pandas()
        played = set(sch[sch.result.notna()].week.astype(int))
        for idx, r in pending[pending.season == season].iterrows():
            col = STAT.get(str(r.market))
            if col is None:
                print(f"  ? unknown market {r.market!r} on {r.id}")
                continue
            hit = ps[(ps.player_display_name == r.player) &
                     (ps.week == int(r.week))]
            if hit.empty:
                # No stats row can mean two very different things: the game
                # has not happened yet, or the player was inactive. Only the
                # second is resolvable, and it is NOT a loss — it is a row
                # with no outcome to score.
                if int(r.week) in played:
                    print(f"  – dnp   {r.player:22s} {r.market:14s} "
                          f"did not play; voided")
                    dnp += 1
                    if not a.dry_run:
                        df.loc[idx, "outcome"] = "dnp"
                        df.loc[idx, "resolved_at"] = dt.datetime.now(
                            dt.timezone.utc).isoformat(timespec="seconds")
                else:
                    waiting += 1
                continue

            actual = float(hit.iloc[0][col])
            line = float(r.line)
            now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            if actual == line:
                print(f"  = push  {r.player:22s} {r.market:14s} "
                      f"{actual:g} = {line:g}  (dropped)")
                pushes += 1
                if not a.dry_run:
                    df.loc[idx, "actual"] = actual
                    df.loc[idx, "outcome"] = "push"
                    df.loc[idx, "resolved_at"] = now
                continue

            over = actual > line
            outcome = int(over if r.side == "over" else not over)
            mark = "✓" if outcome else "✗"
            print(f"  {mark} {r.player:22s} {r.market:14s} "
                  f"actual {actual:5g}  line {line:5g}  "
                  f"{r.side:5s}  you said {float(r.my_p):.2f}")
            filled += 1
            if not a.dry_run:
                df.loc[idx, "actual"] = actual
                df.loc[idx, "outcome"] = outcome
                df.loc[idx, "resolved_at"] = now

    if not a.dry_run:
        df.to_csv(CSV, index=False)

    print(f"\nresolved {filled}   pushes {pushes}   dnp {dnp}   "
          f"still waiting {waiting}"
          + ("   (dry run, nothing written)" if a.dry_run else ""))
    if filled and not a.dry_run:
        print("commit predictions.csv, then: python score.py")


if __name__ == "__main__":
    main()
