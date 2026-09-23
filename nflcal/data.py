"""Pull NFL data and turn it into the few numbers the hypothesis needs.

Everything here comes from nflverse (free, no key). The only non-obvious
step is the shrinkage in `defense_ratings` — at week 3 a team has played
two games, which is not enough to rate a defense, so we pull the current
season's number toward last season's in proportion to how little data we
have. See the K_PRIOR_GAMES note below.
"""

from __future__ import annotations
import functools
import numpy as np
import pandas as pd
import nflreadpy as nfl

# How many games of "last season" evidence the prior is worth.
# rating = (n * current + k * prior) / (n + k)
# n=2, k=4  -> one third current, two thirds prior.  By week 10 it flips.
K_PRIOR_GAMES = 4


@functools.lru_cache(maxsize=8)
def _schedules(season: int) -> pd.DataFrame:
    return nfl.load_schedules([season]).to_pandas()


@functools.lru_cache(maxsize=8)
def _team_stats(season: int) -> pd.DataFrame:
    return nfl.load_team_stats([season]).to_pandas()


@functools.lru_cache(maxsize=8)
def _player_stats(season: int) -> pd.DataFrame:
    return nfl.load_player_stats([season]).to_pandas()


@functools.lru_cache(maxsize=8)
def _snaps(season: int) -> pd.DataFrame:
    return nfl.load_snap_counts([season]).to_pandas()


def _allowed(season: int, through_week: int | None = None) -> pd.DataFrame:
    """Per-team defensive numbers: what each team GAVE UP, per game.

    team_stats is offensive, one row per team per game. To get what a
    defense allowed we just relabel each row under its opponent.
    """
    ts = _team_stats(season)
    ts = ts[ts.season_type == "REG"] if "season_type" in ts else ts
    if through_week is not None:
        ts = ts[ts.week <= through_week]
    if ts.empty:
        return pd.DataFrame()

    off = ts.rename(columns={"team": "offense", "opponent_team": "defense"})
    g = off.groupby("defense").agg(
        games=("week", "nunique"),
        pass_yds_allowed=("passing_yards", "mean"),
        rush_yds_allowed=("rushing_yards", "mean"),
        pass_epa_allowed=("passing_epa", "mean"),
        rush_epa_allowed=("rushing_epa", "mean"),
        pass_att_faced=("attempts", "mean"),
        rush_att_faced=("carries", "mean"),
    )
    # plays per game the DEFENCE faced, a rough pace proxy for the opponent
    g["plays_faced"] = g.pass_att_faced + g.rush_att_faced
    return g


def defense_ratings(season: int, through_week: int) -> pd.DataFrame:
    """Defensive quality, shrunk toward the prior season.

    Returns one row per team with shrunk per-game figures plus ranks.
    Rank 1 = best defense (fewest yards / lowest EPA allowed).
    """
    cur = _allowed(season, through_week)
    prior = _allowed(season - 1)

    metrics = ["pass_yds_allowed", "rush_yds_allowed",
               "pass_epa_allowed", "rush_epa_allowed"]

    out = cur.copy()
    n = out["games"].clip(lower=0)
    k = K_PRIOR_GAMES

    for m in metrics:
        p = prior[m] if (not prior.empty and m in prior) else pd.Series(dtype=float)
        p = p.reindex(out.index)
        # league mean stands in wherever a prior is missing (new/renamed team)
        p = p.fillna(out[m].mean())
        out[m] = (n * out[m] + k * p) / (n + k)

    for m in metrics:
        out[m + "_rank"] = out[m].rank(method="min").astype(int)

    # One composite. Equal weight on the two EPA measures — EPA is a better
    # defensive signal than raw yardage because it accounts for down,
    # distance and field position.
    out["def_score"] = (out["pass_epa_allowed_rank"] + out["rush_epa_allowed_rank"]) / 2
    out["def_rank"] = out["def_score"].rank(method="min").astype(int)
    return out.sort_values("def_rank")


def implied_totals(spread_line: float, total_line: float) -> tuple[float, float]:
    """(home_implied_points, away_implied_points).

    nflverse spread_line is the HOME team's line: positive = home favored.
    Half the total, then move each side by half the spread.
    """
    half = total_line / 2.0
    return half + spread_line / 2.0, half - spread_line / 2.0


def usage(season: int, through_week: int, team: str, last_n: int = 3) -> pd.DataFrame:
    """Recent usage for one team's skill players.

    Volume is what the hypothesis is about, so this reports targets,
    target share, receptions and carries — not yards.
    """
    ps = _player_stats(season)
    ps = ps[(ps.team == team) & (ps.week <= through_week)]
    if ps.empty:
        return pd.DataFrame()
    recent = ps[ps.week > max(0, through_week - last_n)]

    agg = recent.groupby(["player_display_name", "position"]).agg(
        gms=("week", "nunique"),
        tgt=("targets", "mean"),
        tgt_share=("target_share", "mean"),
        rec=("receptions", "mean"),
        car=("carries", "mean"),
    ).reset_index()

    sn = _snaps(season)
    sn = sn[(sn.team == team) & (sn.week <= through_week)]
    sn = sn[sn.week > max(0, through_week - last_n)]
    if not sn.empty:
        snap = sn.groupby("player")["offense_pct"].mean().rename("snap_pct")
        agg = agg.merge(snap, left_on="player_display_name",
                        right_index=True, how="left")
    else:
        agg["snap_pct"] = np.nan

    agg = agg[agg.position.isin(["WR", "RB", "TE"])]
    return agg.sort_values(["position", "tgt_share"], ascending=[True, False])


def upcoming(season: int, week: int) -> pd.DataFrame:
    s = _schedules(season)
    return s[(s.week == week)].copy()


def last_completed_week(season: int) -> int:
    s = _schedules(season)
    done = s[s.result.notna()]
    return int(done.week.max()) if len(done) else 0


def pace(season: int, through_week: int) -> pd.Series:
    """Offensive plays per game. Volume props scale with it, so it is a
    control, not part of the hypothesis."""
    ts = _team_stats(season)
    ts = ts[ts.week <= through_week]
    if ts.empty:
        return pd.Series(dtype=float)
    ts = ts.assign(plays=ts.attempts + ts.carries)
    return ts.groupby("team")["plays"].mean()
