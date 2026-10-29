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
    """R-006 helper. Season-to-date mean of a player's stat across all
    games they actually played through `through_week`. Returns None if
    no games on record. 'market' is 'rushing' or 'receiving' (the two
    the formula consumes); yards markets are not modelled here.
    This is the stable prior that R-006 blends the thin trailing-3
    base toward when the player missed games in the trailing window."""
    ps = _player_stats(season)
    hit = ps[(ps.player_display_name == player) & (ps.week <= through_week)]
    if hit.empty:
        return None
    col = hit.carries if market == "rushing" else hit.receptions
    return float(col.mean())


def blend_small_n(base: float, season_avg_: float | None, gms: int) -> float:
    """R-006 blend. When `gms < 3` (player missed games in the trailing-3
    window), weight-blend the thin trailing base against the season
    average. Weight on trailing = gms/3, so gms=2 → 2/3 trailing + 1/3
    season, gms=1 → 1/3 trailing + 2/3 season. gms≥3 is a no-op; a
    missing season_avg_ (first-game players, rookies) also no-ops."""
    if gms >= 3 or season_avg_ is None:
        return float(base or 0)
    w = max(gms, 0) / 3.0
    return w * float(base or 0) + (1.0 - w) * float(season_avg_)


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
