"""
Watchlist tracking — the fix for the scanner's biggest blind spot.

scan.py queries GitHub with sort=stars, which returns the BIGGEST repos per
topic, not the fastest-growing. A repo going 20 -> 800 stars never appears
unless 800 already ranks top-40 for its topic. For a trend scanner that is
backwards: the interesting thing is momentum, and momentum starts small.

This module fixes it. Once a repo has been seen in any snapshot it joins the
watchlist, and every run queries it directly by name (/repos/{owner}/{name} —
one cheap call, exact numbers, no ranking involved). Star history is then
complete for every repo ever seen, regardless of whether it charts.

The top-40 sweep in scan.py still matters — it is how genuinely new things
enter the watchlist in the first place. The two are complementary.

    python watchlist.py [github_token]      # refresh counts for all watched repos
"""
import io, json, os, sys, time, urllib.request
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                              line_buffering=True)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCH = os.path.join(HERE, "watchlist.json")
TOKEN = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GITHUB_TOKEN", "")


def _hdr():
    h = {"Accept": "application/vnd.github+json", "User-Agent": "geo-trendscan/2.0"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def load_watchlist():
    if os.path.exists(WATCH):
        return json.load(open(WATCH, encoding="utf-8"))
    return {"repos": {}, "updated": None}


def seed_from_snapshots():
    """Add every repo ever seen in a snapshot to the watchlist."""
    import glob
    wl = load_watchlist()
    added = 0
    for f in sorted(glob.glob(os.path.join(HERE, "snapshots", "*.json*"))):
        try:
            if f.endswith(".gz"):
                import gzip
                d = json.load(gzip.open(f, "rt", encoding="utf-8"))
            else:
                d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for name in d.get("github", {}):
            if name not in wl["repos"]:
                wl["repos"][name] = {"history": {}}
                added += 1
    print(f"watchlist: {len(wl['repos'])} repos ({added} newly seeded)")
    return wl


def refresh(wl, limit=None):
    """
    Query each watched repo directly. Rate limits: authenticated core API is
    5000/hour, so a few thousand repos is fine; unauthenticated is 60/hour and
    will not get far — pass a token.
    """
    today = f"{datetime.now(timezone.utc):%Y-%m-%d}"
    names = list(wl["repos"])
    if limit:
        names = names[:limit]
    if not TOKEN:
        print("  ! no GITHUB_TOKEN — unauthenticated limit is 60/hour, "
              "refreshing first 50 only")
        names = names[:50]

    ok = gone = err = 0
    for i, name in enumerate(names, 1):
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{name}",
                                         headers=_hdr())
            r = json.load(urllib.request.urlopen(req, timeout=25))
            wl["repos"][name]["history"][today] = {
                "stars": r.get("stargazers_count", 0),
                "forks": r.get("forks_count", 0),
                "issues": r.get("open_issues_count", 0),
            }
            wl["repos"][name]["lang"] = r.get("language") or "-"
            wl["repos"][name]["desc"] = (r.get("description") or "")[:100]
            ok += 1
        except urllib.error.HTTPError as e:
            if e.code == 404:            # repo deleted or renamed
                wl["repos"][name]["gone"] = today
                gone += 1
            else:
                err += 1
                if e.code in (403, 429):
                    print(f"  ! rate limited at {i}/{len(names)} — stopping early")
                    break
        except Exception:
            err += 1
        if i % 200 == 0:
            print(f"  {i}/{len(names)} refreshed")
        time.sleep(0.05)

    wl["updated"] = today
    print(f"  refreshed {ok}, gone {gone}, errors {err}")
    return wl


def report(wl, top=25):
    """Rank by star gain since the previous observation."""
    rows = []
    for name, v in wl["repos"].items():
        h = v.get("history", {})
        if len(h) < 2:
            continue
        dates = sorted(h)
        prev, cur = h[dates[-2]]["stars"], h[dates[-1]]["stars"]
        days = (datetime.strptime(dates[-1], "%Y-%m-%d")
                - datetime.strptime(dates[-2], "%Y-%m-%d")).days or 1
        gain = cur - prev
        if gain <= 0:
            continue
        rows.append((gain, gain / days, (gain / prev * 100) if prev else 0,
                     cur, name, v))

    if not rows:
        print("\nNo deltas yet — this is the first refresh.")
        print("Star history needs two observations before momentum exists.")
        print("Run again after the next scan.")
        return

    print(f"\n{'gain':>7} {'/day':>7} {'%':>7} {'now':>8}  repo")
    print("-" * 74)
    for gain, per_day, pct, cur, name, v in sorted(rows, reverse=True)[:top]:
        print(f"{gain:>+7} {per_day:>7.1f} {pct:>6.1f}% {cur:>8,}  "
              f"{name[:30]:32}{v.get('desc','')[:22]}")


def main():
    wl = seed_from_snapshots()
    wl = refresh(wl)
    with open(WATCH, "w", encoding="utf-8") as f:
        json.dump(wl, f, ensure_ascii=False, indent=1)
    print(f"saved -> {WATCH}")
    report(wl)


if __name__ == "__main__":
    main()
