"""
Compare two snapshots — this is where the value is.

A single scan tells you what is *big*. Big is mostly a function of age: an
established library outranks everything new simply by having existed longer.
Comparing snapshots tells you what is *rising*, which is the only part that
predicts anything.

    python compare.py                    # oldest vs newest
    python compare.py 2026-09-01 2026-10-01
"""
import glob, gzip, io, json, os, sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                              errors='replace', line_buffering=True)


def _open(path):
    """Snapshots are gzipped once committed; plain JSON before that."""
    if path.endswith('.gz'):
        return gzip.open(path, 'rt', encoding='utf-8')
    return open(path, encoding='utf-8')


def _tag(path):
    return os.path.basename(path).replace('.json.gz', '').replace('.json', '')

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(HERE, "snapshots")


def load(tag=None):
    files = sorted(glob.glob(os.path.join(SNAP, "*.json*")))
    if not files:
        sys.exit("No snapshots yet — run scan.py first.")
    if tag:
        m = [f for f in files if tag in os.path.basename(f)]
        if not m:
            sys.exit(f"No snapshot matching {tag}. Have: "
                     + ", ".join(os.path.basename(f)[:-5] for f in files))
        f = m[0]
    else:
        f = files[0]
    return _tag(f), json.load(_open(f))


def growth(old, new, key, label, name_fmt=lambda k, v: k, top=20, min_new=50):
    """Rank by absolute gain, but report percentage too — a big library adding
    5% dwarfs a small one doubling, and both facts matter."""
    o, n = old.get(key, {}), new.get(key, {})
    if not isinstance(o, dict) or not isinstance(n, dict):
        return
    rows = []
    for k, v in n.items():
        cur = v if isinstance(v, (int, float)) else v.get("stars", v.get("score", 0))
        if cur < min_new:
            continue
        if k in o:
            ov = o[k]
            prev = ov if isinstance(ov, (int, float)) else ov.get("stars", ov.get("score", 0))
            rows.append((cur - prev, (cur - prev) / prev * 100 if prev else 0, cur, k, v, False))
        else:
            rows.append((cur, float("inf"), cur, k, v, True))
    if not rows:
        return
    print(f"\n--- {label} ---")
    for gain, pct, cur, k, v, is_new in sorted(rows, key=lambda r: -r[0])[:top]:
        tag = "NEW" if is_new else f"+{pct:.0f}%"
        print(f"  {gain:>+8,}  {tag:>7}  now {cur:>9,}  {name_fmt(k, v)[:52]}")


def main():
    a = sys.argv[1] if len(sys.argv) > 1 else None
    b = sys.argv[2] if len(sys.argv) > 2 else None
    files = sorted(glob.glob(os.path.join(SNAP, "*.json*")))
    if len(files) < 2 and not (a and b):
        d, _ = load()
        sys.exit(f"Only one snapshot ({d}). Run scan.py again in a week —\n"
                 "the comparison is the point; a single run cannot show a trend.")

    old_tag, old = load(a)
    new_tag, new = load(b or _tag(files[-1]))
    if old_tag == new_tag:
        old_tag, old = load(_tag(files[0]))

    days = (datetime.strptime(new_tag, "%Y-%m-%d") - datetime.strptime(old_tag, "%Y-%m-%d")).days
    print("=" * 74)
    print(f"TREND DELTA   {old_tag}  ->  {new_tag}   ({days} days)".center(74))
    print("=" * 74)

    growth(old, new, "github", "REPOS BY STAR GAIN",
           lambda k, v: f"{k}  [{v.get('lang','-')}]", min_new=30)
    growth(old, new, "npm", "NPM MONTHLY DOWNLOADS", min_new=1000)
    growth(old, new, "pypi", "PYPI MONTHLY DOWNLOADS", min_new=1000)
    growth(old, new, "crates", "CRATES.IO DOWNLOADS", min_new=1000)

    # topic-level momentum: total stars per topic, then delta
    def topic_stars(snap):
        agg = {}
        for v in snap.get("github", {}).values():
            for t in v.get("topics", []):
                agg[t] = agg.get(t, 0) + v.get("stars", 0)
        return agg

    to, tn = topic_stars(old), topic_stars(new)
    rows = [(tn[t] - to.get(t, 0), t, tn[t]) for t in tn]
    print("\n--- TOPIC MOMENTUM (total stars across repos in topic) ---")
    for gain, t, cur in sorted(rows, reverse=True)[:15]:
        print(f"  {gain:>+8,}  now {cur:>9,}  {t}")

    print("\nNote: different sources use different currencies. Compare within a\n"
          "column, never across them.\n")


if __name__ == "__main__":
    main()
