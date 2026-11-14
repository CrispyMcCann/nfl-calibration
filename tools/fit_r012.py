#!/usr/bin/env python3
"""Fit R-012 game-script coefficients. See plans/R-012-game-script.md.

    python tools/fit_r012.py                 # fit on 2019-2022 (the only fit window)

Poisson regression with log link and base as exposure:
    actual ~ offset(log base) + intercept + b_margin·d_margin + b_total·d_total
(fit as rate actual/base with sample_weight=base, which is the same model).
The intercept absorbs average regression of the trailing base; only the
slopes become R012_COEF. CIs are a bootstrap over game-weeks, because props
in the same game share one script.

2023-2025 are the backtest seasons and must never be added to the fit.
"""

from __future__ import annotations
import pathlib, sys

import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from nflcal import data as D

FIT_SEASONS = (2019, 2020, 2021, 2022)
POOL = {"RB": ("carries", 8.0), "WR": ("receptions", 2.5)}   # same thresholds as the W10 review
N_BOOT = 300


def build(season: int) -> pd.DataFrame:
    ps = D._player_stats(season)
    if "season_type" in ps:
        ps = ps[ps.season_type == "REG"]
    gs = D.team_game_script(season)
    gs_idx = gs.set_index(["team", "week"])
    rows = []
    for pos, (col, minb) in POOL.items():
        p = ps[ps.position == pos]
        for (name, team), g in p.groupby(["player_display_name", "team"]):
            g = g.sort_values("week")
            wk_val = dict(zip(g.week.astype(int), g[col].astype(float)))
            for wk in sorted(wk_val):
                win = [w for w in range(wk - 3, wk) if w in wk_val]
                if not win:
                    continue
                base = float(np.mean([wk_val[w] for w in win]))
                if base < minb or (team, wk) not in gs_idx.index:
                    continue
                ctx = [gs_idx.loc[(team, w)] for w in win if (team, w) in gs_idx.index]
                if not ctx:
                    continue
                now = gs_idx.loc[(team, wk)]
                rows.append({
                    "season": season, "week": wk, "pos": pos, "team": team,
                    "base": base, "actual": wk_val[wk],
                    "d_margin": float(now.exp_margin) - float(np.mean([c.exp_margin for c in ctx])),
                    "d_total": float(now.imp_total) - float(np.mean([c.imp_total for c in ctx])),
                })
    return pd.DataFrame(rows)


def fit(x: pd.DataFrame) -> np.ndarray:
    m = PoissonRegressor(alpha=0.0, max_iter=1000)
    m.fit(x[["d_margin", "d_total"]].values, (x.actual / x.base).values,
          sample_weight=x.base.values)
    return m.coef_


def main():
    d = pd.concat([build(s) for s in FIT_SEASONS], ignore_index=True)
    rng = np.random.default_rng(12)
    print(f"fit seasons {FIT_SEASONS}; bootstrap {N_BOOT} reps over game-weeks\n")
    for pos in ("RB", "WR"):
        x = d[d.pos == pos].reset_index(drop=True)
        coef = fit(x)
        clusters = x.groupby(["season", "week", "team"]).indices
        keys = list(clusters)
        boots = []
        for _ in range(N_BOOT):
            pick = rng.integers(0, len(keys), len(keys))
            idx = np.concatenate([clusters[keys[i]] for i in pick])
            boots.append(fit(x.iloc[idx]))
        lo, hi = np.percentile(np.array(boots), [2.5, 97.5], axis=0)
        print(f"{pos}  n={len(x)}  "
              f"b_margin {coef[0]:+.4f}  [{lo[0]:+.4f}, {hi[0]:+.4f}]   "
              f"b_total {coef[1]:+.4f}  [{lo[1]:+.4f}, {hi[1]:+.4f}]")
        print(f"     sd d_margin {x.d_margin.std():.2f}  sd d_total {x.d_total.std():.2f}  "
              f"→ 1 sd of d_margin moves expected by {np.exp(coef[0]*x.d_margin.std())-1:+.1%}")


if __name__ == "__main__":
    main()
