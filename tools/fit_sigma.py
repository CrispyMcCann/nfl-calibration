#!/usr/bin/env python3
"""Fit empirical sigma for RB carries and WR receptions from
2023-2025 regular-season data. Used to seed METHODOLOGY.md's
Week 4 sigma values. Rerun after each season to update.

    python tools/fit_sigma.py
"""
from __future__ import annotations
import numpy as np, pandas as pd
import nflreadpy as nfl

def load():
    frames = []
    for season in [2023, 2024, 2025]:
        df = nfl.load_player_stats([season]).to_pandas()
        if "season_type" in df.columns:
            df = df[df.season_type == "REG"]
        frames.append(df[["season","week","player_display_name","position",
                          "team","carries","receptions"]])
    ps = pd.concat(frames, ignore_index=True)
    ps["carries"] = ps.carries.fillna(0).astype(float)
    ps["receptions"] = ps.receptions.fillna(0).astype(float)
    return ps.sort_values(["player_display_name","season","week"])

def add_trailing_mean(df: pd.DataFrame, stat: str, n: int = 3) -> pd.DataFrame:
    g = df.groupby("player_display_name", group_keys=False)
    df[f"{stat}_expected"] = g[stat].apply(
        lambda s: s.rolling(n, min_periods=n).mean().shift(1))
    return df

def fit(df: pd.DataFrame, stat: str, min_expected: float, label: str):
    d = df.dropna(subset=[f"{stat}_expected"]).copy()
    d = d[d[f"{stat}_expected"] >= min_expected]
    d["residual"] = d[stat] - d[f"{stat}_expected"]
    d["expected"] = d[f"{stat}_expected"]
    print(f"\n=== {label} ===")
    print(f"n={len(d)}  distinct players={d.player_display_name.nunique()}")
    print(f"constant sigma = {d.residual.std():.3f}")
    k = np.sqrt((d.residual**2 / d.expected).mean())
    print(f"sqrt-law k     = {k:.3f}   (sigma = k * sqrt(expected))")
    print("empirical SD by bucket:")
    d["bin"] = pd.cut(d.expected, bins=[0,5,10,15,20,25,30,100],
                       right=False)
    tab = d.groupby("bin", observed=True).agg(
        n=("residual", "size"),
        emp_sd=("residual", "std"),
        mean_expected=("expected", "mean"))
    print(tab.round(2).to_string())

if __name__ == "__main__":
    ps = load()
    rb = add_trailing_mean(ps[ps.position == "RB"].copy(), "carries")
    fit(rb, "carries", 5, "RB rushing attempts (expected >= 5)")
    wr = add_trailing_mean(ps[ps.position == "WR"].copy(), "receptions")
    fit(wr, "receptions", 2, "WR receptions (expected >= 2)")
