#!/usr/bin/env python3
"""Fold the logger UI's CSV export into predictions.csv.

    python merge.py              # reads ui_export.csv
    python merge.py --dry-run    # report what would change, write nothing
    python merge.py path/to/export.csv

The UI exports its whole table every time, but it never sees the outcomes
resolve.py writes. Pasting an export straight over predictions.csv would
therefore erase every resolved outcome. This script is the only way UI
rows reach predictions.csv, and it enforces SCHEMA.md's writer table:

- a row id not yet in predictions.csv is appended;
- an existing row takes the export's closing-price columns (the UI's
  Close price button) and, only when the export's `amendments` list is
  longer, the amendable forecast fields (a recorded pre-kickoff edit);
- `actual`, `outcome`, `resolved_at` belong to resolve.py and are never
  overwritten once filled; a recorded closing price is never erased;
- an amendment is accepted only if it is dated before the row's kickoff;
- any other difference on an existing row aborts the merge and nothing
  is written;
- rows in predictions.csv that the export lacks are kept untouched.

Every value is handled as text, so nothing is reformatted.
"""

from __future__ import annotations
import argparse, csv, datetime as dt, json, pathlib, sys

from log import FIELDS

HERE = pathlib.Path(__file__).parent
CSV = HERE / "predictions.csv"
EXPORT = HERE / "ui_export.csv"

OUTCOME = {"actual", "outcome", "resolved_at"}
CLOSE = {"close_odds", "close_opp_odds", "market_p_close", "edge_close", "closed_at"}
AMENDABLE = {"line", "my_p", "why", "odds", "opp_odds", "market_p", "edge", "amendments"}


def read(path: pathlib.Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        header = rows and list(rows[0].keys())
    if rows and header != FIELDS:
        missing = [c for c in FIELDS if c not in header]
        extra = [c for c in header if c not in FIELDS]
        raise SystemExit(f"{path.name}: columns don't match SCHEMA.md "
                         f"(missing {missing}, unexpected {extra})")
    return rows


def same(a: str, b: str) -> bool:
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except ValueError:
        return False


def n_amend(v: str) -> int:
    try:
        return len(json.loads(v)) if v else 0
    except json.JSONDecodeError:
        return 0


def when(s: str):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def amended_before_kickoff(row: dict) -> bool:
    """The newest amendment must be dated before this row's kickoff."""
    try:
        last = json.loads(row["amendments"])[-1]["at"]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        return False
    at, ko = when(last), when(row["kickoff"])
    return bool(at and ko and at < ko)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export", nargs="?", default=str(EXPORT))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    src = pathlib.Path(a.export)
    if not src.exists():
        raise SystemExit(f"no {src.name} — paste the UI's 'Copy all as CSV' into it first")
    new = read(src)
    if not new:
        raise SystemExit(f"{src.name} is empty")
    old = read(CSV) if CSV.exists() else []

    by_id = {r["id"]: r for r in old}
    ids = [r["id"] for r in new]
    if len(set(ids)) != len(ids):
        raise SystemExit("export has duplicate ids — re-copy it from the UI")

    problems, changes, added = [], [], []
    for n in new:
        o = by_id.get(n["id"])
        if o is None:
            added.append(n)
            continue
        grew = n_amend(n["amendments"]) > n_amend(o["amendments"])
        if grew and not amended_before_kickoff(n):
            problems.append(f"  {o['player']:22s} amendment dated after kickoff")
            continue
        for c in FIELDS:
            if same(o[c], n[c]):
                continue
            if c in OUTCOME:
                if o["outcome"] == "":          # not resolved yet: UI may set e.g. "skip"
                    o[c] = n[c]
                continue                        # resolved: predictions.csv wins
            if c in CLOSE and n[c] == "":
                continue                        # never erase a recorded close
            if c in CLOSE or (c in AMENDABLE and grew):
                changes.append(f"  {o['player']:22s} {c}: {o[c]!r} -> {n[c]!r}")
                o[c] = n[c]
                continue
            problems.append(f"  {o['player']:22s} {c}: {o[c]!r} (committed) vs {n[c]!r} (export)")

    if problems:
        print("REFUSED — the export changes committed values it is not allowed to:")
        print("\n".join(problems))
        print("\nnothing written. predictions.csv is untouched.")
        sys.exit(1)

    kept = [r for r in old if r["id"] not in set(ids)]
    added.sort(key=lambda r: r["logged_at"])

    # a queued prop should appear once; two rows for it means a double log
    seen, dupes = {}, []
    for r in old + added:
        k = (r["week"], r["game"], r["player"], r["market"])
        if k in seen:
            dupes.append(f"  week {k[0]} {k[1]} {k[2]} {k[3]}  (ids {seen[k]}, {r['id']})")
        seen[k] = r["id"]

    print(f"{len(added)} new rows, {len(changes)} field updates on existing rows, "
          f"{len(old)} rows already in predictions.csv")
    if changes:
        print("\n".join(changes))
    if kept:
        print(f"note: {len(kept)} rows in predictions.csv are not in the export "
              f"(CLI-logged or deleted in the UI) — kept as they are")
    if dupes:
        print("WARNING: the same prop appears more than once:\n" + "\n".join(dupes))

    if a.dry_run:
        print("(dry run, nothing written)")
        return
    with open(CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(old + added)
    print(f"wrote {CSV.name}: {len(old) + len(added)} rows. "
          f"Commit it: git add predictions.csv && git commit && git push")


if __name__ == "__main__":
    main()
