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


@functools.lru_cache(maxsize=8)
def _depth(season: int) -> pd.DataFrame:
    return nfl.load_depth_charts([season]).to_pandas()


@functools.lru_cache(maxsize=8)
def _injuries(season: int) -> pd.DataFrame:
    return nfl.load_injuries([season]).to_pandas()


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
    # Convenience aliases that R-002 consumes. Position-specific EPA ranks
    # are the signal — raw-yardage ranks are already on the frame too but
    # EPA is the better defensive proxy (see composite note above).
    out["pass_def_rank"] = out["pass_epa_allowed_rank"]
    out["rush_def_rank"] = out["rush_epa_allowed_rank"]
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


def depth_order(season: int, team: str, position: str,
                as_of: str | None = None) -> pd.DataFrame:
    """Depth-chart ordering for a team's position group.

    nflverse depth-chart schemas differ by era:
      * 2025+ (dt/team/pos_abb/pos_rank/player_name): timestamped
        snapshots; latest snapshot is used unless `as_of` ceiling is set.
      * 2024- (season/club_code/week/depth_team/position/full_name):
        weekly rows; `as_of` is read as a week number (int-or-string),
        otherwise the latest week in the frame is used.

    Returned frame is normalised to columns {player_name, pos_rank} so
    callers downstream read a single schema. Rows are sorted by
    `pos_rank` ascending.
    """
    d = _depth(season)
    if d.empty:
        return pd.DataFrame(columns=["player_name", "pos_rank"])

    if "team" in d.columns and "pos_abb" in d.columns:
        # 2025+ schema
        d = d[(d.team == team) & (d.pos_abb == position)]
        if d.empty:
            return pd.DataFrame(columns=["player_name", "pos_rank"])
        if as_of is not None:
            d = d[d.dt <= as_of]
            if d.empty:
                return pd.DataFrame(columns=["player_name", "pos_rank"])
        latest = d.dt.max()
        d = d[d.dt == latest]
        return (d.rename(columns={"pos_rank": "pos_rank"})
                 [["player_name", "pos_rank"]]
                 .sort_values("pos_rank").reset_index(drop=True))

    # 2024- schema
    team_col = "club_code" if "club_code" in d.columns else "team"
    name_col = "full_name" if "full_name" in d.columns else "player_name"
    rank_col = "depth_team" if "depth_team" in d.columns else "pos_rank"
    pos_col = "position"
    d = d[(d[team_col] == team) & (d[pos_col] == position)]
    if d.empty:
        return pd.DataFrame(columns=["player_name", "pos_rank"])
    if as_of is not None and "week" in d.columns:
        try:
            d = d[d.week <= int(as_of)]
        except (ValueError, TypeError):
            pass
        if d.empty:
            return pd.DataFrame(columns=["player_name", "pos_rank"])
    if "week" in d.columns:
        latest = d.week.max()
        d = d[d.week == latest]
    return (d.rename(columns={name_col: "player_name", rank_col: "pos_rank"})
             [["player_name", "pos_rank"]]
             .sort_values("pos_rank").reset_index(drop=True))


def primary_at(season: int, through_week: int, team: str,
               position: str,
               exclude_out_week: int | None = None) -> dict | None:
    """Pick the WR1 / RB1 for `team` at `through_week`.

    If `exclude_out_week` is given, players whose `report_status == 'Out'`
    for that week are dropped from consideration — so a depth-1 WR who
    is inactive this week yields to the next-eligible player instead of
    being queued and then R-003-self-elevated.

    Preference order:
      1) depth-chart `pos_rank == 1` player, if they appear in trailing-3
         `usage()` with snap_pct >= 0.4.
      2) composite 0.6 * snap_pct + 0.4 * share (share is tgt_share for WR,
         car / team-recent-carries for RB), snap_pct tiebreak.

    Returns a dict shaped like the entries slate.py already consumes
    (`name, pos, tgt, share, rec, car, snap`) plus `depth_team` and `gms`,
    or None if no qualifying player exists.
    """
    u = usage(season, through_week, team)
    if u.empty:
        return None
    u = u[u.position == position]
    if u.empty:
        return None

    out_names: set[str] = set()
    if exclude_out_week is not None:
        inj = _injuries(season)
        out_rows = inj[(inj.week == exclude_out_week) & (inj.team == team)
                       & (inj.position == position)
                       & (inj.report_status == "Out")]
        out_names = set(out_rows.full_name.dropna().astype(str))

    u = u[~u.player_display_name.isin(out_names)]
    if u.empty:
        return None

    d = depth_order(season, team, position)
    d_eligible = d[~d.player_name.isin(out_names)] if not d.empty else d

    if not d_eligible.empty:
        top_name = d_eligible.sort_values("pos_rank").iloc[0]["player_name"]
        top_rank = int(d_eligible.sort_values("pos_rank").iloc[0]["pos_rank"])
        match = u[u.player_display_name == top_name]
        if not match.empty:
            r = match.iloc[0]
            if float(r.snap_pct or 0) >= 0.4:
                return _player_dict(r, position, depth_team=top_rank)

    u = u.copy()
    if position == "RB":
        total_car = max(float(u.car.sum()), 1e-6)
        share = u.car / total_car
    else:
        share = u.tgt_share.fillna(0)
    u = u.assign(_composite=0.6 * u.snap_pct.fillna(0) + 0.4 * share)
    u = u.sort_values(["_composite", "snap_pct"], ascending=[False, False])
    r = u.iloc[0]
    depth = None
    if not d.empty:
        hit = d[d.player_name == r.player_display_name]
        if not hit.empty:
            depth = int(hit.iloc[0]["pos_rank"])
    return _player_dict(r, position, depth_team=depth)


def _player_dict(r, position: str, depth_team: int | None) -> dict:
    return {
        "name": r.player_display_name,
        "pos": position,
        "tgt": round(float(r.tgt or 0), 1),
        "share": round(float(r.tgt_share or 0), 3),
        "rec": round(float(r.rec or 0), 1),
        "car": round(float(r.car or 0), 1),
        "snap": round(float(r.snap_pct or 0), 2),
        "depth_team": depth_team,
        "gms": int(r.gms),
    }


def elevation_r003(season: int, week: int, team: str, position: str,
                   base_rec: float, base_car: float,
                   exclude_player: str | None = None) -> dict:
    """R-003 base elevation. Returns the pre- or post-elevation base plus
    an audit note.

    Fires when a same-position teammate on `team` has `report_status == 'Out'`
    for `week` AND their trailing-3 snap share was ≥ 25% (so a scrub-RB
    going out does not elevate the starter artificially). `exclude_player`
    (the player we're predicting for) is removed from the out-list first,
    so a WR1 picked as the queue slot does not elevate herself when the
    picker already fell back past her.

    Inheritance rate is position-specific:
      WR : base_rec += 0.4 × sum(out_teammate.rec/g)
      RB : base_car += 0.6 × sum(out_teammate.car/g)

    Returns {"base_rec", "base_car", "fires", "notes"}.
    """
    inj = _injuries(season)
    out = inj[(inj.week == week) & (inj.team == team)
             & (inj.position == position)
             & (inj.report_status == "Out")]
    if exclude_player is not None:
        out = out[out.full_name != exclude_player]
    quiet = {"base_rec": round(float(base_rec or 0), 2),
             "base_car": round(float(base_car or 0), 2),
             "fires": False, "notes": ""}
    if out.empty:
        return quiet

    u = usage(season, week - 1, team)
    if u.empty:
        return quiet

    rec_add, car_add, notes = 0.0, 0.0, []
    for _, row in out.iterrows():
        hit = u[(u.player_display_name == row.full_name)
                & (u.position == position)]
        if hit.empty:
            continue
        r = hit.iloc[0]
        if float(r.snap_pct or 0) < 0.25:
            continue
        if position == "WR":
            rec_val = float(r.rec or 0)
            rec_add += 0.4 * rec_val
            notes.append(f"{row.full_name} OUT; +0.4 × {rec_val:.1f} rec/g")
        elif position == "RB":
            car_val = float(r.car or 0)
            car_add += 0.6 * car_val
            notes.append(f"{row.full_name} OUT; +0.6 × {car_val:.1f} car/g")

    if not notes:
        return quiet

    return {
        "base_rec": round(float(base_rec or 0) + rec_add, 2),
        "base_car": round(float(base_car or 0) + car_add, 2),
        "fires": True,
        "notes": "; ".join(notes),
    }


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
