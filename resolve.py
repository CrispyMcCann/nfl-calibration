#!/usr/bin/env python3
"""Fill in outcomes automatically from nflverse once games have played.

    python resolve.py            # resolve everything it can
    python resolve.py --dry-run  # show what it would do, change nothing

It writes only `actual`, `outcome` and `resolved_at` on existing rows —
never your probability, never your reasoning. Those are frozen the moment
you log them. Every value is read and written as text, so the rest of the
file is left byte-for-byte as it was.

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
    # text in, text out: nothing else in the file gets reformatted
    df = pd.read_csv(CSV, dtype=str, keep_default_na=False)
    for c in ("outcome", "resolved_at", "actual", "skip_reason"):
        if c not in df:
            df[c] = ""

    # skip rows are recorded but never scored; leave them alone
    pending = df[df.outcome.eq("") & df.skip_reason.eq("") & df.my_p.ne("")]
    if pending.empty:
        print("nothing pending")
        return

    filled = pushes = waiting = dnp = 0
    for season in sorted(pending.season.unique()):
        ps = nfl.load_player_stats([int(season)]).to_pandas()
        sch = nfl.load_schedules([int(season)]).to_pandas()
        # Per game, not per week: one finished Thursday game must not make
        # the rest of that week's unplayed games look finished.
        done = sch[sch.result.notna()]
        played = {(int(w), f"{aw} @ {hm}") for w, aw, hm in
                  done[["week", "away_team", "home_team"]].itertuples(index=False)}
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
                if (int(r.week), str(r.game)) in played:
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
                    df.loc[idx, "actual"] = f"{actual:g}"
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
                df.loc[idx, "actual"] = f"{actual:g}"
                df.loc[idx, "outcome"] = str(outcome)
                df.loc[idx, "resolved_at"] = now

    if not a.dry_run:
        df.to_csv(CSV, index=False, lineterminator="\n")

    print(f"\nresolved {filled}   pushes {pushes}   dnp {dnp}   "
          f"still waiting {waiting}"
          + ("   (dry run, nothing written)" if a.dry_run else ""))
    if filled and not a.dry_run:
        print("commit predictions.csv, then: python score.py")


if __name__ == "__main__":
    main()
