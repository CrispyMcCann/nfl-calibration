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
    # pushes and did-not-plays have no outcome to score
    skipped = df[df.outcome.astype(str).isin(["push", "dnp"])]
    df = df[~df.outcome.astype(str).isin(["push", "dnp"])]
    if len(skipped):
        print(f"(excluded {len(skipped)} unscoreable rows: "
              + ", ".join(f"{k} {v}" for k, v in
                          skipped.outcome.value_counts().items()) + ")")
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

    print("\ncalibration (gap > 0 = underconfident, < 0 = overconfident)\n")
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
        ax.plot(t.stated, t.actual, "o-", color="#2B4C7E")
        ax.set_xlabel("your stated probability")
        ax.set_ylabel("observed frequency")
        ax.set_title(f"Calibration, n={n}")
        ax.set_xlim(0, 1); ax.set_ylim(0, 1)
        fig.tight_layout(); fig.savefig(a.plot, dpi=150)
        print(f"\nwrote {a.plot}")


if __name__ == "__main__":
    main()
