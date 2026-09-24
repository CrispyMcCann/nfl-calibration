#!/usr/bin/env python3
"""Score the log: Brier, calibration curve, and the residual edge test.

    python score.py
    python score.py --min-n 100 --plot calibration.png

Three Brier scores get printed. Read them in this order:

  naive   predicting the base rate on everything. If you do not beat
          this, you know nothing. It is the floor, not the target.
  market  the devigged line. This is the real bar and it is hard.
  you     your own numbers.

Then the edge test. Your edge column is (my_p - market_p). If your
structural read adds information the market has not already priced,
that column should correlate with the outcome AFTER market_p is
accounted for. That is what the regression at the bottom checks.
"""

from __future__ import annotations
import argparse, pathlib
import numpy as np
import pandas as pd

CSV = pathlib.Path(__file__).parent / "predictions.csv"


def brier(p, o):
    return float(np.mean((np.asarray(p, float) - np.asarray(o, float)) ** 2))


def load() -> pd.DataFrame:
    if not CSV.exists():
        raise SystemExit("no predictions.csv yet — log some predictions first")
    df = pd.read_csv(CSV)
    df = df[df.outcome.notna() & (df.outcome != "")]
    # push/dnp/skip carry no scoreable outcome
    unscoreable = df[df.outcome.astype(str).isin(["push", "dnp", "skip"])]
    df = df[~df.outcome.astype(str).isin(["push", "dnp", "skip"])]
    if len(unscoreable):
        print(f"(excluded {len(unscoreable)} unscoreable rows: "
              + ", ".join(f"{k} {v}" for k, v in
                          unscoreable.outcome.value_counts().items()) + ")")
    if df.empty:
        raise SystemExit("no scoreable rows yet — run resolve.py")
    df["outcome"] = df.outcome.astype(float)
    return df


def calibration(df: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    edges = np.linspace(0, 1, bins + 1)
    df = df.assign(bucket=pd.cut(df.my_p, bins=edges, include_lowest=True))
    t = df.groupby("bucket", observed=True).agg(
        n=("outcome", "size"),
        stated=("my_p", "mean"),
        actual=("outcome", "mean"),
    )
    t["gap"] = t.actual - t.stated          # positive = you were underconfident
    return t


def calibration_ci(df: pd.DataFrame, bins: int = 10, n_boot: int = 1000,
                   seed: int = 0) -> pd.DataFrame:
    """Cluster-bootstrapped 95% CI on decile observed frequencies.

    Props within a game share a game script, so their outcomes are
    correlated. A row-level bootstrap would understate the sampling
    error. Resampling games (with replacement) treats each game as the
    independent unit, which it is.

    Returns the point-estimate calibration table plus lo/hi bounds on
    the `actual` column.
    """
    point = calibration(df, bins)
    if "game" not in df or df.game.nunique() < 2:
        point["lo"] = np.nan
        point["hi"] = np.nan
        return point
    rng = np.random.default_rng(seed)
    games = df.game.unique()
    boot = {b: [] for b in point.index}
    for _ in range(n_boot):
        pick = rng.choice(games, size=len(games), replace=True)
        rows = pd.concat([df[df.game == g] for g in pick], ignore_index=True)
        t = calibration(rows, bins)
        for b in point.index:
            boot[b].append(t.actual.get(b, np.nan))
    point["lo"] = [np.nanpercentile(boot[b], 2.5) for b in point.index]
    point["hi"] = [np.nanpercentile(boot[b], 97.5) for b in point.index]
    return point


def edge_test(df: pd.DataFrame) -> str:
    """Does my edge explain anything market_p does not?

    Logistic regression of outcome on [market_p, edge]. If the coefficient
    on edge is not clearly positive, your read is already in the price.
    """
    try:
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        return "install scikit-learn to run the edge test"
    if len(df) < 40 or df.outcome.nunique() < 2:
        return "not enough resolved rows for the edge test (want 100+)"
    X = df[["market_p", "edge"]].to_numpy(float)
    y = df.outcome.to_numpy(float)
    m = LogisticRegression(max_iter=1000).fit(X, y)
    b_mkt, b_edge = m.coef_[0]
    verdict = ("your edge adds signal" if b_edge > 0.25 else
               "your edge adds nothing the price did not have")
    return (f"  coef on market_p : {b_mkt:+.3f}\n"
            f"  coef on edge     : {b_edge:+.3f}   -> {verdict}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-n", type=int, default=100)
    ap.add_argument("--plot", default=None)
    ap.add_argument("--bootstrap", type=int, default=1000,
                    help="number of game-clustered bootstrap resamples for "
                         "calibration CIs; 0 skips")
    a = ap.parse_args()

    df = load()
    n = len(df)
    base = df.outcome.mean()
    n_games = df.game.nunique() if "game" in df else n

    print(f"\n{n} resolved predictions across {n_games} games   "
          f"base rate {base:.3f}")
    print(f"  effective sample is closer to {n_games} than to {n}: props on the "
          "same game\n  share a game script, so their errors are correlated. "
          "Treat the game\n  count as your n when you decide whether a result "
          "means anything.")
    if n < a.min_n:
        print(f"\nWARNING: under {a.min_n} rows. Early numbers are noise — "
              "read them, do not act on them.")

    print(f"\n  brier you    {brier(df.my_p, df.outcome):.4f}")
    print(f"  brier market {brier(df.market_p, df.outcome):.4f}")
    print(f"  brier naive  {brier(np.full(n, base), df.outcome):.4f}")

    print("\ncalibration (gap > 0 = underconfident, < 0 = overconfident)")
    if a.bootstrap and n_games >= 2:
        print(f"  [lo, hi] are 95% CIs from {a.bootstrap} game-clustered "
              "bootstrap resamples\n")
        t = calibration_ci(df, n_boot=a.bootstrap)
    else:
        print()
        t = calibration(df)
    print(t.to_string(float_format=lambda v: f"{v:.3f}"))

    print("\nedge test")
    print(edge_test(df))

    by = df.groupby("market").apply(
        lambda d: pd.Series({
            "n": len(d),
            "you": brier(d.my_p, d.outcome),
            "market": brier(d.market_p, d.outcome),
        }), include_groups=False)
    print("\nby market type\n")
    print(by.to_string(float_format=lambda v: f"{v:.4f}"))

    if "tier" in df and df.tier.notna().any():
        print("\ncore vs control — the hypothesis should win on BOTH, in "
              "opposite directions\n")
        ct = df.groupby("tier").apply(
            lambda d: pd.Series({
                "n": len(d),
                "games": d.game.nunique() if "game" in d else len(d),
                "you": brier(d.my_p, d.outcome),
                "market": brier(d.market_p, d.outcome),
            }), include_groups=False)
        print(ct.to_string(float_format=lambda v: f"{v:.4f}"))
        print("\nIf you beat the market on core but not control, you are not"
              "\nseeing game script — you are just biased toward overs.")

    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(5, 5))
        ax.plot([0, 1], [0, 1], "--", lw=1, color="#8A8D93")
        if "lo" in t and t.lo.notna().any():
            ax.fill_between(t.stated, t.lo, t.hi, alpha=0.15,
                            color="#2B4C7E", label="95% CI (game-clustered)")
        ax.plot(t.stated, t.actual, "o-", color="#2B4C7E", label="observed")
        ax.legend(loc="lower right", frameon=False, fontsize=9)
        ax.set_xlabel("your stated probability")
        ax.set_ylabel("observed frequency")
        ax.set_title(f"Calibration, n={n} rows, {n_games} games")
        ax.set_xlim(0, 1); ax.set_ylim(0, 1)
        fig.tight_layout(); fig.savefig(a.plot, dpi=150)
        print(f"\nwrote {a.plot}")


if __name__ == "__main__":
    main()
