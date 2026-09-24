#!/usr/bin/env python3
"""Append one prediction to predictions.csv. Never edits, only appends.

    python log.py --game "SEA @ WAS" --week 3 \
        --player "Terry McLaurin" --market receptions --line 4.5 \
        --side over --my-p 0.61 --odds -115 --opp-odds -105 \
        --why "WAS 30th D, implied 16.8 pts, trails from Q2; 20% tgt share"

--my-p is YOUR number. Write it before you look at the odds screen.
The odds go in afterwards and the script devigs them for you.
"""

from __future__ import annotations
import argparse, csv, datetime as dt, pathlib, uuid

CSV = pathlib.Path(__file__).parent / "predictions.csv"

# Canonical schema. Must stay in sync with SCHEMA.md, slate.py's --json
# queue stub, and the "Prediction Log" UI's CSV_COLS. Order matters.
FIELDS = [
    # identifiers & timing
    "id", "logged_at", "season", "week", "game_date", "kickoff", "weekday",
    # game & mechanism
    "game", "team", "side_of_mechanism", "tier", "expect",
    # player & market
    "player", "position", "market", "line", "side",
    # prediction
    "my_p", "why",
    # opening price
    "odds", "opp_odds", "market_p", "edge",
    # closing price (usually filled by the UI's Close price button)
    "close_odds", "close_opp_odds", "market_p_close", "edge_close", "closed_at",
    # controls (game context, from slate)
    "own_def_rank", "def_rank_opp", "def_gap",
    "implied_total", "deficit", "spread", "total", "roof", "pace",
    "base_tgt_share", "base_rec", "base_car", "base_snap",
    # skip & amendment audit
    "skip_reason", "amendments",
    # outcome (written only by resolve.py)
    "actual", "outcome", "resolved_at",
]


def american_to_prob(odds: float) -> float:
    """-115 -> 0.535,  +105 -> 0.488.  Includes the vig."""
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def devig(odds: float, opp_odds: float) -> float:
    """Normalise the two sides so they sum to 1. That strips the margin."""
    a, b = american_to_prob(odds), american_to_prob(opp_odds)
    return a / (a + b)


def append(row: dict) -> None:
    new = not CSV.exists()
    with CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def _ask(prompt, cast=str, allow_blank=False):
    while True:
        raw = input(prompt).strip()
        if not raw and allow_blank:
            return None
        try:
            return cast(raw)
        except Exception:
            print("  ...try again")


def _pick(label, options, render):
    print(f"\n{label}")
    for i, o in enumerate(options, 1):
        print(f"  {i:2d}. {render(o)}")
    while True:
        try:
            i = int(input("  > ").strip())
            if 1 <= i <= len(options):
                return options[i - 1]
        except Exception:
            pass
        print("  ...pick a number from the list")


def interactive(season: int, week: int) -> None:
    """Guided logging. Asks for YOUR number before it shows you any odds —
    the ordering is the point, not a convenience."""
    from slate import build_slate
    from nflcal import data as D

    s = build_slate(season, week)
    if s.empty:
        raise SystemExit(f"no qualifying games in week {week}")

    g = _pick("Which game?", list(s.itertuples()),
              lambda r: f"{r.game:12s}  trailing {r.trailing:3s} (D{r.trail_def_rank:2d})  "
                        f"leading {r.leading:3s} (D{r.lead_def_rank:2d})  "
                        f"spread {r.spread:+.1f}  signal {r.signal:.0f}")

    side = _pick("Which side of the mechanism?",
                 [("trailing", g.trailing, "WR", "receiving volume"),
                  ("leading", g.leading, "RB", "rushing volume")],
                 lambda t: f"{t[0]:8s} {t[1]}  -> predict {t[3]}")
    _, team, pos, _ = side

    u = D.usage(season, week - 1, team)
    cand = u[u.position == pos]
    cand = cand.sort_values("tgt_share" if pos == "WR" else "car", ascending=False).head(6)
    if cand.empty:
        cand = u.head(6)
    player = _pick(f"Which {team} {pos}?", list(cand.itertuples()),
                   lambda r: f"{r.player_display_name:24s} tgt {r.tgt:4.1f}  "
                             f"share {r.tgt_share:.2f}  rec {r.rec:4.1f}  "
                             f"car {r.car:4.1f}  snaps {r.snap_pct or 0:.0%}")

    default_mkt = "receptions" if pos == "WR" else "rush_attempts"
    mkt = _ask(f"Market [{default_mkt}]: ", str, allow_blank=True) or default_mkt
    line = _ask("Line (e.g. 4.5): ", float)
    sd = (_ask("Side over/under [over]: ", str, allow_blank=True) or "over").lower()

    print("\n  --- your number first. do not look at the odds screen yet ---")
    my_p = _ask(f"P({sd} {line} {mkt}) as 0-1: ", float)
    why = _ask("Why? one or two sentences naming the mechanism:\n  > ", str)

    print("\n  --- now open the odds ---")
    odds = _ask(f"American odds, {sd}: ", float)
    opp = _ask(f"American odds, other side: ", float)

    is_home = g.game.split(" @ ")[1] == team
    implied = g.imp_home if is_home else g.imp_away
    opp_rank = g.lead_def_rank if side[0] == "trailing" else g.trail_def_rank
    own_rank = g.trail_def_rank if side[0] == "trailing" else g.lead_def_rank

    tier_map = {("trailing", "receptions"): ("core_pass", "over"),
                ("leading", "rush_attempts"): ("core_rush", "over"),
                ("leading", "receptions"): ("control_pass", "under"),
                ("trailing", "rush_attempts"): ("control_rush", "under")}
    tier, expect = tier_map.get((side[0], mkt), ("", ""))

    paces = D.pace(season, week - 1).to_dict()
    base = player._asdict() if hasattr(player, "_asdict") else {}

    mp = devig(odds, opp)
    row = {
        "id": uuid.uuid4().hex[:10],
        "logged_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "season": season, "week": week, "game_date": g.date,
        "kickoff": g.kickoff, "weekday": g.weekday,
        "game": g.game, "team": team, "side_of_mechanism": side[0],
        "tier": tier, "expect": expect,
        "player": player.player_display_name, "position": pos,
        "market": mkt, "line": line, "side": sd,
        "my_p": round(my_p, 4), "why": why,
        "odds": odds, "opp_odds": opp,
        "market_p": round(mp, 4), "edge": round(my_p - mp, 4),
        "close_odds": "", "close_opp_odds": "",
        "market_p_close": "", "edge_close": "", "closed_at": "",
        "own_def_rank": own_rank, "def_rank_opp": opp_rank,
        "def_gap": g.def_gap, "implied_total": implied, "deficit": g.deficit,
        "spread": g.spread, "total": g.total, "roof": g.roof,
        "pace": round(float(paces.get(team, float("nan"))), 1),
        "base_tgt_share": round(float(base.get("tgt_share", 0) or 0), 3),
        "base_rec": round(float(base.get("rec", 0) or 0), 1),
        "base_car": round(float(base.get("car", 0) or 0), 1),
        "base_snap": round(float(base.get("snap_pct", 0) or 0), 2),
        "skip_reason": "", "amendments": "",
        "actual": "", "outcome": "", "resolved_at": "",
    }
    append(row)
    print(f"\n  logged {row['id']}   you {my_p:.3f}   market {mp:.3f}   "
          f"edge {row['edge']:+.3f}")
    if abs(row["edge"]) > 0.15:
        print("  that is a big claimed edge on a liquid market — sure?")

    if (_ask("\nLog another? [y/N]: ", str, allow_blank=True) or "n").lower().startswith("y"):
        interactive(season, week)
    else:
        print("\ncommit predictions.csv — the git timestamp is what makes this evidence")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-i", "--interactive", action="store_true",
                   help="guided prompts; pulls game context from the slate")
    p.add_argument("--season", type=int, default=2026)
    p.add_argument("--week", type=int, required=True)
    p.add_argument("--game")
    p.add_argument("--game-date", default="")
    p.add_argument("--player")
    p.add_argument("--position", default="")
    p.add_argument("--market",
                   help="receptions | rush_attempts | receiving_yards | rushing_yards")
    p.add_argument("--line", type=float)
    p.add_argument("--side", choices=["over", "under"], default="over")
    p.add_argument("--my-p", type=float,
                   help="your probability the chosen side hits, 0-1")
    p.add_argument("--odds", type=float, help="American odds, your side")
    p.add_argument("--opp-odds", type=float, help="American odds, other side")
    p.add_argument("--def-rank-opp", type=int, default=None)
    p.add_argument("--implied-total", type=float, default=None)
    p.add_argument("--spread", type=float, default=None)
    p.add_argument("--roof", default="")
    p.add_argument("--why", help="the mechanism, in one or two sentences")
    a = p.parse_args()

    if a.interactive:
        interactive(a.season, a.week)
        return

    missing = [f for f in ("game", "player", "market", "line", "my_p", "odds",
                           "opp_odds", "why") if getattr(a, f) is None]
    if missing:
        raise SystemExit("missing: " + ", ".join("--" + m.replace("_", "-")
                                                 for m in missing)
                         + "\n(or just run:  python log.py --week N -i)")

    if not 0 < a.my_p < 1:
        raise SystemExit("--my-p must be strictly between 0 and 1")

    mp = devig(a.odds, a.opp_odds)
    # Flag-mode CLI is a fallback path; columns not passed as flags are
    # left empty, and the UI is expected to be the primary writer that
    # populates the full analytical context.
    row = {f: "" for f in FIELDS}
    row.update({
        "id": uuid.uuid4().hex[:10],
        "logged_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "season": a.season, "week": a.week, "game_date": a.game_date,
        "game": a.game, "player": a.player, "position": a.position,
        "market": a.market, "line": a.line, "side": a.side,
        "my_p": round(a.my_p, 4), "why": a.why,
        "odds": a.odds, "opp_odds": a.opp_odds,
        "market_p": round(mp, 4), "edge": round(a.my_p - mp, 4),
        "def_rank_opp": a.def_rank_opp, "implied_total": a.implied_total,
        "spread": a.spread, "roof": a.roof,
    })
    append(row)
    print(f"logged {row['id']}  you {a.my_p:.3f}  market {mp:.3f}  "
          f"edge {row['edge']:+.3f}")
    print("commit predictions.csv now — the git timestamp is what makes this evidence")


if __name__ == "__main__":
    main()
