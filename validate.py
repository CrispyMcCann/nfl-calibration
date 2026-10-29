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
import functools
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


def _season_avg(ps: pd.DataFrame, player: str, through_week: int, market: str) -> float | None:
    """R-006 helper: player's season-to-date mean of the stat across all
    games they actually played through_week. Returns None if no games.
    This is the stable prior that R-006 blends the thin trailing-3 base
    toward when the player missed games."""
    hit = ps[(ps.player_display_name == player) & (ps.week <= through_week)]
    if hit.empty:
        return None
    col = hit.carries if market == "rushing" else hit.receptions
    return float(col.mean())


def _blend_small_n(base: float, season_avg: float | None, gms: int) -> float:
    """R-006 blend. When the player's trailing window saw <3 games played,
    weight the thin trailing base against the season-to-date mean.
    Weight on trailing = gms/3, so gms=2 → 2/3 trailing + 1/3 season,
    gms=1 → 1/3 trailing + 2/3 season. gms=0 should not reach this (the
    player wouldn't be queued), but if it does we fall back to season_avg.
    gms>=3 is a no-op."""
    if gms >= 3 or season_avg is None:
        return base
    w = max(gms, 0) / 3.0
    return w * base + (1.0 - w) * season_avg


# ----- R-007 / R-008 candidate helpers (backtest-only, not yet in prod) -----

R007_CAP_REC = 0.30  # WR: R-003 bump capped at 30% of player's own trailing base
R007_CAP_CAR = 0.50  # RB: R-003 bump capped at 50% of player's own trailing base
R008_MIN_RATE_RB = 5.0   # don't deflate for returning backups with <5 car/g
R008_MIN_RATE_WR = 2.0   # don't deflate for returning backups with <2 rec/g

# ----- W9 draft candidates (backtest-only, not yet in prod) -----
# Chosen 2026-10-29 after sports-stats research (PlayerProfiler Variance
# Manifesto, Efron-Morris regression halflife from Bleacher Report).

# R-005' upgraded SIGMA_SCALE (field median; was 0.30 under W8).
R005B_SIGMA_SCALE_RB = 0.60   # ~RB CV median per PlayerProfiler
R005B_SIGMA_SCALE_WR = 0.65   # ~WR CV median per PlayerProfiler

# R-010 empirical-Bayes halflife (games at which shrinkage is 50/50).
R010_HALFLIFE_RB = 29.5   # Bleacher Report / Efron-Morris
R010_HALFLIFE_WR = 13.5   # ditto

# R-010 positional priors — league-mean rate for a depth-rank-1 player
# at the given position. Used as fallback when prior-season data missing
# (rookies, new-to-league players). Rough calibration from historical
# nflverse; refinable per-season if needed.
R010_POSITIONAL_PRIOR_RB = 14.0   # car/g for a typical RB1
R010_POSITIONAL_PRIOR_WR =  5.0   # rec/g for a typical WR1


@functools.lru_cache(maxsize=8)
def _prior_season_stats(season: int) -> pd.DataFrame:
    """Prior-season player_stats cache. One file per prior season; cheap
    to repeat across many rows via lru_cache. Returns an empty frame if
    the prior season has no data (first-year league / 2022 for the 2023
    backtest start)."""
    try:
        return nfl.load_player_stats([season]).to_pandas()
    except Exception:
        return pd.DataFrame()


def _prior_full_season(season: int, player: str, market: str) -> tuple[float | None, int]:
    """Full prior-season (season-1) mean rate across all games the player
    played. Chris chose full-season over last-5-of-prior for stability.
    Returns (mean, n_games) or (None, 0) if unavailable."""
    ps = _prior_season_stats(season - 1)
    if ps.empty:
        return None, 0
    hit = ps[ps.player_display_name == player]
    if hit.empty:
        return None, 0
    col = hit.carries if market == "rushing" else hit.receptions
    return float(col.mean()), int(len(hit))


def _r010_ebayes_base(trailing_mean: float, trailing_n: int,
                     season_mean: float | None, season_n: int,
                     prior_mean: float | None, prior_n: int,
                     positional_mean: float, position: str) -> float:
    """R-010 empirical-Bayes base. See METHODOLOGY.md deferred-candidates
    (via memory). The idea: shrink the player's rate toward prior-season
    (or positional fallback) with pseudo-count = Efron-Morris halflife;
    then blend with trailing-3 for recency weight.
    """
    k = R010_HALFLIFE_RB if position == "RB" else R010_HALFLIFE_WR

    # Pick the prior: previous-season average when we have ≥5 games on
    # record; otherwise fall back to the positional mean (rookies, new).
    prior = prior_mean if (prior_mean is not None and prior_n >= 5) else positional_mean

    # Shrink current-season observation toward the prior.
    if season_n > 0 and prior is not None:
        shrunk_season = (season_n * season_mean + k * prior) / (season_n + k)
    elif season_n > 0:
        shrunk_season = season_mean
    elif prior is not None:
        shrunk_season = prior
    else:
        # No data of any kind — fall back to trailing (or zero).
        return float(trailing_mean or 0)

    # Blend trailing-window with the shrunk season estimate. Pseudo-count
    # on the shrunk season = k/2 — the shrunk estimate itself is already
    # a composite, worth about half as much "evidence weight" as the raw
    # prior would be.
    recency_pseudo = k / 2.0
    if trailing_n > 0:
        return (trailing_n * trailing_mean + recency_pseudo * shrunk_season) \
             / (trailing_n + recency_pseudo)
    return shrunk_season


def _cap_r007(base_raw: float, base_r003: float, position: str) -> float:
    """R-007: cap the R-003 bump at a fraction of the player's own trailing
    base. Only applies when R-003 added volume (bump > 0). Prevents one
    out-teammate from doubling a player's base when the inheritance would
    disperse in reality (e.g. a WR3 vacancy routing to multiple pass
    catchers, or a committee backfield losing one of several backs)."""
    base_raw = float(base_raw or 0)
    bump = float(base_r003 or 0) - base_raw
    if bump <= 0:
        return float(base_r003 or 0)
    cap_frac = R007_CAP_CAR if position == "RB" else R007_CAP_REC
    cap = cap_frac * base_raw
    return base_raw + min(bump, cap)


def _frac_window_out(season: int, team: str, position: str,
                     trailing_weeks: list[int], name: str) -> float:
    """Fraction of trailing_weeks in which `name` was effectively out for
    `team` at `position` (uses R-003b's union of injury-report Out and
    non-ACT roster signals, applied per week)."""
    if not trailing_weeks:
        return 0.0
    out_count = 0
    for wk in trailing_weeks:
        try:
            out_map = D._effectively_out(season, int(wk), team, position)
            if name in out_map:
                out_count += 1
        except Exception:
            pass
    return out_count / len(trailing_weeks)


def _backup_rate(ps: pd.DataFrame, name: str, through_week: int,
                 market: str) -> float | None:
    """Season-to-date rate for `name` across games they played through
    `through_week`. Used as the deflation magnitude for R-008 — mirrors
    R-003's use of a trailing-ish rate for the OUT teammate."""
    hit = ps[(ps.player_display_name == name) & (ps.week <= through_week)]
    if hit.empty:
        return None
    col = hit.carries if market == "rushing" else hit.receptions
    return float(col.mean())


def _r008_deflate(season: int, week: int, team: str, position: str,
                  queued_name: str, base_rec: float, base_car: float,
                  base_rec_raw: float, base_car_raw: float,
                  ps: pd.DataFrame) -> tuple[float, float, bool, str]:
    """R-008: when a same-position teammate was effectively out in some of
    the trailing-3 weeks AND is active this week, deflate the queued
    player's base by inherit_rate × teammate.season_rate × fraction_out.

    Mirrors R-003 but in reverse. Deflation is capped at the same levels
    as R-007 (50% RB / 30% WR of the player's RAW trailing base) so a
    single returning backup can't zero the base.

    Returns (base_rec_adj, base_car_adj, fires, notes).
    """
    if week <= 3:
        return float(base_rec or 0), float(base_car or 0), False, ""
    trailing_weeks = [week - 3, week - 2, week - 1]

    # Candidate teammates: on the active roster for THIS week at the same
    # position, excluding the queued player.
    try:
        rw = D._rosters_weekly(season)
        hit = rw[(rw.week == week) & (rw.team == team)
                 & (rw.position == position) & (rw.status == "ACT")]
        candidates = set(hit.full_name.dropna().astype(str)) - {queued_name}
    except Exception:
        candidates = set()
    if not candidates:
        return float(base_rec or 0), float(base_car or 0), False, ""

    inherit = 0.6 if position == "RB" else 0.4
    market = "rushing" if position == "RB" else "receiving"
    min_rate = R008_MIN_RATE_RB if position == "RB" else R008_MIN_RATE_WR

    deflate_rec, deflate_car, notes = 0.0, 0.0, []
    for tm in sorted(candidates):
        frac = _frac_window_out(season, team, position, trailing_weeks, tm)
        if frac <= 0:
            continue
        rate = _backup_rate(ps, tm, week - 1, market)
        if rate is None or rate < min_rate:
            continue
        amt = inherit * rate * frac
        if position == "RB":
            deflate_car += amt
            notes.append(
                f"{tm} returned (out {frac:.0%} of trailing); "
                f"-{inherit} × {rate:.1f} car/g × {frac:.2f}")
        else:
            deflate_rec += amt
            notes.append(
                f"{tm} returned (out {frac:.0%} of trailing); "
                f"-{inherit} × {rate:.1f} rec/g × {frac:.2f}")

    # Cap: same symmetric policy as R-007 on the RAW trailing base.
    cap_rec = R007_CAP_REC * float(base_rec_raw or 0)
    cap_car = R007_CAP_CAR * float(base_car_raw or 0)
    if cap_rec > 0:
        deflate_rec = min(deflate_rec, cap_rec)
    if cap_car > 0:
        deflate_car = min(deflate_car, cap_car)

    base_rec_adj = max(0.0, float(base_rec or 0) - deflate_rec)
    base_car_adj = max(0.0, float(base_car or 0) - deflate_car)
    return base_rec_adj, base_car_adj, bool(notes), "; ".join(notes)


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
                    base_rec_raw = float(pl.get("rec", 0.0) or 0)
                    base_car_raw = float(pl.get("car", 0.0) or 0)
                    base_raw   = base_car_raw if market == "rushing" else base_rec_raw
                    base_r003  = elev["base_car"] if market == "rushing" else elev["base_rec"]
                    # R-007 cap on top of R-003 (per-market).
                    base_r007_car = _cap_r007(base_car_raw, elev["base_car"], pos)
                    base_r007_rec = _cap_r007(base_rec_raw, elev["base_rec"], pos)
                    base_r007 = base_r007_car if market == "rushing" else base_r007_rec
                    # R-006 inputs: games played in trailing-3 window + season avg.
                    pl_gms   = int(pl.get("gms", 3))
                    sa       = _season_avg(ps, pl["name"], wk - 1, market)
                    base_r006_from_r003 = _blend_small_n(base_r003, sa, pl_gms)
                    base_r006_from_r007 = _blend_small_n(base_r007, sa, pl_gms)
                    # R-008 deflation (applied AFTER R-006). Compute once with
                    # the R-003-then-R-006 base, and once with the R-007-then-R-006 base.
                    base_rec_r006_r003 = _blend_small_n(elev["base_rec"], sa, pl_gms) \
                        if market == "receiving" else _blend_small_n(base_rec_raw, sa, pl_gms)
                    base_car_r006_r003 = _blend_small_n(elev["base_car"], sa, pl_gms) \
                        if market == "rushing" else _blend_small_n(base_car_raw, sa, pl_gms)
                    base_rec_r008, base_car_r008, r008_fires, _ = _r008_deflate(
                        season, wk, team, pos, pl["name"],
                        base_rec_r006_r003, base_car_r006_r003,
                        base_rec_raw, base_car_raw, ps)
                    base_r008 = base_car_r008 if market == "rushing" else base_rec_r008
                    # R-007 + R-008 variant: cap first, then blend, then deflate.
                    base_rec_r006_r007 = _blend_small_n(base_r007_rec, sa, pl_gms)
                    base_car_r006_r007 = _blend_small_n(base_r007_car, sa, pl_gms)
                    base_rec_r007_r008, base_car_r007_r008, _, _ = _r008_deflate(
                        season, wk, team, pos, pl["name"],
                        base_rec_r006_r007, base_car_r006_r007,
                        base_rec_raw, base_car_raw, ps)
                    base_r007_r008 = (base_car_r007_r008 if market == "rushing"
                                      else base_rec_r007_r008)

                    # Outcome: 1 if actual > line (over hit), 0 if under hit.
                    # Push impossible on half-point line.
                    over_hit = 1 if actual > line else 0
                    y = over_hit if side == "over" else (1 - over_hit)

                    # Each variant's my_p. First 4 keep the original ladder
                    # (no rules → current W6-R001-R002-R003b = "all") so the
                    # existing table is unchanged. The last 3 stack R-005 and
                    # R-006 on top of "all" to answer the Week 7 question.
                    sc = a.sigma_scale  # R-005 constant; default 0.30
                    # W9 draft candidates (backtest-only). R-010 empirical-Bayes
                    # base replaces R-003+R-006 composition for the receiving-
                    # or rushing-market base; R-005' uses position-specific
                    # SIGMA_SCALE. apply_r001=False in the W9 variant because
                    # the ebayes blend subsumes R-001's shrinkage.
                    prior_mean, prior_n = _prior_full_season(season, pl["name"], market)
                    positional_mean = (R010_POSITIONAL_PRIOR_RB if pos == "RB"
                                       else R010_POSITIONAL_PRIOR_WR)
                    # season_n = total games the player played this season
                    # through wk-1. For in-window games we have gms; for the
                    # rest we need to count from player_stats directly.
                    _psh = ps[(ps.player_display_name == pl["name"]) & (ps.week < wk)]
                    season_n_actual = int(len(_psh))
                    season_mean_actual = (float(_psh.carries.mean()) if market == "rushing"
                                          else float(_psh.receptions.mean())) if season_n_actual else None
                    trailing_mean = base_car_raw if market == "rushing" else base_rec_raw
                    base_r010 = _r010_ebayes_base(
                        trailing_mean, pl_gms,
                        season_mean_actual, season_n_actual,
                        prior_mean, prior_n,
                        positional_mean, pos)
                    # R-005' sigma scale per position (independent of market).
                    sc_r005b = R005B_SIGMA_SCALE_RB if pos == "RB" else R005B_SIGMA_SCALE_WR

                    # Pre-compute R-008 variants that drop other rules so we can
                    # ablate the W8 stack rule-by-rule. The "r008 only" variant
                    # deflates from raw base; the "no_r006" variant deflates
                    # from the R-003 output without blending.
                    base_rec_r008_raw, base_car_r008_raw, _, _ = _r008_deflate(
                        season, wk, team, pos, pl["name"],
                        base_rec_raw, base_car_raw,
                        base_rec_raw, base_car_raw, ps)
                    base_r008_only = base_car_r008_raw if market == "rushing" else base_rec_r008_raw
                    base_rec_r008_no_r006, base_car_r008_no_r006, _, _ = _r008_deflate(
                        season, wk, team, pos, pl["name"],
                        elev["base_rec"], elev["base_car"],
                        base_rec_raw, base_car_raw, ps)
                    base_r008_no_r006 = (base_car_r008_no_r006 if market == "rushing"
                                         else base_rec_r008_no_r006)
                    base_rec_r008_no_r003, base_car_r008_no_r003, _, _ = _r008_deflate(
                        season, wk, team, pos, pl["name"],
                        _blend_small_n(base_rec_raw, sa, pl_gms),
                        _blend_small_n(base_car_raw, sa, pl_gms),
                        base_rec_raw, base_car_raw, ps)
                    base_r008_no_r003 = (base_car_r008_no_r003 if market == "rushing"
                                         else base_rec_r008_no_r003)
                    variants_myp = {
                        "baseline":          compute_myp(market, side, base_raw, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=False)["my_p"],
                        "r002":              compute_myp(market, side, base_raw, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=True)["my_p"],
                        "r003":              compute_myp(market, side, base_r003, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=False)["my_p"],
                        "all":               compute_myp(market, side, base_r003, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=True)["my_p"],
                        "all_r005":          compute_myp(market, side, base_r003, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=True, sigma_scale=sc)["my_p"],
                        "all_r006":          compute_myp(market, side, base_r006_from_r003, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=True)["my_p"],
                        "all_r005_r006":     compute_myp(market, side, base_r006_from_r003, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=True, sigma_scale=sc)["my_p"],
                        # W8 candidates (stacked on top of all_r005_r006).
                        "r007":              compute_myp(market, side, base_r006_from_r007, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=True, sigma_scale=sc)["my_p"],
                        "r008":              compute_myp(market, side, base_r008, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=True, sigma_scale=sc)["my_p"],
                        "r007_r008":         compute_myp(market, side, base_r007_r008, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=True, sigma_scale=sc)["my_p"],
                        # Each rule in isolation vs no-rules baseline.
                        "r005_only":         compute_myp(market, side, base_raw, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=False, sigma_scale=sc)["my_p"],
                        "r006_only":         compute_myp(market, side,
                                                         _blend_small_n(base_raw, sa, pl_gms),
                                                         pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=False)["my_p"],
                        "r008_only":         compute_myp(market, side, base_r008_only, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=False)["my_p"],
                        # Full W8 stack (R-002+R-003+R-005+R-006+R-008) == "r008".
                        # Ablations drop each rule from the W8 stack one at a time.
                        "w8_no_r002":        compute_myp(market, side, base_r008, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=False, sigma_scale=sc)["my_p"],
                        "w8_no_r003":        compute_myp(market, side, base_r008_no_r003, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=True, sigma_scale=sc)["my_p"],
                        "w8_no_r005":        compute_myp(market, side, base_r008, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=True)["my_p"],
                        "w8_no_r006":        compute_myp(market, side, base_r008_no_r006, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=True, sigma_scale=sc)["my_p"],
                        # w8_no_r008 == all_r005_r006 (already present).

                        # W9 draft slice: SIGMA_SCALE bump + R-010 empirical
                        # Bayes base + drop R-001.
                        "sigma_up":          compute_myp(market, side, base_r008, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=True, sigma_scale=sc_r005b)["my_p"],
                        "r010":              compute_myp(market, side, base_r010, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=True, apply_r001=False,
                                                         sigma_scale=sc)["my_p"],
                        "w9":                compute_myp(market, side, base_r010, pace, line,
                                                         pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                         apply_r002=True, apply_r001=False,
                                                         sigma_scale=sc_r005b)["my_p"],
                    }
                    rows.append({"season": season, "week": wk, "player": pl["name"],
                                 "market": market, "side": side,
                                 "line": line, "actual": actual, "y": y,
                                 "r003_fired": elev["fires"],
                                 "r008_fired": r008_fires,
                                 "gms": pl_gms, "small_n": pl_gms < 3,
                                 **{f"myp_{v}": variants_myp[v] for v in variants_myp}})

        df = pd.DataFrame(rows)
        if df.empty:
            print(f"  no scorable props across weeks {a.from_week}-{last}")
            continue
        n_small = int(df.small_n.sum())
        print(f"  {len(df)} props across weeks {a.from_week}-{last}  "
              f"({n_small} with trailing gms<3; R-006 affects those)")
        all_variants = ("baseline", "r002", "r003", "all",
                        "all_r005", "all_r006", "all_r005_r006",
                        "r007", "r008", "r007_r008",
                        "r005_only", "r006_only", "r008_only",
                        "w8_no_r002", "w8_no_r003", "w8_no_r005", "w8_no_r006",
                        "sigma_up", "r010", "w9")
        for v in all_variants:
            b = ((df[f"myp_{v}"] - df.y) ** 2).mean()
            agg_rows.append({"season": season, "variant": v, "n": len(df), "brier": b})

        r003_hits = df[df.r003_fired]
        if len(r003_hits):
            print(f"  R-003 fired on {len(r003_hits)} props "
                  f"(sub-Brier all_r005_r006={((r003_hits.myp_all_r005_r006-r003_hits.y)**2).mean():.4f}, "
                  f"r007={((r003_hits.myp_r007-r003_hits.y)**2).mean():.4f})")
        r008_hits = df[df.r008_fired]
        if len(r008_hits):
            print(f"  R-008 fired on {len(r008_hits)} props "
                  f"(sub-Brier all_r005_r006={((r008_hits.myp_all_r005_r006-r008_hits.y)**2).mean():.4f}, "
                  f"r008={((r008_hits.myp_r008-r008_hits.y)**2).mean():.4f})")
        small = df[df.small_n]
        if len(small):
            print(f"  small-N (gms<3) sub-Brier: "
                  f"all={((small.myp_all-small.y)**2).mean():.4f}, "
                  f"all_r006={((small.myp_all_r006-small.y)**2).mean():.4f}, "
                  f"all_r005_r006={((small.myp_all_r005_r006-small.y)**2).mean():.4f}")
        print()

    if not agg_rows:
        raise SystemExit("no backtest data produced; try --from-week lower")

    all_variants = ["baseline", "r002", "r003", "all",
                    "all_r005", "all_r006", "all_r005_r006",
                    "r007", "r008", "r007_r008",
                    "r005_only", "r006_only", "r008_only",
                    "w8_no_r002", "w8_no_r003", "w8_no_r005", "w8_no_r006",
                    "sigma_up", "r010", "w9"]
    out = pd.DataFrame(agg_rows).pivot(index="season", columns="variant", values="brier")
    out = out[all_variants]
    print("Brier per variant per season (lower is better)")
    print(out.to_string(float_format=lambda v: f"{v:.4f}"))
    print()

    # Delta polarity: positive = variant lowered Brier = better.
    print("Table 1 — deltas vs baseline (no rules). Keeps the W6 codification")
    print("record. Policy was: drop a rule only when every season shows delta < 0.")
    delta_b = -out.sub(out["baseline"], axis=0).drop(columns=["baseline"])
    print(delta_b.to_string(float_format=lambda v: f"{v:+.4f}"))
    print()
    worst_b = delta_b.min()
    for v in ("r002", "r003", "all", "all_r005", "all_r006", "all_r005_r006",
              "r007", "r008", "r007_r008"):
        verdict = "KEEP" if worst_b[v] >= 0 else "DROP"
        print(f"  {v}:  worst-season delta vs baseline {worst_b[v]:+.4f}   →  {verdict}")
    print()

    print("Table 2 — deltas vs 'all' (W6-R001-R002-R003b). Week 7 codification view.")
    incr_cols = ["all_r005", "all_r006", "all_r005_r006"]
    delta_c = -out[incr_cols].sub(out["all"], axis=0)
    print(delta_c.to_string(float_format=lambda v: f"{v:+.4f}"))
    print()
    worst_c = delta_c.min()
    mean_c  = delta_c.mean()
    for v in incr_cols:
        verdict = "KEEP" if worst_c[v] >= -0.0001 else "DROP"
        print(f"  {v}:  worst-season delta vs 'all' {worst_c[v]:+.4f}   mean {mean_c[v]:+.4f}   →  {verdict}")
    print()

    print("Table 3 — deltas vs CURRENT (W7-...-R005-R006 = 'all_r005_r006'). The")
    print("Week 8 codification view: does adding R-007 and/or R-008 on top of W7")
    print("improve calibration?")
    w8_cols = ["r007", "r008", "r007_r008"]
    delta_w = -out[w8_cols].sub(out["all_r005_r006"], axis=0)
    print(delta_w.to_string(float_format=lambda v: f"{v:+.4f}"))
    print()
    worst_w = delta_w.min()
    mean_w  = delta_w.mean()
    for v in w8_cols:
        verdict = "KEEP" if worst_w[v] >= -0.0001 else "DROP"
        print(f"  {v}:  worst-season delta vs 'all_r005_r006' {worst_w[v]:+.4f}   "
              f"mean {mean_w[v]:+.4f}   →  {verdict}")
    print()

    print("Table 4 — each rule IN ISOLATION vs baseline (no rules). Positive =")
    print("rule helps on its own. 'r002' / 'r003' are isolation variants from")
    print("Table 1; 'r005_only' / 'r006_only' / 'r008_only' are rules applied")
    print("directly on the raw trailing base without any of the others.")
    iso_cols = ["r002", "r003", "r005_only", "r006_only", "r008_only"]
    delta_iso = -out[iso_cols].sub(out["baseline"], axis=0)
    print(delta_iso.to_string(float_format=lambda v: f"{v:+.4f}"))
    print()
    for v in iso_cols:
        print(f"  {v:12s} worst {delta_iso[v].min():+.4f}   mean {delta_iso[v].mean():+.4f}")
    print()

    print("Table 6 — W9 DRAFT SLICE vs current W8 (r008). Positive = W9 variant")
    print("improves calibration. Strict policy: ship if worst-season ≥ -0.0001.")
    w9_cols = ["sigma_up", "r010", "w9"]
    delta_w9 = -out[w9_cols].sub(out["r008"], axis=0)
    print(delta_w9.to_string(float_format=lambda v: f"{v:+.4f}"))
    print()
    for v in w9_cols:
        worst_v = delta_w9[v].min()
        mean_v  = delta_w9[v].mean()
        verdict = "KEEP" if worst_v >= -0.0001 else "DROP"
        print(f"  {v:10s} worst {worst_v:+.4f}   mean {mean_v:+.4f}   →  {verdict}")
    print()

    print("Table 5 — ABLATION of the full W8 stack (R-002+R-003+R-005+R-006+R-008 =")
    print("'r008'). Delta vs 'r008' when each rule is dropped — negative means the")
    print("rule is contributing to the stack (dropping it hurts).")
    full = out["r008"]
    abl_cols = ["w8_no_r002", "w8_no_r003", "w8_no_r005", "w8_no_r006", "all_r005_r006"]
    # Rename all_r005_r006 → w8_no_r008 for display clarity.
    abl = out[abl_cols].rename(columns={"all_r005_r006": "w8_no_r008"})
    delta_a = abl.sub(full, axis=0)  # positive = rule's absence raises Brier (= rule helped)
    print(delta_a.to_string(float_format=lambda v: f"{v:+.4f}"))
    print()
    for col in delta_a.columns:
        mean_v = delta_a[col].mean()
        worst_v = delta_a[col].min()  # smallest "benefit from dropping" = worst case for the rule being contributing
        if mean_v > 0.0005:
            verdict = "contributes"
        elif mean_v < -0.0005:
            verdict = "HURTS the stack — consider dropping"
        else:
            verdict = "neutral"
        print(f"  {col:14s} mean +{mean_v:+.4f}  worst {worst_v:+.4f}  →  {verdict}")


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
    ap.add_argument("--sigma-scale", type=float, default=0.30,
                    help="R-005 candidate: sigma_eff = max(SIGMA, base × this). "
                         "Default 0.30. Sweep via repeated runs: 0.20, 0.25, 0.30, 0.35.")
    a = ap.parse_args()
    if a.mode == "link":
        _run_link_mode(a)
    else:
        _run_brier_mode(a)


if __name__ == "__main__":
    main()
