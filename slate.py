#!/usr/bin/env python3
"""Pick the week's games worth predicting, and show the usage behind them.

    python slate.py                # next unplayed week
    python slate.py --week 3
    python slate.py --week 3 --top 4 --detail

Selection rule, which is the hypothesis stated as a filter:
we want games where ONE team is very likely to trail and the other to
lead, because that is the only condition under which game script is
strongly determined. That means a large gap between the two defenses
AND a spread pointing the same way. Games failing either test are
dropped — predicting them would dilute the sample with noise.
"""

from __future__ import annotations
import argparse
import pandas as pd
from nflcal import data as D

SEASON = 2026
pd.set_option("display.width", 200)

# --- narrowed by the 2024+2025 backtest, see validate.py -------------------
# Rushing is the robust half of the mechanism: the leading team's carries rise
# monotonically with the expected deficit (+0.2, +0.6, +1.3, +4.7 carries over
# the team's own mean across deficit bands). The passing half is NOT monotonic
# — it peaks in the 3-6 point band and goes NEGATIVE past 9 points, because a
# blowout makes both teams run out the clock.
#
# Selection accuracy also improves as the season goes on: the designated
# trailing team actually trailed 61.9% of the time in weeks 2-6 versus 70.9%
# from week 7. Early-season defensive ratings lean on the prior season and
# carry less information, which is the opposite of the original guess.
MIN_DEFICIT = 6.0     # below this the script is not determined enough
MAX_DEFICIT = 12.0    # above this it dies in garbage time
MIN_WEEK = 7          # before this the ratings are mostly last season


def build_slate(season: int, week: int, narrow: bool = False) -> pd.DataFrame:
    ratings = D.defense_ratings(season, through_week=week - 1)
    games = D.upcoming(season, week)

    rows = []
    for _, g in games.iterrows():
        home, away = g.home_team, g.away_team
        if home not in ratings.index or away not in ratings.index:
            continue
        h, a = ratings.loc[home], ratings.loc[away]
        hi, ai = D.implied_totals(g.spread_line, g.total_line)

        # Whose defense is worse? That team concedes points, trails, throws.
        def_gap = a.def_rank - h.def_rank          # >0 means away D is worse
        trailing = away if def_gap > 0 else home
        leading = home if def_gap > 0 else away

        # Does the market agree about who trails? spread_line > 0 = home favored.
        market_favors_home = g.spread_line > 0
        agrees = (market_favors_home and leading == home) or \
                 (not market_favors_home and leading == away)

        # Real kickoff, not an assumed Sunday afternoon. Thursday and Monday
        # games kick at 20:15 ET; a hardcoded time would leave a Thursday row
        # editable after it had already been played.
        et = "-04:00" if int(str(g.gameday)[5:7]) in (3,4,5,6,7,8,9,10) else "-05:00"
        gt = str(g.gametime) if isinstance(g.gametime, str) and ":" in str(g.gametime) else "13:00"
        kickoff = f"{g.gameday}T{gt}:00{et}"

        rows.append({
            "week": int(g.week),
            "date": str(g.gameday),
            "weekday": str(g.weekday),
            "kickoff": kickoff,
            "game": f"{away} @ {home}",
            "trailing": trailing,
            "leading": leading,
            "def_gap": abs(int(def_gap)),
            "spread": float(g.spread_line),
            "total": float(g.total_line),
            "imp_home": round(hi, 1),
            "imp_away": round(ai, 1),
            "roof": g.roof if isinstance(g.roof, str) else "unknown",
            "agrees": agrees,
            "trail_def_rank": int(ratings.loc[trailing, "def_rank"]),
            "lead_def_rank": int(ratings.loc[leading, "def_rank"]),
        })

    s = pd.DataFrame(rows)
    if s.empty:
        return s
    s["deficit"] = (s.imp_home - s.imp_away).abs()
    # Rank by how determined the game script is: big defensive gap AND a
    # big expected margin. Both must be large, so multiply rather than add.
    s["signal"] = s.def_gap * s.deficit
    s = s[s.agrees]                       # drop games where the market disagrees
    if narrow:
        s = s[(s.deficit >= MIN_DEFICIT) & (s.deficit <= MAX_DEFICIT)]
    return s.sort_values("signal", ascending=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--top", type=int, default=None,
                    help="cap the number of games; default is every game that passes")
    ap.add_argument("--detail", action="store_true",
                    help="show usage tables for the selected games")
    ap.add_argument("--narrow", action="store_true",
                    help="restrict to the backtested deficit band; OFF by default, "
                         "because narrowing on a backtest is what the season is "
                         "meant to test rather than assume")
    ap.add_argument("--json", action="store_true",
                    help="emit the slate plus usage as JSON, to paste into the logger UI")
    a = ap.parse_args()

    if a.json:
        import json
        week = a.week or D.last_completed_week(SEASON) + 1
        s = build_slate(SEASON, week, narrow=a.narrow)
        paces = D.pace(SEASON, week - 1)
        games, queue = [], []
        for _, g in (s.head(a.top) if a.top else s).iterrows():
            entry = {k: (v.item() if hasattr(v, "item") else v)
                     for k, v in g.to_dict().items()}
            for role, team, pos in (("trailing", g.trailing, "WR"),
                                    ("leading", g.leading, "RB"),
                                    ("trailing_rbs", g.trailing, "RB"),
                                    ("leading_wrs", g.leading, "WR")):
                u = D.usage(SEASON, week - 1, team)
                u = u[u.position == pos].sort_values(
                    "tgt_share" if pos == "WR" else "car", ascending=False).head(5)
                key = role + "_players" if role in ("trailing", "leading") else role
                entry[key] = [
                    {"name": r.player_display_name, "pos": r.position,
                     "tgt": round(float(r.tgt), 1),
                     "share": round(float(r.tgt_share), 3),
                     "rec": round(float(r.rec), 1),
                     "car": round(float(r.car), 1),
                     "snap": round(float(r.snap_pct or 0), 2)}
                    for r in u.itertuples()]
            games.append(entry)

            # The queue is generated, not chosen — choosing is how a
            # calibration record quietly becomes a record of the predictions
            # you felt like making.
            #
            # Two CORE props state the hypothesis: the trailing team throws
            # more, the leading team runs more.
            # Two CONTROL props state its mirror image: the leading team's
            # receivers should do LESS, the trailing team's back should do
            # LESS. Without these every prop points the same way and a
            # general bias toward overs would look like skill.
            tp, lp = entry["trailing_players"], entry["leading_players"]
            tp_rb, lp_wr = entry.get("trailing_rbs", []), entry.get("leading_wrs", [])
            pc = paces.to_dict()

            def stub(team, side, pos, pl, market, tier, expect):
                own = entry["trail_def_rank"] if side == "trailing" else entry["lead_def_rank"]
                opp = entry["lead_def_rank"] if side == "trailing" else entry["trail_def_rank"]
                is_home = entry["game"].split(" @ ")[1] == team
                return {"game": entry["game"], "game_date": entry["date"],
                        "kickoff": entry["kickoff"],
                        "weekday": entry["weekday"],
                        "team": team, "side_of_mechanism": side, "position": pos,
                        "player": pl["name"], "market": market,
                        "tier": tier, "expect": expect,
                        # --- the hypothesis variables ---
                        "own_def_rank": own, "def_rank_opp": opp,
                        "def_gap": entry["def_gap"],
                        "implied_total": entry["imp_home"] if is_home else entry["imp_away"],
                        "deficit": entry["deficit"],
                        # --- controls ---
                        "spread": entry["spread"], "total": entry["total"],
                        "roof": entry["roof"],
                        "pace": round(float(pc.get(team, float("nan"))), 1),
                        "base_tgt_share": pl.get("share"), "base_rec": pl.get("rec"),
                        "base_car": pl.get("car"), "base_snap": pl.get("snap")}

            # Four props per game, stating the mechanism in both directions
            # for BOTH positions. The tiers label which way the hypothesis
            # points, not how much I believe it — belief is what the season
            # measures. Controls exist so that a general bias toward overs
            # cannot masquerade as insight.
            if lp:
                queue.append(stub(entry["leading"], "leading", "RB", lp[0],
                                  "rush_attempts", "core_rush", "over"))
            if tp_rb:
                queue.append(stub(entry["trailing"], "trailing", "RB", tp_rb[0],
                                  "rush_attempts", "control_rush", "under"))
            if tp:
                queue.append(stub(entry["trailing"], "trailing", "WR", tp[0],
                                  "receptions", "core_pass", "over"))
            if lp_wr:
                queue.append(stub(entry["leading"], "leading", "WR", lp_wr[0],
                                  "receptions", "control_pass", "under"))

        # Soonest kickoff first: a Thursday game locks four days before the
        # Sunday ones, so it has to be worked first in a single sitting.
        tier_order = {"core_rush": 0, "control_rush": 1,
                      "core_pass": 2, "control_pass": 3}
        queue.sort(key=lambda q: (q["kickoff"], tier_order.get(q["tier"], 9),
                                  q["side_of_mechanism"]))
        print(json.dumps({"season": SEASON, "week": week,
                          "games": games, "queue": queue}, indent=1))
        return

    week = a.week or D.last_completed_week(SEASON) + 1
    s = build_slate(SEASON, week, narrow=a.narrow)
    if s.empty:
        print(f"No qualifying games in week {week}.")
        return

    cols = ["date", "game", "trailing", "leading", "def_gap", "spread",
            "total", "imp_home", "imp_away", "deficit", "roof", "signal"]
    print(f"\nWeek {week} — games ranked by how determined the script is\n")
    print(s[cols].head(a.top) .to_string(index=False) if a.top else s[cols].to_string(index=False))
    print("\ntrailing = worse defense, expected to trail and throw"
          "\nleading  = better defense, expected to lead and run"
          "\ngames where the spread disagrees with the defensive gap are dropped\n")

    if not a.detail:
        print("run with --detail for usage tables")
        return

    for _, g in (s.head(a.top) if a.top else s).iterrows():
        print("=" * 72)
        print(f"{g.game}   {g.date}   spread {g.spread:+.1f}   total {g.total}")
        for side, team in (("TRAILING — predict receiving volume OVER", g.trailing),
                           ("LEADING  — predict rushing volume OVER", g.leading)):
            u = D.usage(SEASON, week - 1, team)
            print(f"\n  {side}: {team}  (def rank "
                  f"{g.trail_def_rank if team == g.trailing else g.lead_def_rank}/32)")
            if u.empty:
                print("    no usage data yet")
                continue
            is_trail = team == g.trailing
            pos, by = ("WR", "tgt_share") if is_trail else ("RB", "car")
            keep = u[u.position == pos].sort_values(by, ascending=False).head(3)
            if keep.empty:
                keep = u.head(3)
            print(keep.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
        print()


if __name__ == "__main__":
    main()
