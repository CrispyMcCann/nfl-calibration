#!/usr/bin/env python3
"""Backtest the MECHANISM, before betting a season on it.

    python validate.py --season 2025                 # link-based mechanism test
    python validate.py --season 2024 --mode brier    # per-variant Brier backtest

Two modes:

  * link (default): the hypothesis is a three-link chain —
    bad defence → the team trails → the team throws more. Each link can
    fail independently; if any is weak the whole design is pointless.
    This mode walks a completed season week by week, builds the slate
    using ONLY data available before that week, and checks what actually
    happened. It tests whether the mechanism exists, not whether you can
    beat the market.

  * brier: evaluates the probability formula itself. Runs each prop
    through the Week 6 formula under baseline (no R-002, no R-003) and
    the three variants (r002, r003, all), scores Brier against a
    synthetic half-point line proxy = round(2 × trailing-median) / 2 - 0.5,
    and prints per-variant Brier deltas by season. The absolute Brier is
    not comparable to the live record because the lines are a proxy —
    only the DELTAS between variants on the same proxy are read as
    signal. Codification policy from METHODOLOGY.md: drop any rule whose
    delta < 0 across all backtest seasons.
"""

from __future__ import annotations
import argparse
import numpy as np
import pandas as pd
import nflreadpy as nfl
from nflcal import data as D
from slate import build_slate
from tools.calc import compute_myp

pd.set_option("display.width", 200)


def _run_link_mode(a) -> None:
    """The original mechanism validator — unchanged from Week 5 behaviour."""
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
                "trail_att_vs_mean": float(tr.attempts - season_mean.loc[g.trailing, "mean_att"]),
                "trail_pr_vs_mean": float(tr.pass_rate - season_mean.loc[g.trailing, "mean_pr"]),
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


# ------------- brier mode ---------------------------------------------------

def _actual_for(ps: pd.DataFrame, player: str, week: int, market: str) -> float | None:
    """Pull the player's actual stat value for the given week. Returns None
    if the player has no stats row that week (missed or DNP)."""
    hit = ps[(ps.player_display_name == player) & (ps.week == week)]
    if hit.empty:
        return None
    r = hit.iloc[0]
    val = r.carries if market == "rushing" else r.receptions
    return float(val) if val is not None and not (isinstance(val, float) and np.isnan(val)) else None


def _line_proxy(ps: pd.DataFrame, player: str, through_week: int, market: str) -> float | None:
    """Synthetic half-point line: round(2 × median of the player's actual
    value over the games they played through_week ≤ this one) / 2 - 0.5.
    Returns None if the player has fewer than 2 games on record."""
    hit = ps[(ps.player_display_name == player) & (ps.week <= through_week)]
    if len(hit) < 2:
        return None
    col = hit.carries if market == "rushing" else hit.receptions
    med = float(col.median())
    return round(2 * med) / 2 - 0.5


def _run_brier_mode(a) -> None:
    """Per-variant Brier backtest. See the module docstring for policy."""
    seasons = [int(x) for x in a.seasons.split(",")]
    variants = ["baseline", "r002", "r003", "all"] if a.variant == "all-table" else [a.variant]

    print("line is a synthetic proxy (median-rounded half-point); absolute")
    print("Brier here is NOT comparable to the live record. Read DELTAS")
    print("between variants on the same proxy, not levels.\n")

    agg_rows = []
    for season in seasons:
        print(f"=== season {season} ===")
        ps = D._player_stats(season)
        sch = nfl.load_schedules([season]).to_pandas()
        if sch[sch.result.notna()].empty:
            print(f"  no completed games for {season}; skip")
            continue
        last = int(sch[sch.result.notna()].week.max())

        rows = []
        for wk in range(max(a.from_week, 4), last + 1):
            try:
                s = build_slate(season, wk)
            except Exception:
                continue
            if s.empty:
                continue
            paces = D.pace(season, wk - 1).to_dict()
            ratings = D.defense_ratings(season, through_week=wk - 1)

            for _, g in s.iterrows():
                for role, team, pos, market, side in (
                    ("leading",  g.leading,  "RB", "rushing",   "over"),
                    ("trailing", g.trailing, "RB", "rushing",   "under"),
                    ("trailing", g.trailing, "WR", "receiving", "over"),
                    ("leading",  g.leading,  "WR", "receiving", "under"),
                ):
                    pl = D.primary_at(season, wk - 1, team, pos)
                    if pl is None:
                        continue
                    actual = _actual_for(ps, pl["name"], wk, market)
                    if actual is None:
                        continue
                    line = _line_proxy(ps, pl["name"], wk - 1, market)
                    if line is None:
                        continue
                    pace = float(paces.get(team, 60.0))
                    opp_team = g.leading if role == "trailing" else g.trailing
                    pass_rk = int(ratings.loc[opp_team, "pass_def_rank"]) \
                        if opp_team in ratings.index else None
                    rush_rk = int(ratings.loc[opp_team, "rush_def_rank"]) \
                        if opp_team in ratings.index else None
                    elev = D.elevation_r003(season, wk, team, pos,
                                            pl.get("rec", 0.0),
                                            pl.get("car", 0.0),
                                            exclude_player=pl["name"])
                    base_raw   = pl["car"] if market == "rushing" else pl["rec"]
                    base_r003  = elev["base_car"] if market == "rushing" else elev["base_rec"]

                    # Outcome: 1 if actual > line (over hit), 0 if under hit.
                    # Push impossible on half-point line.
                    over_hit = 1 if actual > line else 0
                    y = over_hit if side == "over" else (1 - over_hit)

                    # Each variant's my_p.
                    variants_myp = {
                        "baseline": compute_myp(market, side, base_raw, pace, line,
                                                pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                apply_r002=False)["my_p"],
                        "r002":     compute_myp(market, side, base_raw, pace, line,
                                                pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                apply_r002=True)["my_p"],
                        "r003":     compute_myp(market, side, base_r003, pace, line,
                                                pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                apply_r002=False)["my_p"],
                        "all":      compute_myp(market, side, base_r003, pace, line,
                                                pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                apply_r002=True)["my_p"],
                    }
                    rows.append({"season": season, "week": wk, "player": pl["name"],
                                 "market": market, "side": side,
                                 "line": line, "actual": actual, "y": y,
                                 "r003_fired": elev["fires"],
                                 **{f"myp_{v}": variants_myp[v] for v in variants_myp}})

        df = pd.DataFrame(rows)
        if df.empty:
            print(f"  no scorable props across weeks {a.from_week}-{last}")
            continue
        print(f"  {len(df)} props across weeks {a.from_week}-{last}")
        for v in ("baseline", "r002", "r003", "all"):
            b = ((df[f"myp_{v}"] - df.y) ** 2).mean()
            agg_rows.append({"season": season, "variant": v, "n": len(df), "brier": b})

        r003_hits = df[df.r003_fired]
        if len(r003_hits):
            print(f"  R-003 fired on {len(r003_hits)} props "
                  f"(sub-Brier baseline={((r003_hits.myp_baseline-r003_hits.y)**2).mean():.4f}, "
                  f"r003={((r003_hits.myp_r003-r003_hits.y)**2).mean():.4f})")
        print()

    if not agg_rows:
        raise SystemExit("no backtest data produced; try --from-week lower")

    out = pd.DataFrame(agg_rows).pivot(index="season", columns="variant", values="brier")
    out = out[["baseline", "r002", "r003", "all"]]
    print("Brier per variant per season (lower is better)")
    print(out.to_string(float_format=lambda v: f"{v:.4f}"))
    print()
    print("deltas vs baseline (negative = variant WORSE; policy says drop a")
    print("rule only when every season shows delta < 0)")
    delta = out.sub(out["baseline"], axis=0).drop(columns=["baseline"])
    # Delta polarity convention: positive = variant lowered Brier = better.
    delta = -delta
    print(delta.to_string(float_format=lambda v: f"{v:+.4f}"))
    print()
    worst = delta.min()
    for v in ("r002", "r003", "all"):
        verdict = "KEEP" if worst[v] >= 0 else "DROP"
        print(f"  {v}:  worst-season delta {worst[v]:+.4f}   →  {verdict}")


# ------------- entry point --------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--from-week", type=int, default=4,
                    help="skip the first weeks; ratings are mostly prior before then")
    ap.add_argument("--mode", choices=("link", "brier"), default="link")
    ap.add_argument("--seasons", default="2023,2024,2025",
                    help="brier mode only: comma-separated seasons to backtest")
    ap.add_argument("--variant", default="all-table",
                    choices=("baseline", "r002", "r003", "all", "all-table"),
                    help="brier mode only; 'all-table' prints every variant")
    a = ap.parse_args()
    if a.mode == "link":
        _run_link_mode(a)
    else:
        _run_brier_mode(a)


if __name__ == "__main__":
    main()
