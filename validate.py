#!/usr/bin/env python3
"""Backtest the MECHANISM, before betting a season on it.

    python validate.py --season 2025

The hypothesis is a three-link chain:

  bad defence  ->  the team trails  ->  the team throws more

Each link can fail independently, and if any of them is weak the whole
design is pointless. This script walks a completed season week by week,
builds the slate using ONLY data available before that week, and then
checks what actually happened.

It does not test whether you can beat the market. It tests whether the
thing you are claiming to see is there at all.
"""

from __future__ import annotations
import argparse
import numpy as np
import pandas as pd
import nflreadpy as nfl
from nflcal import data as D
from slate import build_slate

pd.set_option("display.width", 200)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--from-week", type=int, default=4,
                    help="skip the first weeks; ratings are mostly prior before then")
    a = ap.parse_args()

    sch = nfl.load_schedules([a.season]).to_pandas()
    ts = nfl.load_team_stats([a.season]).to_pandas()
    if sch[sch.result.notna()].empty:
        raise SystemExit(f"no completed games for {a.season}")

    ts = ts.assign(pass_rate=ts.attempts / (ts.attempts + ts.carries))
    season_mean = ts.groupby("team").agg(
        mean_att=("attempts", "mean"),
        mean_car=("carries", "mean"),
        mean_pr=("pass_rate", "mean"))

    last = int(sch[sch.result.notna()].week.max())
    rows = []
    for wk in range(a.from_week, last + 1):
        try:
            s = build_slate(a.season, wk)
        except Exception:
            continue
        if s.empty:
            continue
        played = sch[(sch.week == wk) & (sch.result.notna())]
        for _, g in s.iterrows():
            m = played[(played.home_team + "|" + played.away_team)
                       == (g.game.split(" @ ")[1] + "|" + g.game.split(" @ ")[0])]
            if m.empty:
                continue
            m = m.iloc[0]
            home = g.game.split(" @ ")[1]
            # margin from the designated trailing team's point of view
            margin = m.result if g.trailing == home else -m.result

            wkt = ts[(ts.week == wk)]
            tr = wkt[wkt.team == g.trailing]
            ld = wkt[wkt.team == g.leading]
            if tr.empty or ld.empty:
                continue
            tr, ld = tr.iloc[0], ld.iloc[0]

            rows.append({
                "week": wk, "game": g.game, "def_gap": g.def_gap,
                "deficit": g.deficit, "signal": g.signal,
                "trail_margin": float(margin),
                "trailed": margin < 0,
                # link 2 -> 3: did the trailing team throw more than usual?
                "trail_att_vs_mean": float(tr.attempts - season_mean.loc[g.trailing, "mean_att"]),
                "trail_pr_vs_mean": float(tr.pass_rate - season_mean.loc[g.trailing, "mean_pr"]),
                # and did the leading team run more than usual?
                "lead_car_vs_mean": float(ld.carries - season_mean.loc[g.leading, "mean_car"]),
                "lead_pr_vs_mean": float(ld.pass_rate - season_mean.loc[g.leading, "mean_pr"]),
            })

    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("no games survived the filter — nothing to validate")

    n = len(df)
    print(f"\n{a.season}: {n} games passed the filter, weeks {a.from_week}–{last}\n")

    print("LINK 1 — does the designated trailing team actually trail?")
    hit = df.trailed.mean()
    print(f"  trailed (lost the game)      {hit:.1%}   of {n}")
    print(f"  mean margin                  {df.trail_margin.mean():+.1f} points")
    print("  a coin flip here means the selection rule does not work.\n")

    print("LINK 2 — given that, does volume actually move?")
    t = df[df.trailed]
    print(f"  among the {len(t)} that DID trail:")
    print(f"    trailing team pass attempts vs own mean   {t.trail_att_vs_mean.mean():+.2f}")
    print(f"    trailing team pass RATE vs own mean       {t.trail_pr_vs_mean.mean():+.3f}")
    print(f"    leading  team carries vs own mean         {t.lead_car_vs_mean.mean():+.2f}")
    print(f"    leading  team pass rate vs own mean       {t.lead_pr_vs_mean.mean():+.3f}")

    f = df[~df.trailed]
    if len(f):
        print(f"\n  among the {len(f)} where the pick was WRONG (they led instead):")
        print(f"    trailing team pass attempts vs own mean   {f.trail_att_vs_mean.mean():+.2f}")
        print(f"    trailing team pass RATE vs own mean       {f.trail_pr_vs_mean.mean():+.3f}")
        print("    these are the rows that cost you. If the gap between the two"
              "\n    blocks is small, game script is not doing much work.")

    print("\nDOES THE SIGNAL RANK ANYTHING? split by the selection score\n")
    df["band"] = pd.qcut(df.signal, 3, labels=["weak", "mid", "strong"], duplicates="drop")
    band = df.groupby("band", observed=True).agg(
        n=("trailed", "size"),
        trailed=("trailed", "mean"),
        margin=("trail_margin", "mean"),
        trail_pass_rate=("trail_pr_vs_mean", "mean"),
        lead_carries=("lead_car_vs_mean", "mean"))
    print(band.to_string(float_format=lambda v: f"{v:.3f}"))
    print("\n  'trailed' should climb from weak to strong. If it is flat, the"
          "\n  signal score is not ranking anything and the filter is arbitrary.")

    c = df[["def_gap", "deficit", "signal"]].corrwith(df.trailed.astype(float))
    print("\ncorrelation with actually trailing")
    print(c.to_string(float_format=lambda v: f"{v:+.3f}"))
    print("\n  def_gap is YOUR variable; deficit is the market's. If the market's"
          "\n  correlation is much higher, the spread already contains your idea.")


if __name__ == "__main__":
    main()
