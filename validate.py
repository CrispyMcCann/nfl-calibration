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
                     positional_mean: float, position: str,
                     use_positional_prior: bool = False) -> float:
    """R-010 empirical-Bayes base. See METHODOLOGY.md deferred-candidates
    (via memory). The idea: shrink the player's rate toward prior-season
    (or positional fallback) with pseudo-count = Efron-Morris halflife;
    then blend with trailing-3 for recency weight. R-014 (W11): the
    caller forces the positional prior when a role change since last
    season makes last year's rate the wrong anchor.
    """
    k = R010_HALFLIFE_RB if position == "RB" else R010_HALFLIFE_WR

    # Pick the prior: previous-season average when we have ≥5 games on
    # record; otherwise fall back to the positional mean (rookies, new).
    # R-014 override: force positional prior when role has changed.
    if use_positional_prior:
        prior = positional_mean
    else:
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


# W10 surgical-fix constants. See the "Between Week 9 and Week 10" section
# of METHODOLOGY.md for the rationale (variance bump, anti-overconfidence
# clamp, R-010 magnitude cap).
W10_SIGMA_SCALE_RB_HI = 0.70
W10_SIGMA_SCALE_WR_HI = 0.75
W10_SIGMA_SCALE_RB_HIGHER = 0.80
W10_SIGMA_SCALE_WR_HIGHER = 0.85


def _clip_p(p: float, lo: float, hi: float) -> float:
    """Tighten the W9 [0.02, 0.98] clamp to a narrower interval, post hoc."""
    return max(lo, min(hi, float(p)))


def _r010_capped(base_r010: float, trailing_mean: float,
                 pct: float) -> float:
    """Cap the magnitude of R-010's move from trailing_mean to pct%
    (e.g., pct=0.20 means the R-010 output can deviate from the raw
    trailing mean by at most 20% in either direction)."""
    base_r010 = float(base_r010 or 0)
    tr = float(trailing_mean or 0)
    if tr <= 0:
        return base_r010
    lo = tr * (1.0 - pct)
    hi = tr * (1.0 + pct)
    return max(lo, min(hi, base_r010))


def _w10_candidates(market, side, base_r010, pace, line, pass_rk, rush_rk,
                    pos, trailing_mean) -> dict:
    """Return the dict of W10 surgical-fix variant my_p values for one
    prop. Separated from the big literal so each variant is on one line
    and easy to audit.

    Variants:
      sigma_075:  W9 + sigma_scale 0.70 RB / 0.75 WR (variance bump).
      sigma_085:  W9 + sigma_scale 0.80 RB / 0.85 WR (bigger bump).
      clamp_05:   W9 with my_p clamped to [0.05, 0.95] post-hoc.
      clamp_10:   W9 with my_p clamped to [0.10, 0.90].
      r010_cap20: W9 with R-010 shrinkage capped at ±20% of trailing mean.
      r010_cap30: W9 with R-010 shrinkage capped at ±30%.
      sigma_clamp_05 / _10: combined.
      sigma_cap20 / cap30:  sigma bump + R-010 cap.
      w10_all:    sigma_075 + clamp_10 + cap20 (triple).
    """
    sc_hi = W10_SIGMA_SCALE_RB_HI if pos == "RB" else W10_SIGMA_SCALE_WR_HI
    sc_higher = W10_SIGMA_SCALE_RB_HIGHER if pos == "RB" else W10_SIGMA_SCALE_WR_HIGHER
    sc_w9 = R005B_SIGMA_SCALE_RB if pos == "RB" else R005B_SIGMA_SCALE_WR

    base_cap20 = _r010_capped(base_r010, trailing_mean, 0.20)
    base_cap30 = _r010_capped(base_r010, trailing_mean, 0.30)

    def _myp(base, scale):
        return compute_myp(market, side, base, pace, line,
                           pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                           apply_r002=True, apply_r001=False,
                           sigma_scale=scale)["my_p"]

    w9       = _myp(base_r010, sc_w9)
    s_075    = _myp(base_r010, sc_hi)
    s_085    = _myp(base_r010, sc_higher)
    cap20    = _myp(base_cap20, sc_w9)
    cap30    = _myp(base_cap30, sc_w9)
    s_cap20  = _myp(base_cap20, sc_hi)
    s_cap30  = _myp(base_cap30, sc_hi)
    w10_all_ = _myp(base_cap20, sc_hi)

    return {
        "sigma_075":       s_075,
        "sigma_085":       s_085,
        "clamp_05":        _clip_p(w9, 0.05, 0.95),
        "clamp_10":        _clip_p(w9, 0.10, 0.90),
        "r010_cap20":      cap20,
        "r010_cap30":      cap30,
        "sigma_clamp_05":  _clip_p(s_075, 0.05, 0.95),
        "sigma_clamp_10":  _clip_p(s_075, 0.10, 0.90),
        "sigma_cap20":     s_cap20,
        "sigma_cap30":     s_cap30,
        "w10_all":         _clip_p(w10_all_, 0.10, 0.90),
    }


def _run_brier_mode(a) -> None:
    """Per-variant Brier backtest. See the module docstring for policy."""
    seasons = [int(x) for x in a.seasons.split(",")]
    variants = ["baseline", "r002", "r003", "all"] if a.variant == "all-table" else [a.variant]

    print("line is a synthetic proxy (median-rounded half-point); absolute")
    print("Brier here is NOT comparable to the live record. Read DELTAS")
    print("between variants on the same proxy, not levels.\n")

    agg_rows = []
    all_dfs = []
    # R-015 (W12) side table: parallel picks under four primary modes.
    # Rows per (slot, mode) tagged with the mode and the W11-live my_p
    # (w11_nopace formula + r014_r13 ebayes). Table 12 aggregates by
    # (season, mode); pre-declared protocol picks a winner on 2023-24
    # and confirms on 2025.
    r015_rows = []
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
                    # W11 candidates: how R-008 composes with the capped R-010
                    # base. Live W10 subtracts R-008's deflation from the R-010
                    # output; R-010 has usually already pulled the trailing
                    # mean toward teammate-present games, so that can count
                    # the same inflation twice.
                    d_r008 = max(0.0, base_raw - base_r008_only)
                    base_cap20_w11 = _r010_capped(base_r010, trailing_mean, 0.20)
                    base_w10_live = max(0.0, base_cap20_w11 - d_r008)
                    # Non-stacking: R-008 subtracts only what R-010 has not
                    # already removed from the trailing mean. When R-010
                    # moved the base up, this equals the live stack.
                    already_removed = max(0.0, trailing_mean - base_cap20_w11)
                    base_w11_nostack = max(0.0, base_cap20_w11
                                           - max(0.0, d_r008 - already_removed))
                    sc_w10 = W10_SIGMA_SCALE_RB_HI if pos == "RB" else W10_SIGMA_SCALE_WR_HI

                    # R-014 (W11): role-change prior. When current-season rate
                    # differs from prior-season rate by at least `ratio` in
                    # either direction (≥4 current games, ≥5 prior games), swap
                    # R-010's prior from last-year mean to the positional
                    # prior; otherwise reproduce w11_nopace exactly.
                    def _r014_variant(ratio: float) -> tuple[float, bool]:
                        fired = False
                        if (prior_mean is not None and prior_n >= 5
                                and prior_mean > 0
                                and season_n_actual >= 4
                                and season_mean_actual is not None):
                            rr = float(season_mean_actual) / float(prior_mean)
                            if rr >= ratio or rr <= (1.0 / ratio):
                                fired = True
                        if fired:
                            base_r010_r014 = _r010_ebayes_base(
                                trailing_mean, pl_gms,
                                season_mean_actual, season_n_actual,
                                prior_mean, prior_n,
                                positional_mean, pos,
                                use_positional_prior=True)
                        else:
                            base_r010_r014 = base_r010
                        base_cap_r014 = _r010_capped(base_r010_r014, trailing_mean, 0.20)
                        already_r014 = max(0.0, trailing_mean - base_cap_r014)
                        base_r014 = max(0.0, base_cap_r014
                                        - max(0.0, d_r008 - already_r014))
                        myp = compute_myp(market, side, base_r014, 60.0, line,
                                          pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                          apply_r002=True, apply_r001=False,
                                          sigma_scale=sc_w10)["my_p"]
                        return myp, fired
                    myp_r014_13, fired_13 = _r014_variant(1.3)
                    myp_r014_15, fired_15 = _r014_variant(1.5)
                    myp_r014_20, fired_20 = _r014_variant(2.0)

                    # R-012 grid (plans/R-012-game-script.md). Script multiplier
                    # goes in usage_factor, so sigma stays tied to the base.
                    sctx = D.script_context(season, team, wk, pl["name"])
                    dm = sctx["d_margin"] if sctx else 0.0
                    dt = sctx["d_total"] if sctx else 0.0
                    r012_mult = {
                        "0": 1.0,
                        "1": D.script_mult_r012(pos, dm, dt, use_margin=True, use_total=False),
                        "2": D.script_mult_r012(pos, dm, dt, use_margin=False, use_total=True),
                        "3": D.script_mult_r012(pos, dm, dt, use_margin=True, use_total=True),
                    }

                    def _grid(stack: str, cell: str) -> float:
                        base_g, pace_g = ((base_w10_live, pace) if stack == "A"
                                          else (base_w11_nostack, 60.0))
                        return compute_myp(market, side, base_g, pace_g, line,
                                           pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                           usage_factor=r012_mult[cell],
                                           apply_r002=True, apply_r001=False,
                                           sigma_scale=sc_w10)["my_p"]
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
                    # W10 share-model variants. Projection comes from
                    # team_vol × final_share × opp_mult; sigma scaling
                    # reuses R-005' (position-aware). apply_r002=False
                    # because opp_mult is already folded into the
                    # projection. Position is passed so σ_eff scales per
                    # position.
                    def _share_myp(k_share: float, apply_ebayes: bool,
                                   apply_roster: bool,
                                   opp_mag: float) -> float:
                        opp_rank_pos = (int(pass_rk) if market == "receiving"
                                        and pass_rk is not None
                                        else (int(rush_rk) if rush_rk is not None
                                              else None))
                        sp = D.share_projection(
                            season, wk, team, pl["name"], pos,
                            opp_rank=opp_rank_pos,
                            depth_team=pl.get("depth_team"),
                            k_share=k_share,
                            apply_ebayes=apply_ebayes,
                            apply_roster=apply_roster,
                            opp_mult_magnitude=opp_mag)
                        return compute_myp(
                            market, side, sp["projection"], 60.0, line,
                            pass_rank_opp=None, rush_rank_opp=None,
                            apply_r002=False, apply_r001=False,
                            sigma_scale=sc_r005b, position=pos)["my_p"]

                    if a.grid_only:
                        _share_myp = lambda *x, **k: float("nan")  # noqa: E731
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

                        # W10 surgical-fix candidates. Each targets the
                        # W9 overconfidence pattern without an architecture
                        # rebuild. σ-bump = wider my_p spread; clamp =
                        # hard anti-overconfidence ceiling; R-010 cap =
                        # dampens shrinkage magnitude so it doesn't swing
                        # in either direction.
                        **(lambda: _w10_candidates(
                            market, side, base_r010, pace, line, pass_rk, rush_rk,
                            pos, trailing_mean))(),
                        # W11: live W10 stack (R-010cap then R-008) vs
                        # non-stacking R-008 (take the larger downward move).
                        "w10_live":      compute_myp(market, side, base_w10_live, pace, line,
                                                     pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                     apply_r002=True, apply_r001=False,
                                                     sigma_scale=sc_w10)["my_p"],
                        "w11_nostack":   compute_myp(market, side, base_w11_nostack, pace, line,
                                                     pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                     apply_r002=True, apply_r001=False,
                                                     sigma_scale=sc_w10)["my_p"],
                        # W11 review candidates, each on the non-stacking base:
                        # no pace multiplier (base was produced at the team's
                        # own pace already), fixed sigma floors (residual SD of
                        # volume is ~flat in the mean, not proportional), both.
                        "w11_nopace":    compute_myp(market, side, base_w11_nostack, 60.0, line,
                                                     pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                     apply_r002=True, apply_r001=False,
                                                     sigma_scale=sc_w10)["my_p"],
                        "w11_flatsig":   compute_myp(market, side, base_w11_nostack, pace, line,
                                                     pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                     apply_r002=True, apply_r001=False,
                                                     sigma_scale=0.0)["my_p"],
                        "w11_nopace_flatsig": compute_myp(market, side, base_w11_nostack, 60.0, line,
                                                     pass_rank_opp=pass_rk, rush_rank_opp=rush_rk,
                                                     apply_r002=True, apply_r001=False,
                                                     sigma_scale=0.0)["my_p"],
                        # R-012 declared grid: A = live W10, B = w11_nopace.
                        **{f"grid_{st}{c}": _grid(st, c) for st in "AB" for c in "0123"},
                        # R-014 declared variants (plans/R-014-role-change-prior.md):
                        # swap R-010's prior from last-year to positional when a
                        # role change is detected. Base is w11_nopace's chain.
                        "r014_r13": myp_r014_13,
                        "r014_r15": myp_r014_15,
                        "r014_r20": myp_r014_20,
                        # W10 share-model variants.
                        "share_model":   _share_myp(10, apply_ebayes=False,
                                                     apply_roster=False, opp_mag=0.05),
                        "share_ebayes":  _share_myp(10, apply_ebayes=True,
                                                     apply_roster=False, opp_mag=0.05),
                        "share_full":    _share_myp(10, apply_ebayes=True,
                                                     apply_roster=True, opp_mag=0.05),
                        # k_share sweep (ebayes + full roster).
                        "share_k5":      _share_myp(5,  apply_ebayes=True,
                                                     apply_roster=True, opp_mag=0.05),
                        "share_k15":     _share_myp(15, apply_ebayes=True,
                                                     apply_roster=True, opp_mag=0.05),
                        "share_k20":     _share_myp(20, apply_ebayes=True,
                                                     apply_roster=True, opp_mag=0.05),
                        # opp_mult magnitude sweep.
                        "share_opp003":  _share_myp(10, apply_ebayes=True,
                                                     apply_roster=True, opp_mag=0.03),
                        "share_opp008":  _share_myp(10, apply_ebayes=True,
                                                     apply_roster=True, opp_mag=0.08),
                    }
                    rows.append({"season": season, "week": wk, "player": pl["name"],
                                 "market": market, "side": side,
                                 "line": line, "actual": actual, "y": y,
                                 "r003_fired": elev["fires"],
                                 "r008_fired": r008_fires,
                                 "r014_fired_13": fired_13,
                                 "r014_fired_15": fired_15,
                                 "r014_fired_20": fired_20,
                                 "gms": pl_gms, "small_n": pl_gms < 3,
                                 **{f"myp_{v}": variants_myp[v] for v in variants_myp}})

                    # R-015 sidecar. For each primary_mode variant,
                    # re-pick the player and run the W11-live pipeline
                    # end-to-end on THAT player. The trailing_3 row above
                    # is the live baseline; the 3 depth_* rows measure
                    # whether swapping the slate's picker improves Brier.
                    slot_id = f"{season}-{wk}-{g.game}-{side}-{pos}"
                    def _w11_live_myp(pl_m):
                        """Run the live W11 chain (R-003 → R-010 cap → R-011
                        nostack → formula no pace → R-005c σ) with the R-014
                        role-change check on the ebayes prior. Returns
                        (actual, y, myp_live) or None if the player has no
                        valid stats/line this week."""
                        name = pl_m["name"]
                        a_m = _actual_for(ps, name, wk, market)
                        if a_m is None: return None
                        line_m = _line_proxy(ps, name, wk - 1, market)
                        if line_m is None: return None
                        rec_raw = float(pl_m.get("rec", 0) or 0)
                        car_raw = float(pl_m.get("car", 0) or 0)
                        elev_m = D.elevation_r003(season, wk, team, pos,
                                                  rec_raw, car_raw,
                                                  exclude_player=name)
                        base_r003_m = (elev_m["base_car"] if market == "rushing"
                                       else elev_m["base_rec"])
                        trailing_m = car_raw if market == "rushing" else rec_raw
                        gms_m = int(pl_m.get("gms", 0))
                        pr_mean_m, pr_n_m = _prior_full_season(season, name, market)
                        _hist = ps[(ps.player_display_name == name) & (ps.week < wk)]
                        snm = int(len(_hist)); smm = None
                        if snm:
                            smm = float((_hist.carries if market == "rushing"
                                         else _hist.receptions).mean())
                        # R-014 role-change check
                        use_pos = False
                        if (pr_mean_m is not None and pr_n_m >= 5
                                and pr_mean_m > 0 and snm >= 4 and smm is not None):
                            r_ratio = float(smm) / float(pr_mean_m)
                            if r_ratio >= 1.3 or r_ratio <= 1/1.3:
                                use_pos = True
                        pos_mean = (R010_POSITIONAL_PRIOR_RB if pos == "RB"
                                    else R010_POSITIONAL_PRIOR_WR)
                        b10 = _r010_ebayes_base(trailing_m, gms_m,
                                                smm, snm, pr_mean_m, pr_n_m,
                                                pos_mean, pos,
                                                use_positional_prior=use_pos)
                        b_cap = _r010_capped(b10, trailing_m, 0.20)
                        # R-008 deflation on the player's own raw
                        rec_r008_raw_m, car_r008_raw_m, _, _ = _r008_deflate(
                            season, wk, team, pos, name,
                            rec_raw, car_raw, rec_raw, car_raw, ps)
                        b_r008_only = (car_r008_raw_m if market == "rushing"
                                       else rec_r008_raw_m)
                        d_r008_m = max(0.0, trailing_m - b_r008_only)
                        already = max(0.0, trailing_m - b_cap)
                        base_live = max(0.0, b_cap
                                        - max(0.0, d_r008_m - already))
                        pr_mo = int(ratings.loc[opp_team, "pass_def_rank"]) \
                            if opp_team in ratings.index else None
                        rr_mo = int(ratings.loc[opp_team, "rush_def_rank"]) \
                            if opp_team in ratings.index else None
                        sc_live = (W10_SIGMA_SCALE_RB_HI if pos == "RB"
                                   else W10_SIGMA_SCALE_WR_HI)
                        myp = compute_myp(market, side, base_live, 60.0, line_m,
                                          pass_rank_opp=pr_mo, rush_rank_opp=rr_mo,
                                          apply_r002=True, apply_r001=False,
                                          sigma_scale=sc_live)["my_p"]
                        over_hit_m = 1 if a_m > line_m else 0
                        y_m = over_hit_m if side == "over" else (1 - over_hit_m)
                        return (a_m, y_m, myp)
                    # Trailing-3 mode: already computed above as `pl`; reuse.
                    r015_rows.append({"season": season, "week": wk,
                                      "slot": slot_id, "mode": "trailing_3",
                                      "player": pl["name"], "y": y,
                                      "myp": variants_myp["r014_r13"]})
                    for mode in ("depth_strict", "depth_safe", "depth_season"):
                        pl_m = D.primary_at(season, wk - 1, team, pos,
                                            primary_mode=mode)
                        if pl_m is None: continue
                        out = _w11_live_myp(pl_m)
                        if out is None: continue
                        a_m, y_m, myp_m = out
                        r015_rows.append({"season": season, "week": wk,
                                          "slot": slot_id, "mode": mode,
                                          "player": pl_m["name"], "y": y_m,
                                          "myp": myp_m})

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
                        "sigma_up", "r010", "w9",
                        "share_model", "share_ebayes", "share_full",
                        "share_k5", "share_k15", "share_k20",
                        "share_opp003", "share_opp008",
                        "sigma_075", "sigma_085", "clamp_05", "clamp_10",
                        "r010_cap20", "r010_cap30",
                        "sigma_clamp_05", "sigma_clamp_10",
                        "sigma_cap20", "sigma_cap30", "w10_all",
                        "w10_live", "w11_nostack", "w11_nopace",
                        "w11_flatsig", "w11_nopace_flatsig",
                        *[f"grid_{st}{c}" for st in "AB" for c in "0123"],
                        "r014_r13", "r014_r15", "r014_r20")
        for v in all_variants:
            b = ((df[f"myp_{v}"] - df.y) ** 2).mean()
            agg_rows.append({"season": season, "variant": v, "n": len(df), "brier": b})
        df["season"] = season
        all_dfs.append(df)

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
        r8 = df[df.r008_fired]
        if len(r8):
            print(f"  W11 check on {len(r8)} R-008-fired props: "
                  f"w10_live={((r8.myp_w10_live-r8.y)**2).mean():.4f}  "
                  f"w11_nostack={((r8.myp_w11_nostack-r8.y)**2).mean():.4f}  "
                  f"no_r008(sigma_cap20)={((r8.myp_sigma_cap20-r8.y)**2).mean():.4f}")
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
                    "sigma_up", "r010", "w9",
                    "share_model", "share_ebayes", "share_full",
                    "share_k5", "share_k15", "share_k20",
                    "share_opp003", "share_opp008",
                    "sigma_075", "sigma_085", "clamp_05", "clamp_10",
                    "r010_cap20", "r010_cap30",
                    "sigma_clamp_05", "sigma_clamp_10",
                    "sigma_cap20", "sigma_cap30", "w10_all",
                    "w10_live", "w11_nostack", "w11_nopace",
                    "w11_flatsig", "w11_nopace_flatsig",
                    *[f"grid_{st}{c}" for st in "AB" for c in "0123"],
                    "r014_r13", "r014_r15", "r014_r20"]
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

    print("Table 8 — W10 SURGICAL FIXES vs current W9 ('w9'). Positive = fix")
    print("improves calibration. Strict policy: ship a fix if worst-season")
    print("delta >= -0.0001 AND mean delta >= +0.003. 'nearly' = worst in")
    print("[-0.001, -0.0001] with mean > 0 (borderline; judgement call).")
    w10_cols = ["sigma_075", "sigma_085", "clamp_05", "clamp_10",
                "r010_cap20", "r010_cap30",
                "sigma_clamp_05", "sigma_clamp_10",
                "sigma_cap20", "sigma_cap30", "w10_all"]
    delta_w10 = -out[w10_cols].sub(out["w9"], axis=0)
    print(delta_w10.to_string(float_format=lambda v: f"{v:+.4f}"))
    print()
    for v in w10_cols:
        worst_v = delta_w10[v].min()
        mean_v  = delta_w10[v].mean()
        strict_pass = (worst_v >= -0.0001) and (mean_v >= 0.003)
        if strict_pass:
            verdict = "SHIP"
        elif (worst_v >= -0.001) and (mean_v > 0):
            verdict = "nearly (borderline)"
        elif worst_v >= -0.0001:
            verdict = "marginal"
        else:
            verdict = "DROP"
        print(f"  {v:18s} worst {worst_v:+.4f}   mean {mean_v:+.4f}   →  {verdict}")
    print()

    print("Table 9 — W11: R-008 composition vs LIVE W10 ('w10_live' = R-010cap")
    print("then R-008, sigma 0.70/0.75). 'sigma_cap20' = W10 without R-008.")
    print("Strict policy: worst-season >= -0.0001 AND mean >= +0.003.")
    w11_cols = ["w11_nostack", "sigma_cap20", "w11_nopace",
                "w11_flatsig", "w11_nopace_flatsig"]
    delta_w11 = -out[w11_cols].sub(out["w10_live"], axis=0)
    print(delta_w11.to_string(float_format=lambda v: f"{v:+.4f}"))
    print()
    for v in w11_cols:
        worst_v = delta_w11[v].min()
        mean_v  = delta_w11[v].mean()
        strict_pass = (worst_v >= -0.0001) and (mean_v >= 0.003)
        verdict = ("SHIP" if strict_pass else
                   "no-harm (worst >= -0.0001), below mean bar" if worst_v >= -0.0001 else "DROP")
        print(f"  {v:14s} worst {worst_v:+.4f}   mean {mean_v:+.4f}   →  {verdict}")
    print()

    print("Table 10 — R-012 DECLARED GRID vs live W10 (grid_A0 == w10_live).")
    print("Protocol (plans/R-012-game-script.md): pick the winner on 2023-2024")
    print("with strict policy; confirm only the winner on 2025 (delta >= -0.0001).")
    gcols = [f"grid_{st}{c}" for st in "AB" for c in "0123" if f"{st}{c}" != "A0"]
    dg = -out[gcols].sub(out["grid_A0"], axis=0)
    print(dg.to_string(float_format=lambda v: f"{v:+.4f}"))
    print()
    sel = [s_ for s_ in (2023, 2024) if s_ in dg.index]
    cands = []
    for v in gcols:
        w_ = dg.loc[sel, v].min(); m_ = dg.loc[sel, v].mean()
        ok = (w_ >= -0.0001) and (m_ >= 0.003)
        print(f"  {v:8s} 2023-24 worst {w_:+.4f} mean {m_:+.4f}  →  {'passes' if ok else 'fails'}")
        if ok:
            cands.append((m_, v))
    if cands:
        win = max(cands)[1]
        conf = dg.loc[2025, win] if 2025 in dg.index else float("nan")
        verdict = "SHIP" if conf >= -0.0001 else "FAILS 2025 — nothing ships"
        print(f"\n  winner on 2023-24: {win}   2025 delta {conf:+.4f}  →  {verdict}")
    else:
        print("\n  no cell passes on 2023-24 — nothing from the grid ships")
    print()

    print("Table 11 — R-014 DECLARED VARIANTS vs w11_nopace (live W10b base).")
    print("Protocol (plans/R-014-role-change-prior.md): pick on 2023-2024 only,")
    print("bar = worst-season delta ≥ -0.0001 AND mean > 0; then confirm on 2025")
    print("(2025 delta ≥ -0.0001) or nothing ships. Looser than W10's bar because")
    print("R-014 fires on a minority of props and averaging dilutes any subset rule.")
    r14_cols = ["r014_r13", "r014_r15", "r014_r20"]
    dr14 = -out[r14_cols].sub(out["w11_nopace"], axis=0)
    print(dr14.to_string(float_format=lambda v: f"{v:+.4f}"))
    print()
    sel = [s_ for s_ in (2023, 2024) if s_ in dr14.index]
    cands14 = []
    for v in r14_cols:
        w_ = dr14.loc[sel, v].min(); m_ = dr14.loc[sel, v].mean()
        ok = (w_ >= -0.0001) and (m_ > 0)
        print(f"  {v:10s} 2023-24 worst {w_:+.4f} mean {m_:+.4f}  →  {'passes' if ok else 'fails'}")
        if ok:
            cands14.append((m_, v))
    if cands14:
        win = max(cands14)[1]
        conf = dr14.loc[2025, win] if 2025 in dr14.index else float("nan")
        verdict = "SHIP" if conf >= -0.0001 else "FAILS 2025 — nothing ships"
        print(f"\n  winner on 2023-24: {win}   2025 delta {conf:+.4f}  →  {verdict}")
    else:
        print("\n  nothing passes on 2023-24 — R-014 does not ship")

    # Subgroup detail: how often does each variant fire, and on firing props
    # does it actually help?
    print("\n  R-014 fire counts and sub-Brier on firing props:")
    big = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
    if not big.empty:
        for r, col, fire_col in (("1.3", "r014_r13", "r014_fired_13"),
                                  ("1.5", "r014_r15", "r014_fired_15"),
                                  ("2.0", "r014_r20", "r014_fired_20")):
            fires = big[big[fire_col] == True]
            if len(fires):
                b_live = ((fires["myp_w11_nopace"] - fires.y) ** 2).mean()
                b_r14  = ((fires[f"myp_{col}"] - fires.y) ** 2).mean()
                print(f"    r={r}: {len(fires):4d} firing props  "
                      f"w11_nopace Brier {b_live:.4f}  →  {col} {b_r14:.4f}  "
                      f"(Δ {b_live - b_r14:+.4f})")
            else:
                print(f"    r={r}: 0 firing props")
    print()

    print("Table 12 — R-015 DECLARED VARIANTS (plans/R-015-depth-chart-primary.md).")
    print("Depth-chart-based primary selection vs live trailing_3. Protocol: pick a")
    print("mode on 2023-2024 (bar: worst-season Δ ≥ -0.0001 AND mean > 0), then")
    print("confirm on 2025 (Δ ≥ -0.0001). Each row is one (slot, mode) pick evaluated")
    print("under the W11 formula (w11_nopace + r014_r13).")
    r15 = pd.DataFrame(r015_rows)
    if r15.empty:
        print("  no R-015 rows produced — skip")
    else:
        # Keep only slots where ALL modes produced a valid pick, so each
        # mode's season mean is computed over the same slots — otherwise
        # differing slot sets would bias the comparison.
        modes = ["trailing_3", "depth_strict", "depth_safe", "depth_season"]
        slot_mode_count = r15.groupby("slot").mode.nunique()
        full_slots = set(slot_mode_count[slot_mode_count == len(modes)].index)
        r15f = r15[r15.slot.isin(full_slots)].copy()
        r15f["brier"] = (r15f.myp - r15f.y) ** 2
        tab = (r15f.groupby(["season", "mode"]).brier.mean()
               .unstack("mode")[modes])
        print(f"  {len(full_slots)} slots with all 4 modes present; "
              f"{len(r15) - len(r15f)} rows dropped for unequal coverage")
        print("\nmean Brier per (season, mode):")
        print(tab.to_string(float_format=lambda v: f"{v:.4f}"))
        dr15 = -tab.subtract(tab["trailing_3"], axis=0).drop(columns=["trailing_3"])
        print("\ndelta vs trailing_3 (positive = R-015 variant better):")
        print(dr15.to_string(float_format=lambda v: f"{v:+.4f}"))
        print()
        sel = [s_ for s_ in (2023, 2024) if s_ in dr15.index]
        cands = []
        for v in ("depth_strict", "depth_safe", "depth_season"):
            w_ = dr15.loc[sel, v].min(); m_ = dr15.loc[sel, v].mean()
            ok = (w_ >= -0.0001) and (m_ > 0)
            print(f"  {v:14s} 2023-24 worst {w_:+.4f} mean {m_:+.4f}  →  "
                  f"{'passes' if ok else 'fails'}")
            if ok: cands.append((m_, v))
        if cands:
            win = max(cands)[1]
            conf = dr15.loc[2025, win] if 2025 in dr15.index else float("nan")
            verdict = "SHIP" if conf >= -0.0001 else "FAILS 2025 — nothing ships"
            print(f"\n  winner on 2023-24: {win}   2025 delta {conf:+.4f}  →  {verdict}")
        else:
            print("\n  nothing passes on 2023-24 — R-015 does not ship")

        # Disagreement sub-analysis: how often does each depth mode pick a
        # different player than trailing_3, and on those divergent slots
        # does depth-chart actually beat trailing-3?
        print("\n  R-015 disagreement counts and sub-Brier on divergent slots:")
        for v in ("depth_strict", "depth_safe", "depth_season"):
            t3 = r15f[r15f["mode"] == "trailing_3"].set_index("slot")
            dm = r15f[r15f["mode"] == v].set_index("slot")
            common = t3.index.intersection(dm.index)
            diverg = [s for s in common if t3.loc[s, "player"] != dm.loc[s, "player"]]
            if not diverg:
                print(f"    {v}: 0 divergent slots")
                continue
            t3_b = ((t3.loc[diverg, "myp"] - t3.loc[diverg, "y"]) ** 2).mean()
            dm_b = ((dm.loc[diverg, "myp"] - dm.loc[diverg, "y"]) ** 2).mean()
            print(f"    {v:14s}: {len(diverg):4d} divergent slots  "
                  f"trailing_3 {t3_b:.4f}  →  {v} {dm_b:.4f}  "
                  f"(Δ {t3_b - dm_b:+.4f})")
    print()

    print("Table 7 — W10 SHARE MODEL vs current W9 ('w9'). Positive = share")
    print("variant improves calibration. Strict policy: ship share_full if")
    print("(a) worst-season delta ≥ -0.0001, (b) mean delta ≥ +0.003, both vs W9.")
    share_cols = ["share_model", "share_ebayes", "share_full",
                  "share_k5", "share_k15", "share_k20",
                  "share_opp003", "share_opp008"]
    delta_share = -out[share_cols].sub(out["w9"], axis=0)
    print(delta_share.to_string(float_format=lambda v: f"{v:+.4f}"))
    print()
    for v in share_cols:
        worst_v = delta_share[v].min()
        mean_v = delta_share[v].mean()
        strict_pass = (worst_v >= -0.0001) and (mean_v >= 0.003)
        verdict = "SHIP" if strict_pass else ("marginal" if worst_v >= -0.0001 else "DROP")
        print(f"  {v:14s} worst {worst_v:+.4f}   mean {mean_v:+.4f}   →  {verdict}")
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

# ---------------------------------------------------------------------------
# R-016 (plans/R-016-count-dist-ewma.md): count distribution + EWMA base.
# Baseline M0 is the live W11 chain built with the SAME nflcal.data calls
# slate.py makes (R-003 → R-010/R-014 → cap → R-008 net of R-010), not the
# R-015 sidecar's re-implementation. Scored over seven half-point lines at
# proxy offsets −3…+3 so a too-wide sigma can't hide next to the median.
# ---------------------------------------------------------------------------
R016_OFFSETS = (-3, -2, -1, 0, 1, 2, 3)
R016_SIGMA_FLOOR = {"RB": 5.5, "WR": 2.5}
R016_SIGMA_SCALE = {"RB": 0.70, "WR": 0.75}


def _ewma(vals, hl: float) -> float | None:
    """Exponentially weighted mean, newest last; weight halves every hl games."""
    v = np.asarray(vals, dtype=float)
    if len(v) == 0:
        return None
    w = 0.5 ** (np.arange(len(v))[::-1] / hl)
    return float((w * v).sum() / w.sum())


def _r016_live_base(season, wk, team, pos, pl, market):
    """Live W11 base, exactly as slate.stub() composes it, plus one
    ablation per rule (each drops exactly one rule from the chain).
    Returns a dict of bases for `market`."""
    mk = "car" if market == "rushing" else "rec"
    raw = {"rec": float(pl.get("rec", 0.0) or 0), "car": float(pl.get("car", 0.0) or 0)}
    pl_gms = int(pl.get("gms") or 0)
    n_season = D.season_games(season, pl["name"], wk - 1)
    sa = D.season_avg(season, pl["name"], wk - 1, market)
    pr, pr_n = D.prior_full_season(season, pl["name"], market)
    mk14 = "rushing" if pos == "RB" else "receiving"
    r014 = D.role_changed(season, pl["name"], wk - 1, mk14, D.R014_RATIO) and mk14 == market

    def chain(r003=True, r010=True, cap=True, r014_on=True, r008=True):
        if r003:
            elev = D.elevation_r003(season, wk, team, pos, raw["rec"], raw["car"],
                                    exclude_player=pl["name"])
            e = {"rec": float(elev["base_rec"]), "car": float(elev["base_car"])}
        else:
            e = dict(raw)
        if r010:
            b10 = round(D.ebayes_base(e[mk], pl_gms, sa, n_season, pr, pr_n, pos,
                                      apply_cap=cap,
                                      use_positional_prior=r014 and r014_on), 2)
        else:
            b10 = e[mk]
        if not r008:
            return float(b10)
        b10s = {"rec": b10 if mk == "rec" else e["rec"], "car": b10 if mk == "car" else e["car"]}
        defl = D.deflation_r008(season, wk, team, pos, pl["name"], b10s["rec"], b10s["car"],
                                raw["rec"], raw["car"])
        return float(D.r008_net_of_r010(e[mk], b10, defl[f"base_{mk}"]))

    out = {"base_live": chain(), "abl_no_r003": chain(r003=False),
           "abl_no_r010": chain(r010=False), "abl_no_cap": chain(cap=False),
           "abl_no_r014": chain(r014_on=False), "abl_no_r008": chain(r008=False),
           "raw_t3": raw[mk]}
    elev = D.elevation_r003(season, wk, team, pos, raw["rec"], raw["car"],
                            exclude_player=pl["name"])
    defl_raw = D.deflation_r008(season, wk, team, pos, pl["name"], raw["rec"], raw["car"],
                                raw["rec"], raw["car"])
    out["d003"] = float(elev[f"base_{mk}"]) - raw[mk]
    out["d008"] = raw[mk] - float(defl_raw[f"base_{mk}"])
    return out


def _r016_collect(seasons, from_week) -> pd.DataFrame:
    from tools.calc import matchup_adjust_r002
    rows = []
    for season in seasons:
        ps = D._player_stats(season)
        if "season_type" in ps:
            ps = ps[ps.season_type == "REG"]
        sch = nfl.load_schedules([season]).to_pandas()
        done = sch[sch.result.notna() & (sch.game_type == "REG")]
        if done.empty:
            continue
        last = int(done.week.max())
        for wk in range(max(from_week, 4), last + 1):
            try:
                s = build_slate(season, wk)
            except Exception:
                continue
            if s.empty:
                continue
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
                    line = _line_proxy(ps, pl["name"], wk - 1, market)
                    if actual is None or line is None:
                        continue
                    opp = g.leading if role == "trailing" else g.trailing
                    prk = int(ratings.loc[opp, "pass_def_rank"]) if opp in ratings.index else None
                    rrk = int(ratings.loc[opp, "rush_def_rank"]) if opp in ratings.index else None
                    adj = matchup_adjust_r002(market, prk, rrk)
                    lb = _r016_live_base(season, wk, team, pos, pl, market)
                    col = "carries" if market == "rushing" else "receptions"
                    hist = ps[(ps.player_display_name == pl["name"]) & (ps.week < wk)] \
                        .sort_values("week")[col].fillna(0).values
                    pr_mean, pr_n = D.prior_full_season(season, pl["name"], market)
                    rows.append({"season": season, "week": wk, "game": g.game,
                                 "player": pl["name"], "pos": pos, "market": market,
                                 "side": side, "line": line, "actual": float(actual),
                                 "adj_r002": adj, **lb,
                                 "n_hist": len(hist),
                                 "ew3": _ewma(hist, 3), "ew4": _ewma(hist, 4),
                                 "prior": pr_mean if (pr_mean is not None and pr_n >= 5) else np.nan})
        print(f"  {season}: {sum(r['season'] == season for r in rows)} slots")
    return pd.DataFrame(rows)


def _r016_mean(df: pd.DataFrame, cand: str) -> pd.Series:
    """Expected value (incl. R-002 unless the id ends in '-r002off')."""
    r002 = not cand.endswith("-r002off")
    c = cand.replace("-r002off", "")
    if c == "M0":
        base = df.base_live
    else:  # M1-h{3,4}-k{0,3}-inj{0,1}
        _, h, k, inj = c.split("-")
        ew = df[f"ew{h[1:]}"].fillna(df.base_live)
        k = float(k[1:])
        n = df.n_hist.astype(float)
        has_p = df.prior.notna() & (k > 0)
        base = ew.where(~has_p, (n * ew + k * df.prior) / (n + k))
        if inj == "inj1":
            base = base + df.d003 - df.d008
        base = base.clip(lower=0.0)
    return (base + (df.adj_r002 if r002 else 0.0)).clip(lower=0.0), base


def _r016_p_over(dist: str, mu, base, pos, t, par):
    from scipy.stats import norm, nbinom
    mu = np.maximum(np.asarray(mu, float), 0.05)
    if dist == "D0":
        floor = np.where(pos == "RB", R016_SIGMA_FLOOR["RB"], R016_SIGMA_FLOOR["WR"])
        scale = np.where(pos == "RB", R016_SIGMA_SCALE["RB"], R016_SIGMA_SCALE["WR"])
        sig = np.maximum(floor, np.asarray(base, float) * scale)
        return 1 - norm.cdf((t - mu) / sig)
    if dist == "D2":
        sig = np.where(pos == "RB", par["sd"]["RB"], par["sd"]["WR"])
        return 1 - norm.cdf((t - mu) / sig)
    r = np.where(pos == "RB", par["r"]["RB"], par["r"]["WR"])
    return 1 - nbinom.cdf(np.floor(t), r, r / (r + mu))


def _r016_logscore(dist, mu, base, pos, y, par):
    from scipy.stats import norm, nbinom
    mu = np.maximum(np.asarray(mu, float), 0.05)
    if dist == "D1":
        r = np.where(pos == "RB", par["r"]["RB"], par["r"]["WR"])
        p = nbinom.pmf(y, r, r / (r + mu))
    else:
        if dist == "D0":
            floor = np.where(pos == "RB", R016_SIGMA_FLOOR["RB"], R016_SIGMA_FLOOR["WR"])
            scale = np.where(pos == "RB", R016_SIGMA_SCALE["RB"], R016_SIGMA_SCALE["WR"])
            sig = np.maximum(floor, np.asarray(base, float) * scale)
        else:
            sig = np.where(pos == "RB", par["sd"]["RB"], par["sd"]["WR"])
        p = norm.cdf((y + 0.5 - mu) / sig) - norm.cdf((y - 0.5 - mu) / sig)
    return float(np.log(np.clip(p, 1e-6, 1)).mean())


def _r016_brier(df, cand, dist, par) -> float:
    mu, base = _r016_mean(df, cand)
    pos = df.pos.values
    out = []
    for o in R016_OFFSETS:
        t = df.line.values + o
        po = _r016_p_over(dist, mu.values, base.values, pos, t, par)
        p = np.where(df.side.values == "over", po, 1 - po)
        p = np.clip(p, 0.02, 0.98)
        hit = (df.actual.values > t).astype(float)
        y = np.where(df.side.values == "over", hit, 1 - hit)
        out.append(((p - y) ** 2).mean())
    return float(np.mean(out))


def _r016_fit(df_tr, cand) -> dict:
    """Dispersion per position on the training seasons, for this mean."""
    mu, _ = _r016_mean(df_tr, cand)
    par = {"r": {}, "sd": {}}
    for pos in ("RB", "WR"):
        m = df_tr.pos == pos
        res = df_tr.actual[m] - mu[m]
        excess = float((res ** 2 - mu[m]).sum())
        par["r"][pos] = float((mu[m] ** 2).sum() / excess) if excess > 0 else 1e6
        par["sd"][pos] = float(np.sqrt((res ** 2).mean()))
    return par


def _pregame_only_effectively_out():
    """No-look-ahead mode. Game-day inactives (roster status INA) are
    published ~90 min before kickoff, after the owner logs. Rewrite INA
    to ACT in the weekly roster so R-003, R-008 and primary_at see what
    the owner can see: injury-report Out plus IR/PUP/cut moves."""
    orig = D._rosters_weekly
    cache = {}
    def patched(season):
        if season not in cache:
            rw = orig(season).copy()
            rw.loc[rw.status == "INA", "status"] = "ACT"
            cache[season] = rw
        return cache[season]
    D._rosters_weekly = patched


def _run_r016_mode(a) -> None:
    seasons = [int(x) for x in a.seasons.split(",")]
    if a.pregame_info:
        _pregame_only_effectively_out()
        print("PREGAME-INFO MODE: game-day inactives (INA) hidden from R-003/R-008/primary_at\n")
    print("R-016 backtest — plans/R-016-count-dist-ewma.md")
    print("lines: proxy ± {−3…+3}; Brier = mean over the 7 lines. Fit on "
          f"{seasons[:-1]}, hold out {seasons[-1]}.\n")
    df = _r016_collect(seasons, a.from_week)
    if a.dump:
        df.to_pickle(a.dump)
        print(f"  rows dumped to {a.dump}")
    train, hold = df[df.season.isin(seasons[:-1])], df[df.season == seasons[-1]]
    means = ["M0"] + [f"M1-h{h}-k{k}-inj{i}" for h in (3, 4) for k in (0, 3) for i in (0, 1)]

    # Step 1 — select the mean per position on TRAIN under D1 (each
    # candidate gets its own fitted r). Same choice reported under D0.
    print("Table 1 — mean candidates, TRAIN Brier (lower = better)")
    sel = {}
    for pos in ("RB", "WR"):
        tp = train[train.pos == pos]
        rows = []
        for c in means:
            par = _r016_fit(train, c)
            rows.append((c, _r016_brier(tp, c, "D0", par), _r016_brier(tp, c, "D1", par)))
        t = pd.DataFrame(rows, columns=["mean", "D0", "D1"]).set_index("mean")
        print(f"\n  {pos}\n" + t.to_string(float_format=lambda v: f"{v:.4f}"))
        sel[pos] = t.D1.drop("M0").idxmin()
        print(f"  → selected {pos} mean: {sel[pos]}")

    # Mixed per-position mean: evaluate by splitting.
    def brier_combo(d, mean_by_pos, dist, par_by_pos, r002=True):
        tot, n = 0.0, 0
        for pos in ("RB", "WR"):
            dp = d[d.pos == pos]
            if dp.empty:
                continue
            c = mean_by_pos[pos] + ("" if r002 else "-r002off")
            tot += _r016_brier(dp, c, dist, par_by_pos[pos]) * len(dp); n += len(dp)
        return tot / n

    def pars(mean_by_pos, r002=True):
        return {pos: _r016_fit(train, mean_by_pos[pos] + ("" if r002 else "-r002off"))
                for pos in ("RB", "WR")}

    live = {"RB": "M0", "WR": "M0"}
    combos = {
        "LIVE  M0 + D0":            (live, "D0", True),
        "dist  M0 + D1 (NegBin)":   (live, "D1", True),
        "dist  M0 + D2 (const σ)":  (live, "D2", True),
        "mean  M1* + D0":           (sel, "D0", True),
        "r002  M0 − R-002 + D0":    (live, "D0", False),
        "COMBO M1* + D1":           (sel, "D1", True),
        "COMBO M1* + D1 − R-002":   (sel, "D1", False),
    }
    print("\nTable 2 — per-season Brier (7-line mean) and Δ vs LIVE (+ = better)")
    res = {}
    for name, (mb, dist, r2) in combos.items():
        pp = pars(mb, r2)
        res[name] = {s: brier_combo(df[df.season == s], mb, dist, pp, r2) for s in seasons}
        res[name]["logscore_hold"] = sum(
            _r016_logscore(dist, *_r016_mean(hold[hold.pos == p],
                           mb[p] + ("" if r2 else "-r002off")),
                           hold[hold.pos == p].pos.values,
                           hold[hold.pos == p].actual.values, pp[p]) * (hold.pos == p).sum()
            for p in ("RB", "WR")) / len(hold)
    out = pd.DataFrame(res).T
    for s in seasons:
        out[f"Δ{s}"] = out.loc["LIVE  M0 + D0", s] - out[s]
    print(out.to_string(float_format=lambda v: f"{v:+.4f}" if abs(v) < 0.05 else f"{v:.4f}"))
    print("\n(Δ > 0 in every season = passes the pre-declared ship rule.)")
    print(f"\nFitted dispersion on TRAIN for the selected means: "
          + ", ".join(f"{p}: r={_r016_fit(train, sel[p])['r'][p]:.2f}" for p in ("RB", "WR")))
    print(f"n slots: train {len(train)}, hold {len(hold)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--from-week", type=int, default=4,
                    help="skip the first weeks; ratings are mostly prior before then")
    ap.add_argument("--mode", choices=("link", "brier", "r016"), default="link")
    ap.add_argument("--pregame-info", action="store_true",
                    help="r016 mode: hide game-day inactives (no look-ahead)")
    ap.add_argument("--dump", default=None,
                    help="r016 mode: pickle the collected slot rows here")
    ap.add_argument("--seasons", default="2023,2024,2025",
                    help="brier mode only: comma-separated seasons to backtest")
    ap.add_argument("--variant", default="all-table",
                    choices=("baseline", "r002", "r003", "all", "all-table"),
                    help="brier mode only; 'all-table' prints every variant")
    ap.add_argument("--grid-only", action="store_true",
                    help="skip the slow share-model variants (their columns are NaN)")
    ap.add_argument("--sigma-scale", type=float, default=0.30,
                    help="R-005 candidate: sigma_eff = max(SIGMA, base × this). "
                         "Default 0.30. Sweep via repeated runs: 0.20, 0.25, 0.30, 0.35.")
    a = ap.parse_args()
    if a.mode == "link":
        _run_link_mode(a)
    elif a.mode == "r016":
        _run_r016_mode(a)
    else:
        _run_brier_mode(a)


if __name__ == "__main__":
    main()
