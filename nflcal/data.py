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


@functools.lru_cache(maxsize=8)
def _rosters_weekly(season: int) -> pd.DataFrame:
    return nfl.load_rosters_weekly([season]).to_pandas()


def _effectively_out(season: int, week: int, team: str,
                     position: str) -> dict[str, str]:
    """Players on `team` at `position` who will not play `week`.

    Union of two signals, because the weekly injury report doesn't cover
    season-ending moves:
      - injury report `report_status == 'Out'` for `week` (week-to-week
        designations)
      - weekly roster `status != 'ACT'` for `week` (RES/IR, INA, PUP, CUT,
        TRD, etc. — anyone not on the active roster for this specific week)

    Returns {player_name: reason_tag}; reason_tag is `'Out'` or the roster
    status abbreviation. The caller uses the tag only for audit strings.
    """
    out: dict[str, str] = {}
    try:
        inj = _injuries(season)
        inj_hit = inj[(inj.week == week) & (inj.team == team)
                      & (inj.position == position)
                      & (inj.report_status == "Out")]
        for name in inj_hit.full_name.dropna().astype(str):
            out[name] = "Out"
    except Exception:
        pass
    try:
        rw = _rosters_weekly(season)
        rw_hit = rw[(rw.week == week) & (rw.team == team)
                    & (rw.position == position)
                    & (rw.status != "ACT")]
        for _, row in rw_hit.iterrows():
            name = str(row.full_name)
            if name not in out:
                out[name] = str(row.status)
    except Exception:
        pass
    return out


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


def _gained(season: int, through_week: int | None = None) -> pd.DataFrame:
    """Per-team offensive numbers: what each team GAINED, per game. Mirror
    of _allowed, but grouped on the offense column instead of defense."""
    ts = _team_stats(season)
    ts = ts[ts.season_type == "REG"] if "season_type" in ts else ts
    if through_week is not None:
        ts = ts[ts.week <= through_week]
    if ts.empty:
        return pd.DataFrame()
    g = ts.groupby("team").agg(
        games=("week", "nunique"),
        pass_yds_gained=("passing_yards", "mean"),
        rush_yds_gained=("rushing_yards", "mean"),
        pass_epa_gained=("passing_epa", "mean"),
        rush_epa_gained=("rushing_epa", "mean"),
    )
    return g


def offense_ratings(season: int, through_week: int) -> pd.DataFrame:
    """Offensive quality, shrunk toward the prior season using the same
    K_PRIOR_GAMES as defense_ratings. Rank 1 = best offense (most yards /
    highest EPA gained). The composite 'off_rank' blends the two EPA
    measures the same way def_rank does; 'pass_off_rank' / 'rush_off_rank'
    are the position-specific aliases for UI consumption."""
    cur = _gained(season, through_week)
    prior = _gained(season - 1)

    metrics = ["pass_yds_gained", "rush_yds_gained",
               "pass_epa_gained", "rush_epa_gained"]

    out = cur.copy()
    n = out["games"].clip(lower=0)
    k = K_PRIOR_GAMES

    for m in metrics:
        p = prior[m] if (not prior.empty and m in prior) else pd.Series(dtype=float)
        p = p.reindex(out.index)
        p = p.fillna(out[m].mean())
        out[m] = (n * out[m] + k * p) / (n + k)

    # Rank 1 = highest value (best offense), ascending=False.
    for m in metrics:
        out[m + "_rank"] = out[m].rank(method="min", ascending=False).astype(int)

    out["off_score"] = (out["pass_epa_gained_rank"] + out["rush_epa_gained_rank"]) / 2
    out["off_rank"] = out["off_score"].rank(method="min").astype(int)
    out["pass_off_rank"] = out["pass_epa_gained_rank"]
    out["rush_off_rank"] = out["rush_epa_gained_rank"]
    return out.sort_values("off_rank")


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
        out_names = set(_effectively_out(season, exclude_out_week,
                                         team, position).keys())

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

    Fires when a same-position teammate on `team` is **effectively out**
    for `week` AND their trailing-3 snap share was ≥ 25% (so a scrub-RB
    going out does not elevate the starter artificially). "Effectively
    out" is the union of two signals (see `_effectively_out`):
      - injury report `report_status == 'Out'` for `week` (short-term)
      - weekly roster `status != 'ACT'` for `week` (IR / Reserve / Inactive
        / Trade / Cut — captures season-ending moves that fall off the
        weekly injury report).
    `exclude_player` (the player we're predicting for) is removed from the
    out-list first, so a WR1 picked as the queue slot does not elevate
    herself when the picker already fell back past her.

    Inheritance rate is position-specific:
      WR : base_rec += 0.4 × sum(out_teammate.rec/g)
      RB : base_car += 0.6 × sum(out_teammate.car/g)

    Returns {"base_rec", "base_car", "fires", "notes"}.
    """
    out_map = _effectively_out(season, week, team, position)
    if exclude_player is not None:
        out_map.pop(exclude_player, None)
    quiet = {"base_rec": round(float(base_rec or 0), 2),
             "base_car": round(float(base_car or 0), 2),
             "fires": False, "notes": ""}
    if not out_map:
        return quiet

    u = usage(season, week - 1, team)
    if u.empty:
        return quiet

    rec_add, car_add, notes = 0.0, 0.0, []
    for name, reason in out_map.items():
        hit = u[(u.player_display_name == name)
                & (u.position == position)]
        if hit.empty:
            continue
        r = hit.iloc[0]
        if float(r.snap_pct or 0) < 0.25:
            continue
        tag = "OUT" if reason == "Out" else f"OUT ({reason})"
        if position == "WR":
            rec_val = float(r.rec or 0)
            rec_add += 0.4 * rec_val
            notes.append(f"{name} {tag}; +0.4 × {rec_val:.1f} rec/g")
        elif position == "RB":
            car_val = float(r.car or 0)
            car_add += 0.6 * car_val
            notes.append(f"{name} {tag}; +0.6 × {car_val:.1f} car/g")

    if not notes:
        return quiet

    return {
        "base_rec": round(float(base_rec or 0) + rec_add, 2),
        "base_car": round(float(base_car or 0) + car_add, 2),
        "fires": True,
        "notes": "; ".join(notes),
    }


def season_avg(season: int, player: str, through_week: int,
                market: str) -> float | None:
    """Season-to-date mean of a player's stat across all games they
    actually played through `through_week`. Used by R-010 as the
    current-season observation to shrink; previously used by the
    retired R-006.

    'market' is 'rushing' or 'receiving' (the two the formula consumes);
    yards markets are not modelled here. Returns None if no games on
    record this season."""
    ps = _player_stats(season)
    hit = ps[(ps.player_display_name == player) & (ps.week <= through_week)]
    if hit.empty:
        return None
    col = hit.carries if market == "rushing" else hit.receptions
    return float(col.mean())


def season_games(season: int, player: str, through_week: int) -> int:
    """How many games the player actually played this season through
    `through_week`. R-010 uses this as the current-season n."""
    ps = _player_stats(season)
    hit = ps[(ps.player_display_name == player) & (ps.week <= through_week)]
    return int(len(hit))


def blend_small_n(base: float, season_avg_: float | None, gms: int) -> float:
    """R-006 (retired W8-era) blend. Kept so legacy variants in
    validate.py still resolve the symbol. R-010 subsumes this on live
    predictions — the W9 slate pipeline uses `ebayes_base()` instead.

    When `gms < 3` (player missed games in the trailing-3 window),
    weight-blend the thin trailing base against the season average.
    Weight on trailing = gms/3. gms≥3 or missing season_avg_ is a
    no-op."""
    if gms >= 3 or season_avg_ is None:
        return float(base or 0)
    w = max(gms, 0) / 3.0
    return w * float(base or 0) + (1.0 - w) * float(season_avg_)


# ----- R-010 (empirical-Bayes base) ---------------------------------------
# Shipped W9 (2026-11-01) mid-week after live overconfidence evidence on
# Bucky Irving: current-season observation shrunk toward prior-season
# full-year average with pseudo-count = Efron-Morris regression halflife.
# Trailing-3 is then blended for recency weight.

R010_HALFLIFE_RB = 29.5    # Bleacher Report / Efron-Morris: RBs regress ~half after this many games
R010_HALFLIFE_WR = 13.5    # ditto for WRs
R010_POSITIONAL_PRIOR_RB = 14.0   # fallback for rookies / no-prior-year players (car/g)
R010_POSITIONAL_PRIOR_WR =  5.0   # ditto (rec/g)

# R-010cap (W10): magnitude cap on R-010's move from the raw trailing mean.
# W9 data showed R-010 could drag a trailing 15.7 car/g down to 9.0 on
# scheme-changed players (Swift to CHI) while leaving the fix intact for
# hot-stretch over-estimates (Irving). Clipping the ebayes output to
# trailing × [1 - R010_CAP_PCT, 1 + R010_CAP_PCT] preserves the regression
# intent and bounds the swing.
R010_CAP_PCT = 0.20


@functools.lru_cache(maxsize=4)
def _prior_season_player_stats(season: int) -> pd.DataFrame:
    """Previous-season player_stats cache; one load per prior season."""
    try:
        return nfl.load_player_stats([season]).to_pandas()
    except Exception:
        return pd.DataFrame()


def prior_full_season(season: int, player: str,
                       market: str) -> tuple[float | None, int]:
    """Prior-season (season-1) full-year mean rate and game count for a
    player. Returns (None, 0) when the player has no prior-year data
    (rookies, new-to-league). Chris chose full-season over last-5 for
    stability on 2026-10-29 while drafting the W9 slice."""
    ps = _prior_season_player_stats(season - 1)
    if ps.empty:
        return None, 0
    hit = ps[ps.player_display_name == player]
    if hit.empty:
        return None, 0
    col = hit.carries if market == "rushing" else hit.receptions
    return float(col.mean()), int(len(hit))


def ebayes_base(trailing_mean: float, trailing_n: int,
                 season_mean: float | None, season_n: int,
                 prior_mean: float | None, prior_n: int,
                 position: str,
                 apply_cap: bool = True,
                 cap_pct: float = R010_CAP_PCT) -> float:
    """R-010 empirical-Bayes base: shrink the current-season observation
    toward the prior-season full-year average (or the positional
    fallback for rookies), then blend trailing-3 for recency weight.

    Shrinkage weight: pseudo-count = halflife[position]. At season_n ==
    halflife the shrunk estimate is halfway between observation and
    prior — this is the Efron-Morris calibration (RB ~29.5 games, WR
    ~13.5 games).

    Recency blend: trailing_mean gets trailing_n games of weight,
    shrunk_season gets halflife/2 pseudo-games of weight. The shrunk
    estimate is already a composite so it's worth about half the raw
    prior's evidence weight.

    R-010cap (W10): the composed output is clipped to
    trailing × [1 - cap_pct, 1 + cap_pct]. Preserves the regression
    intent, bounds the swing on scheme-changed players.
    `apply_cap=False` reproduces W9 behaviour (uncapped) for backtest
    comparisons.

    Returns the composed base (same units as the input means)."""
    k = R010_HALFLIFE_RB if position == "RB" else R010_HALFLIFE_WR
    pos_prior = (R010_POSITIONAL_PRIOR_RB if position == "RB"
                 else R010_POSITIONAL_PRIOR_WR)

    # Prior: previous-season full-year when we have ≥5 games of it;
    # otherwise fall back to the positional mean for the depth-rank-1
    # tier (rookies and brand-new players).
    prior = prior_mean if (prior_mean is not None and prior_n >= 5) else pos_prior

    # Shrink current-season observation toward the prior.
    if season_n > 0 and season_mean is not None:
        shrunk_season = (season_n * float(season_mean) + k * prior) / (season_n + k)
    else:
        shrunk_season = prior

    # Blend with trailing-3 for recency weight.
    recency_pseudo = k / 2.0
    tr = float(trailing_mean or 0)
    if trailing_n > 0:
        out = (trailing_n * tr + recency_pseudo * shrunk_season) \
              / (trailing_n + recency_pseudo)
    else:
        out = shrunk_season

    if apply_cap and tr > 0:
        lo = tr * (1.0 - cap_pct)
        hi = tr * (1.0 + cap_pct)
        out = max(lo, min(hi, out))
    return out


# R-008 constants. Deflation cap matches the symmetric magnitude a would-be
# R-007 would have applied on R-003's elevation; capping at the raw trailing
# base keeps a single returning backup from zeroing the queued player's base.
R008_CAP_REC = 0.30   # WR: deflation capped at 30% of raw trailing-rec base
R008_CAP_CAR = 0.50   # RB: deflation capped at 50% of raw trailing-car base
R008_MIN_RATE_RB = 5.0  # ignore returning backups with <5 car/g (noise filter)
R008_MIN_RATE_WR = 2.0  # ignore returning backups with <2 rec/g


def _frac_window_out(season: int, team: str, position: str,
                     trailing_weeks: list[int], name: str) -> float:
    """Fraction of trailing_weeks in which `name` was effectively out for
    `team` at `position`. Uses R-003b's union-of-signals per week."""
    if not trailing_weeks:
        return 0.0
    out_count = 0
    for wk in trailing_weeks:
        try:
            out_map = _effectively_out(season, int(wk), team, position)
            if name in out_map:
                out_count += 1
        except Exception:
            pass
    return out_count / len(trailing_weeks)


def _backup_rate(season: int, name: str, through_week: int,
                 market: str) -> float | None:
    """Season-to-date rate for `name` across games they played through
    `through_week`. R-008 uses this as the deflation magnitude — the
    volume the queued player's trailing base was inflated by, now that
    this teammate is back on the field."""
    ps = _player_stats(season)
    hit = ps[(ps.player_display_name == name) & (ps.week <= through_week)]
    if hit.empty:
        return None
    col = hit.carries if market == "rushing" else hit.receptions
    return float(col.mean())


def deflation_r008(season: int, week: int, team: str, position: str,
                   queued_player: str,
                   base_rec_r006: float, base_car_r006: float,
                   base_rec_raw: float, base_car_raw: float) -> dict:
    """R-008 backup-return deflation. The mirror of R-003.

    Fires when a same-position teammate was **effectively out** (R-003b
    criteria: injury-report Out OR non-ACT weekly roster) in at least one
    of the trailing-3 weeks (`week-3`, `week-2`, `week-1`) AND is ACTIVE
    this week — meaning the queued player's trailing-3 base is inflated
    by a share of volume that will now be reclaimed by the returning
    teammate.

    Deflation magnitude mirrors R-003:
      WR : base_rec -= 0.4 × teammate.season_rate × fraction_window_out
      RB : base_car -= 0.6 × teammate.season_rate × fraction_window_out

    `teammate.season_rate` is the backup's mean across games they played
    through `week-1`. Backups with 0 games played get skipped (we have
    no rate to use). Rates below the min-rate floor (5 car/g for RB,
    2 rec/g for WR) are skipped to avoid noise from scrub-level
    teammates whose return doesn't really recapture volume.

    Deflation is capped at R-008's own symmetric caps (30% WR / 50% RB
    of the RAW trailing base) so a single returning backup can't zero
    the queued player's base.

    Applied in slate.py AFTER R-006 — the deflation is the final adjustment
    to the formula input, correcting whatever the trailing-base + R-006
    blend produced for the "backup absent" context into the "backup
    present" context the game will be played under.

    Returns {"base_rec", "base_car", "fires", "notes"}.
    """
    quiet = {"base_rec": round(float(base_rec_r006 or 0), 2),
             "base_car": round(float(base_car_r006 or 0), 2),
             "fires": False, "notes": ""}
    if week <= 3:
        return quiet
    trailing_weeks = [week - 3, week - 2, week - 1]

    try:
        rw = _rosters_weekly(season)
        hit = rw[(rw.week == week) & (rw.team == team)
                 & (rw.position == position) & (rw.status == "ACT")]
        candidates = set(hit.full_name.dropna().astype(str)) - {queued_player}
    except Exception:
        candidates = set()
    if not candidates:
        return quiet

    inherit = 0.6 if position == "RB" else 0.4
    market = "rushing" if position == "RB" else "receiving"
    min_rate = R008_MIN_RATE_RB if position == "RB" else R008_MIN_RATE_WR

    deflate_rec, deflate_car, notes = 0.0, 0.0, []
    for tm in sorted(candidates):
        frac = _frac_window_out(season, team, position, trailing_weeks, tm)
        if frac <= 0:
            continue
        rate = _backup_rate(season, tm, week - 1, market)
        if rate is None or rate < min_rate:
            continue
        amt = inherit * rate * frac
        if position == "RB":
            deflate_car += amt
            notes.append(f"{tm} returned (out {frac:.0%} of trailing); "
                         f"-{inherit} × {rate:.1f} car/g × {frac:.2f}")
        elif position == "WR":
            deflate_rec += amt
            notes.append(f"{tm} returned (out {frac:.0%} of trailing); "
                         f"-{inherit} × {rate:.1f} rec/g × {frac:.2f}")

    cap_rec = R008_CAP_REC * float(base_rec_raw or 0)
    cap_car = R008_CAP_CAR * float(base_car_raw or 0)
    if cap_rec > 0:
        deflate_rec = min(deflate_rec, cap_rec)
    if cap_car > 0:
        deflate_car = min(deflate_car, cap_car)

    if not notes:
        return quiet

    return {
        "base_rec": round(max(0.0, float(base_rec_r006 or 0) - deflate_rec), 2),
        "base_car": round(max(0.0, float(base_car_r006 or 0) - deflate_car), 2),
        "fires": True,
        "notes": "; ".join(notes),
    }


# ----- W10 share model ----------------------------------------------------
# Two-step projection: team_vol × shrunk_share × opp_mult.
#
# Shipped W10 (2026-11-12) as a rebuild of the stacked-rule base, after
# live W9 evidence that R-010 was dragging high-usage current RBs toward
# their lower prior-season averages on scheme changes (Swift featured in
# CHI, Allgeier lead-back without Robinson). Share is bounded [0, 1] and
# stable across team/scheme changes; raw rate is not.

K_SHARE = 10         # share-ebayes pseudo-count (games). Sweep range 5..20.
OPP_MULT_MAGNITUDE = 0.05  # top-vs-bottom defense = ±5% on volume
OPP_MULT_CLIP = (0.95, 1.05)

# Positional-tier median share for rookies / new-to-league fallback.
# Derived from nflverse 2023-2025 depth-rank-1 players, rounded to 2dp.
# (RB1 is the clear lead back; RB2 is the committee backup; WR1/WR2 similar.)
POS_TIER_SHARE_FALLBACK = {
    ("RB", 1): 0.55, ("RB", 2): 0.25, ("RB", 3): 0.10,
    ("WR", 1): 0.26, ("WR", 2): 0.18, ("WR", 3): 0.12,
}
POS_TIER_SHARE_DEFAULT = {"RB": 0.25, "WR": 0.15}

# Min teammate trailing snap_pct to count as an "OUT candidate" for share
# inheritance. Scrub-level backups don't carry transferable share.
SHARE_OUT_SNAP_FLOOR = 0.25


def _team_pos_weekly_vol(season: int, team: str, position: str,
                         through_week: int) -> pd.Series:
    """Per-week sum of the position group's volume on `team`, up to and
    including `through_week`. Index is week, values are the per-week team
    total (carries for RB, receptions for WR+TE)."""
    ps = _player_stats(season)
    if ps.empty:
        return pd.Series(dtype=float)
    hit = ps[(ps.team == team) & (ps.week <= through_week)]
    if position == "RB":
        hit = hit[hit.position == "RB"]
        col = "carries"
    else:  # WR volume = WR + TE receptions (Step 1 of the plan)
        hit = hit[hit.position.isin(["WR", "TE"])]
        col = "receptions"
    if hit.empty:
        return pd.Series(dtype=float)
    return hit.groupby("week")[col].sum()


def _team_pos_window_total(season: int, team: str, position: str,
                            week_start_exclusive: int,
                            week_end_inclusive: int) -> tuple[float, int]:
    """(total team volume, weeks_with_games) in the half-open interval
    (week_start_exclusive, week_end_inclusive]."""
    s = _team_pos_weekly_vol(season, team, position, week_end_inclusive)
    if s.empty:
        return 0.0, 0
    s = s[s.index > week_start_exclusive]
    if s.empty:
        return 0.0, 0
    return float(s.sum()), int(len(s))


def _player_window_total(season: int, team: str, player: str,
                         position: str,
                         week_start_exclusive: int,
                         week_end_inclusive: int) -> tuple[float, int]:
    """(player's total volume, player_games_played) in the same half-open
    interval (week_start_exclusive, week_end_inclusive]."""
    ps = _player_stats(season)
    if ps.empty:
        return 0.0, 0
    hit = ps[(ps.team == team) & (ps.player_display_name == player)
             & (ps.week <= week_end_inclusive)
             & (ps.week > week_start_exclusive)]
    if hit.empty:
        return 0.0, 0
    col = hit.carries if position == "RB" else hit.receptions
    return float(col.sum()), int(len(hit))


def team_volume(season: int, through_week: int, team: str, position: str,
                last_n: int = 3) -> float | None:
    """Trailing-n mean of the team's position-group weekly volume.

    team_vol[RB] = mean per-game of (sum of RB carries on the team)
    team_vol[WR] = mean per-game of (sum of WR+TE receptions on the team)

    Returns None if the team has no games on record in the trailing window.
    """
    total, n_weeks = _team_pos_window_total(
        season, team, position,
        week_start_exclusive=max(0, through_week - last_n),
        week_end_inclusive=through_week)
    if n_weeks == 0:
        return None
    return total / n_weeks


def _player_trailing_rate(season: int, through_week: int, team: str,
                          player: str, position: str,
                          last_n: int = 3) -> tuple[float, int]:
    """(trailing_mean, trailing_n_games_played) for one player on `team`.

    Counts only games the player actually played in the trailing-n window.
    `position` is WR or RB — same market convention as usage()."""
    ps = _player_stats(season)
    if ps.empty:
        return 0.0, 0
    hit = ps[(ps.team == team) & (ps.player_display_name == player)
             & (ps.week <= through_week)
             & (ps.week > max(0, through_week - last_n))]
    if hit.empty:
        return 0.0, 0
    col = hit.carries if position == "RB" else hit.receptions
    return float(col.mean()), int(len(hit))


def _player_trailing_total(season: int, through_week: int, team: str,
                           player: str, position: str,
                           last_n: int = 3) -> float:
    """Sum of player's volume over the trailing-n window. Used to compute
    share of team's trailing-window total (so a part-time contributor
    doesn't get credited with their per-game rate as share)."""
    ps = _player_stats(season)
    if ps.empty:
        return 0.0
    hit = ps[(ps.team == team) & (ps.player_display_name == player)
             & (ps.week <= through_week)
             & (ps.week > max(0, through_week - last_n))]
    if hit.empty:
        return 0.0
    col = hit.carries if position == "RB" else hit.receptions
    return float(col.sum())


def player_share(season: int, through_week: int, team: str, player: str,
                 position: str, last_n: int = 3) -> tuple[float | None, int]:
    """Trailing-n share: player's total / team's total over the same
    window (so a player who sat half the window doesn't carry the per-
    game rate of a full-time contributor).

    Returns (share, player_games_played). share is None if the team has
    no games in the window."""
    ws = max(0, through_week - last_n)
    team_total, team_weeks = _team_pos_window_total(
        season, team, position, ws, through_week)
    pl_total, pl_games = _player_window_total(
        season, team, player, position, ws, through_week)
    if team_total <= 0 or team_weeks == 0:
        return None, pl_games
    return pl_total / team_total, pl_games


def _prior_season_team(season: int, player: str) -> str | None:
    """Which team the player was on in the prior season. Uses their
    most-played team that year if they switched mid-season. None if the
    player has no prior-season data."""
    ps = _prior_season_player_stats(season - 1)
    if ps.empty:
        return None
    hit = ps[ps.player_display_name == player]
    if hit.empty:
        return None
    team_counts = hit.groupby("team").size()
    if team_counts.empty:
        return None
    return str(team_counts.idxmax())


def prior_season_share(season: int, player: str,
                       position: str) -> tuple[float | None, int]:
    """Prior-season share: player's total prior-year volume / their
    prior team's total position-group volume over the SAME games the
    player played (so a half-season injury doesn't sandbag the share).

    Returns (share, n_games_prior). (None, 0) when the player has no
    prior-season data."""
    ps = _prior_season_player_stats(season - 1)
    if ps.empty:
        return None, 0
    hit = ps[ps.player_display_name == player]
    if hit.empty:
        return None, 0
    prior_team = _prior_season_team(season, player)
    if prior_team is None:
        return None, int(len(hit))
    # Only count games the player and team BOTH played. In practice
    # that's every week the player appears on the team; pool those weeks.
    hit_team = hit[hit.team == prior_team]
    if hit_team.empty:
        return None, int(len(hit))
    weeks_played = sorted(set(int(w) for w in hit_team.week.unique()))
    if not weeks_played:
        return None, int(len(hit))
    col = hit_team.carries if position == "RB" else hit_team.receptions
    player_total = float(col.sum())
    player_n = int(len(weeks_played))

    weekly_team = _team_pos_weekly_vol(season - 1, prior_team, position,
                                        through_week=99)
    if weekly_team.empty:
        return None, player_n
    team_sum = float(weekly_team.loc[weekly_team.index.isin(weeks_played)].sum())
    if team_sum <= 0:
        return None, player_n
    return player_total / team_sum, player_n


def _trailing_snap_pct(season: int, through_week: int, team: str,
                       player: str, last_n: int = 3) -> float:
    """Trailing-n mean offensive snap pct for `player` on `team`. 0 when
    no snap rows exist in the window."""
    sn = _snaps(season)
    if sn.empty:
        return 0.0
    hit = sn[(sn.team == team) & (sn.player == player)
             & (sn.week <= through_week)
             & (sn.week > max(0, through_week - last_n))]
    if hit.empty:
        return 0.0
    return float(hit.offense_pct.mean() or 0.0)


def _active_same_pos(season: int, week: int, team: str, position: str,
                     exclude_player: str) -> list[str]:
    """Active (status=ACT) same-position teammates for `week` on `team`,
    excluding the queued player."""
    try:
        rw = _rosters_weekly(season)
        hit = rw[(rw.week == week) & (rw.team == team)
                 & (rw.position == position) & (rw.status == "ACT")]
        return sorted(set(hit.full_name.dropna().astype(str))
                      - {exclude_player})
    except Exception:
        return []


def _share_fallback(position: str, depth_team: int | None) -> float:
    """Positional-tier share when prior-season data is missing (rookie
    / new-to-league). Falls back to the position default when depth rank
    is unknown or outside the tiered range."""
    if depth_team is None:
        return POS_TIER_SHARE_DEFAULT.get(position, 0.20)
    key = (position, int(depth_team))
    if key in POS_TIER_SHARE_FALLBACK:
        return POS_TIER_SHARE_FALLBACK[key]
    return POS_TIER_SHARE_DEFAULT.get(position, 0.20)


def opp_mult(opp_rank: int | None,
             magnitude: float = OPP_MULT_MAGNITUDE) -> float:
    """Multiplicative opponent adjustment on volume.

    opp_mult = 1 + magnitude × (opp_pos_def_rank − 16.5) / 16.5, clipped
    to OPP_MULT_CLIP. Rank 1 (best defense) → 1 − magnitude (depress);
    rank 32 (worst) → 1 + magnitude (inflate). 16.5 is the midpoint of
    a 32-team rank.
    """
    if opp_rank is None:
        return 1.0
    raw = 1.0 + magnitude * (float(opp_rank) - 16.5) / 16.5
    lo, hi = OPP_MULT_CLIP
    return max(lo, min(hi, raw))


def share_projection(season: int, week: int, team: str, player: str,
                     position: str, opp_rank: int | None,
                     depth_team: int | None = None,
                     k_share: float = K_SHARE,
                     last_n: int = 3,
                     opp_mult_magnitude: float = OPP_MULT_MAGNITUDE,
                     apply_ebayes: bool = True,
                     apply_roster: bool = True) -> dict:
    """Full W10 share-model projection for one player.

    Steps:
      1. team_vol  = trailing-n mean of team's position-group weekly volume
      2. trail_sh  = player.trailing_rate / team_vol
      3. shrunk_sh = empirical-Bayes blend of trail_sh toward prior-season
                     share (or positional-tier fallback for rookies),
                     pseudo-count k_share
      4. final_sh  = shrunk_sh + Σ(OUT_teammate_share × inherit_frac)
                                − Σ(RETURNING_teammate_share × inherit_frac × frac_out)
                     clipped to [0, 1]
      5. projection = team_vol × final_sh × opp_mult

    Returns a dict with every intermediate field plus audit notes.
    """
    through = week - 1
    ws = max(0, through - last_n)  # trailing window exclusive start

    # Team volume per game (used only to scale the final projection).
    team_total_tr, team_weeks_tr = _team_pos_window_total(
        season, team, position, ws, through)
    tv = (team_total_tr / team_weeks_tr) if team_weeks_tr > 0 else None

    # Trailing-window share: player's total / team's total over the SAME
    # window. Part-time contributors stay correctly deflated.
    pl_total_tr, pl_games_tr = _player_window_total(
        season, team, player, position, ws, through)
    n_season_in_window = pl_games_tr
    trailing_share = (pl_total_tr / team_total_tr) if team_total_tr > 0 else None

    # Season-to-date share (full ebayes evidence: not just the trailing
    # window, and over weeks the player actually played).
    season_total_pl, season_n = _player_window_total(
        season, team, player, position, 0, through)
    # Team denominator = sum across the SAME weeks (so a bye for the
    # player doesn't deflate their share).
    ps = _player_stats(season)
    pl_season = ps[(ps.team == team) & (ps.player_display_name == player)
                   & (ps.week <= through)]
    weeks_pl = sorted(set(int(w) for w in pl_season.week.unique())) \
        if not pl_season.empty else []
    season_share = None
    if weeks_pl:
        weekly_team = _team_pos_weekly_vol(season, team, position, through)
        tsum = float(weekly_team.loc[weekly_team.index.isin(weeks_pl)].sum())
        if tsum > 0:
            season_share = season_total_pl / tsum

    # Prior-season share for the ebayes prior.
    prior_share_val, prior_n = prior_season_share(season, player, position)
    use_prior_data = prior_share_val is not None and prior_n >= 5
    if use_prior_data:
        prior_share_used = prior_share_val
        prior_tag = "prior"
    else:
        prior_share_used = _share_fallback(position, depth_team)
        prior_tag = "pos-prior"

    # Shrunk share via empirical Bayes on share. Evidence = season-to-date
    # share (more stable than the trailing-3 share for the ebayes step).
    if apply_ebayes:
        if season_share is not None and season_n > 0:
            shrunk_share = (season_n * season_share
                            + k_share * prior_share_used) / (season_n + k_share)
        else:
            shrunk_share = prior_share_used
        # The final share blends in trailing-3 for recency, same shape as R-010.
        recency_pseudo = k_share / 2.0
        if trailing_share is not None and n_season_in_window > 0:
            blended_share = (n_season_in_window * trailing_share
                             + recency_pseudo * shrunk_share) \
                            / (n_season_in_window + recency_pseudo)
        else:
            blended_share = shrunk_share
    else:
        # share_model baseline: pure trailing_share, no shrinkage. Fall
        # back to prior/positional when the player didn't play in window.
        shrunk_share = prior_share_used
        blended_share = (trailing_share if trailing_share is not None
                         else prior_share_used)

    # Step 4: roster adjustments.
    final_share = blended_share
    out_notes: list[str] = []
    returning_notes: list[str] = []

    # OUT branch: same-position teammates not playing this week with
    # trailing snap_pct ≥ floor.
    out_map = _effectively_out(season, week, team, position)
    out_map.pop(player, None)
    actives = _active_same_pos(season, week, team, position, player)

    # Build each active same-pos teammate's trailing snap_pct for the
    # inheritance denominator.
    active_snaps = {nm: _trailing_snap_pct(season, through, team, nm,
                                           last_n=last_n)
                    for nm in actives}
    total_active_snap = sum(active_snaps.values())

    if apply_roster and out_map and team_total_tr > 0:
        own_snap = _trailing_snap_pct(season, through, team, player,
                                      last_n=last_n)
        own_cred = own_snap if own_snap > 0 else (trailing_share or 0.0)
        min_tm_rate = (R008_MIN_RATE_RB if position == "RB"
                       else R008_MIN_RATE_WR)
        for nm, reason in out_map.items():
            tm_snap = _trailing_snap_pct(season, through, team, nm,
                                         last_n=last_n)
            tm_total, tm_games = _player_window_total(
                season, team, nm, position, ws, through)
            if tm_games == 0:
                continue
            tm_rate_g = tm_total / tm_games
            # Scrub filter: either a visible snap share or a volume rate
            # above the noise floor. Missing snap rows don't disqualify a
            # credible committee contributor.
            if tm_snap < SHARE_OUT_SNAP_FLOOR and tm_rate_g < min_tm_rate:
                continue
            tm_share = tm_total / team_total_tr
            # Credibility of each ACTIVE same-pos teammate, same rule as
            # the queued player, for the denominator sum.
            denom = own_cred
            for other in actives:
                os_snap = active_snaps.get(other, 0.0)
                if os_snap > 0:
                    denom += os_snap
                else:
                    o_tot, _ = _player_window_total(
                        season, team, other, position, ws, through)
                    denom += (o_tot / team_total_tr) if team_total_tr > 0 else 0.0
            inherit_frac = own_cred / denom if denom > 0 else 1.0
            delta = tm_share * inherit_frac
            final_share += delta
            tag = "OUT" if reason == "Out" else f"OUT ({reason})"
            out_notes.append(
                f"{nm} {tag}; +{tm_share:.3f} × {inherit_frac:.2f} share")

    # RETURNING branch: same-position teammate active this week, out of at
    # least one of the trailing-n weeks, with a non-trivial rate.
    if apply_roster and week > last_n and team_total_tr > 0:
        trailing_weeks = [week - last_n + i for i in range(last_n)]
        min_rate = R008_MIN_RATE_RB if position == "RB" else R008_MIN_RATE_WR
        own_snap_r = _trailing_snap_pct(season, through, team, player,
                                         last_n=last_n)
        own_cred_r = own_snap_r if own_snap_r > 0 else (trailing_share or 0.0)
        for nm in actives:
            frac_out = _frac_window_out(season, team, position,
                                         trailing_weeks, nm)
            if frac_out <= 0:
                continue
            # Teammate's share in games THEY played this season (sum over
            # their played weeks, divided by team's sum over the same
            # weeks).
            ps_tm = ps[(ps.player_display_name == nm)
                       & (ps.team == team) & (ps.week <= through)]
            if ps_tm.empty:
                continue
            tm_weeks = sorted(set(int(w) for w in ps_tm.week.unique()))
            if not tm_weeks:
                continue
            col = ps_tm.carries if position == "RB" else ps_tm.receptions
            tm_total_season = float(col.sum())
            if (tm_total_season / len(tm_weeks)) < min_rate:
                continue
            weekly_team_full = _team_pos_weekly_vol(season, team, position,
                                                    through)
            team_in_tm_weeks = float(weekly_team_full.loc[
                weekly_team_full.index.isin(tm_weeks)].sum())
            if team_in_tm_weeks <= 0:
                continue
            tm_share_in_games = tm_total_season / team_in_tm_weeks
            denom = own_cred_r
            for other in actives:
                os_snap = active_snaps.get(other, 0.0)
                if os_snap > 0:
                    denom += os_snap
                else:
                    o_tot, _ = _player_window_total(
                        season, team, other, position, ws, through)
                    denom += (o_tot / team_total_tr) if team_total_tr > 0 else 0.0
            inherit_frac = own_cred_r / denom if denom > 0 else 1.0
            delta = tm_share_in_games * inherit_frac * frac_out
            final_share -= delta
            returning_notes.append(
                f"{nm} returned (out {frac_out:.0%} of trailing); "
                f"-{tm_share_in_games:.3f} × {inherit_frac:.2f} × {frac_out:.2f}")

    final_share = max(0.0, min(1.0, final_share))

    om = opp_mult(opp_rank, magnitude=opp_mult_magnitude)
    projection = (tv * final_share * om) if tv is not None else 0.0

    return {
        "projection": round(float(projection), 2),
        "team_vol": None if tv is None else round(float(tv), 2),
        "trailing_share": (None if trailing_share is None
                           else round(float(trailing_share), 4)),
        "season_share": (None if season_share is None
                          else round(float(season_share), 4)),
        "prior_share": round(float(prior_share_used), 4),
        "prior_tag": prior_tag,
        "prior_n": int(prior_n),
        "season_n": season_n,
        "trailing_n": int(n_season_in_window),
        "shrunk_share": round(float(shrunk_share), 4),
        "final_share": round(float(final_share), 4),
        "opp_mult": round(float(om), 3),
        "out_notes": "; ".join(out_notes),
        "returning_notes": "; ".join(returning_notes),
    }


def upcoming(season: int, week: int) -> pd.DataFrame:
    s = _schedules(season)
    return s[(s.week == week)].copy()


def last_completed_week(season: int) -> int:
    s = _schedules(season)
    done = s[s.result.notna()]
    return int(done.week.max()) if len(done) else 0


def next_open_week(season: int) -> int:
    """The week still being played or next to be played: the earliest week
    with an unplayed game dated today or later (US Eastern). Not
    last_completed_week + 1 — one finished Thursday game would make that
    skip the rest of its own week. Past-dated unplayed games (postponed or
    cancelled) are ignored so they can't pin the default to an old week."""
    import datetime as dt, zoneinfo
    s = _schedules(season)
    today = dt.datetime.now(zoneinfo.ZoneInfo("America/New_York")).date().isoformat()
    open_ = s[s.result.isna() & (s.gameday.astype(str) >= today)]
    return int(open_.week.min()) if len(open_) else last_completed_week(season) + 1


def pace(season: int, through_week: int) -> pd.Series:
    """Offensive plays per game. Volume props scale with it, so it is a
    control, not part of the hypothesis."""
    ts = _team_stats(season)
    ts = ts[ts.week <= through_week]
    if ts.empty:
        return pd.Series(dtype=float)
    ts = ts.assign(plays=ts.attempts + ts.carries)
    return ts.groupby("team")["plays"].mean()
