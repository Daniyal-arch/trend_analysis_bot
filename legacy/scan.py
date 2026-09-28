"""
Geospatial trend scanner v2.

Answers three questions:
  1. What is being built and adopted?      github repos, package downloads
  2. What is getting attention?            hackernews, reddit, bluesky
  3. What is broken / unsolved?            github issues, stackexchange, reddit help posts

Sources run in parallel threads; each is independently rate-limited and any one
failing does not stop the others.

LinkedIn is excluded: its User Agreement prohibits automated collection and
enforcement is account termination.

Access notes discovered by testing (2026-09-01):
  reddit   /api/*.json returns 403 from datacenter IPs regardless of User-Agent.
           The public .rss feeds still work and are used here. They carry titles
           but NO score, so reddit contributes topic/problem signal, not ranking.
           Set REDDIT_CLIENT_ID/SECRET to switch to the full OAuth API.
  bluesky  searchPosts requires auth (403). searchActors + getAuthorFeed are
           public, so we discover geo accounts then read their feeds.

Usage:  python scan.py [github_token]
"""
import io, json, os, re, sys, time, urllib.parse, urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                              line_buffering=True)

TOKEN = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GITHUB_TOKEN", "")
UA = {"User-Agent": "geo-trendscan/2.0 (research; contact via github)"}
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(m):
    print(m, flush=True)


def fetch(url, hdrs=None, tries=3, raw=False, quiet=False):
    h = dict(UA); h.update(hdrs or {})
    for i in range(tries):
        try:
            body = urllib.request.urlopen(
                urllib.request.Request(url, headers=h), timeout=35).read()
            return body.decode("utf-8", "replace") if raw else json.loads(body)
        except Exception as e:
            if i == tries - 1:
                if not quiet:
                    log(f"    ! {type(e).__name__} {str(e)[:44]} {url[:52]}")
                return None
            time.sleep(2 * (i + 1))


# ═══════════════════════════════════════════════════════════ GitHub repos
GH_TOPICS = [
    # core
    "geospatial", "gis", "cartography", "openstreetmap", "geodata", "mapping",
    "spatial-analysis", "webgis", "qgis", "arcgis",
    # data / formats / engines
    "postgis", "geoparquet", "stac", "cloud-optimized-geotiff", "zarr", "duckdb",
    "vector-tiles", "pmtiles", "geojson", "spatial-database",
    # rendering / web
    "maplibre", "deckgl", "cesium", "leaflet", "openlayers", "3d-tiles", "webgl",
    # 3d / reconstruction
    "gaussian-splatting", "nerf", "photogrammetry", "pointcloud", "lidar", "sfm",
    # earth observation
    "remote-sensing", "satellite-imagery", "earth-observation", "sentinel",
    "synthetic-aperture-radar", "hyperspectral", "change-detection",
    # ai
    "geoai", "foundation-model", "semantic-segmentation",
    # domains
    "digital-twin", "bim", "urban-planning", "smart-city", "cadastre", "surveying",
    "gnss", "hydrology", "flood", "forestry", "precision-agriculture", "climate",
    "routing", "isochrone", "geocoding", "navigation", "telecommunications",
    "mining", "bathymetry", "oceanography", "drone", "uav", "indoor-positioning",
    "spatial-statistics", "h3", "trajectory", "geofencing",
]


def scan_github():
    out, hdr = {}, {"Accept": "application/vnd.github+json"}
    if TOKEN:
        hdr["Authorization"] = f"Bearer {TOKEN}"
    for i, t in enumerate(GH_TOPICS, 1):
        d = fetch("https://api.github.com/search/repositories"
                  f"?q=topic:{t}+created:>2024-01-01&sort=stars&order=desc&per_page=40", hdr)
        for r in (d or {}).get("items", []):
            k = r["full_name"]
            if k not in out:
                out[k] = {"stars": r["stargazers_count"], "lang": r.get("language") or "-",
                          "desc": (r["description"] or "").replace("\n", " ")[:100],
                          "created": r["created_at"][:7], "pushed": r["pushed_at"][:7],
                          "forks": r["forks_count"], "issues": r.get("open_issues_count", 0),
                          "topics": []}
            out[k]["topics"].append(t)
        if i % 10 == 0:
            log(f"    github {i}/{len(GH_TOPICS)} topics, {len(out)} repos")
        time.sleep(2.1)          # search API: 30 req/min
    log(f"  github: {len(out)} repos")
    return out


# ═════════════════════════════════════════════════════ GitHub issues (PROBLEMS)
ISSUE_Q = [
    "cog+cloud+optimized+geotiff", "stac+catalog", "geoparquet", "pmtiles",
    "maplibre+performance", "deck.gl+performance", "postgis+slow", "gdal+error",
    "projection+wrong", "coordinate+reference+system", "tile+server",
    "raster+memory", "geopandas+slow", "duckdb+spatial", "gaussian+splatting",
    "point+cloud+large", "lidar+processing", "geocoding+accuracy",
]


def scan_issues():
    """Open, recently-active issues in geo repos = what is actually broken."""
    out, hdr = {}, {"Accept": "application/vnd.github+json"}
    if TOKEN:
        hdr["Authorization"] = f"Bearer {TOKEN}"
    for q in ISSUE_Q:
        d = fetch("https://api.github.com/search/issues"
                  f"?q={q}+is:issue+is:open+created:>2025-01-01&sort=reactions"
                  f"&order=desc&per_page=25", hdr)
        for it in (d or {}).get("items", []):
            repo = re.sub(r".*/repos/", "", it.get("repository_url", ""))
            out[it["html_url"]] = {
                "title": (it.get("title") or "")[:110], "repo": repo,
                "reactions": (it.get("reactions") or {}).get("total_count", 0),
                "comments": it.get("comments", 0), "query": q,
                "labels": [l["name"] for l in it.get("labels", [])][:4]}
        time.sleep(2.1)
    log(f"  github issues: {len(out)} open issues")
    return out


# ═══════════════════════════════════════════════════════════ Hacker News
HN_TERMS = [
    "geospatial", "OpenStreetMap", "cartography", "QGIS", "satellite imagery",
    "remote sensing", "lidar", "digital twin", "earth observation", "PostGIS",
    "GeoJSON", "overture maps", "sentinel-2", "map projection", "vector tiles",
    "gaussian splatting", "geocoding", "point cloud", "elevation model", "mapping",
    "photogrammetry", "GPS", "navigation", "routing algorithm", "isochrone",
    "drone mapping", "aerial imagery", "map tiles", "spatial database", "GDAL",
    "openlayers", "maplibre", "cesium", "3d city", "terrain rendering",
    "flood model", "wildfire map", "census data", "urban planning", "surveying",
]
GEO_RX = re.compile(
    r"\bmap|geo|gis\b|satellit|spatial|cartog|osm\b|openstreetmap|lidar|terrain|"
    r"elevation|globe|atlas|navigat|survey|remote sens|earth|orbit|drone|aerial|"
    r"point cloud|3d tile|projection|coordinat|\bgps\b|planet|imagery|census|"
    r"street|city|urban|land\b|route|routing|isochrone|flood|wildfire|climate", re.I)


def scan_hn(since=1704067200):
    out = {}
    for t in HN_TERMS:
        d = fetch(f"https://hn.algolia.com/api/v1/search?query={urllib.parse.quote(t)}"
                  f"&tags=story&numericFilters=created_at_i>{since},points>15&hitsPerPage=100")
        for h in (d or {}).get("hits", []):
            title = h.get("title") or ""
            if GEO_RX.search(title):
                out[h["objectID"]] = {"title": title, "score": h.get("points") or 0,
                                      "comments": h.get("num_comments") or 0,
                                      "url": h.get("url") or "", "term": t,
                                      "date": (h.get("created_at") or "")[:7]}
        time.sleep(0.25)
    log(f"  hackernews: {len(out)} geo stories")
    return out


# ═══════════════════════════════════════════════════════════════ Reddit
SUBS = ["gis", "geospatial", "remotesensing", "QGIS", "gischat", "cartography",
        "MapPorn", "openstreetmap", "geomatics", "spatialanalysis", "geography",
        "surveying", "photogrammetry", "UAVmapping", "esri", "PostGIS",
        "dataisbeautiful", "urbanplanning", "geologycareers", "askGIS"]

HELP_RX = re.compile(r"\bhow (do|to|can|would)|help|problem|issue|error|stuck|"
                     r"struggl|fail|can'?t |unable|why (does|is|do)|best way|"
                     r"advice|recommend|alternative|instead of|\?$", re.I)


def scan_reddit():
    """
    Reddit blocks /*.json from datacenter IPs (403, any UA). The public .rss
    feeds still serve, so we read those. They carry title + link but no score,
    so reddit gives topic and problem signal rather than ranking.
    """
    out = {}
    for s in SUBS:
        for feed, tag in (("top/.rss?t=year", "top-year"),
                          ("top/.rss?t=month", "top-month"),
                          ("new/.rss", "new")):
            xml = fetch(f"https://www.reddit.com/r/{s}/{feed}", raw=True, quiet=True)
            if not xml:
                continue
            for m in re.finditer(r"<entry>(.*?)</entry>", xml, re.S):
                blk = m.group(1)
                tm = re.search(r"<title>(.*?)</title>", blk, re.S)
                lm = re.search(r'<link href="(.*?)"', blk)
                if not tm:
                    continue
                title = re.sub(r"&amp;", "&", " ".join(tm.group(1).split()))
                key = lm.group(1) if lm else title
                out[key] = {"title": title[:130], "sub": s, "feed": tag,
                            "is_help": bool(HELP_RX.search(title))}
            time.sleep(1.5)
    log(f"  reddit: {len(out)} posts (rss; titles only, no scores)")
    return out


# ══════════════════════════════════════════════════════════════ Bluesky
BSKY_SEEDS = ["geospatial", "GIS", "cartography", "OpenStreetMap", "remote sensing",
              "geography", "mapping", "earth observation"]


def scan_bluesky():
    """searchPosts needs auth; searchActors + getAuthorFeed are public."""
    actors, out = {}, {}
    for q in BSKY_SEEDS:
        d = fetch("https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors"
                  f"?q={urllib.parse.quote(q)}&limit=25", quiet=True)
        for a in (d or {}).get("actors", []):
            actors[a["handle"]] = a.get("displayName") or a["handle"]
        time.sleep(0.4)
    log(f"    bluesky: {len(actors)} geo accounts found, reading feeds")
    for h in list(actors)[:70]:
        d = fetch("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
                  f"?actor={urllib.parse.quote(h)}&limit=40", quiet=True)
        for p in (d or {}).get("feed", []):
            post = p.get("post", {})
            rec = post.get("record", {})
            uri = post.get("uri")
            if uri and rec.get("text"):
                out[uri] = {"text": rec["text"].replace("\n", " ")[:150],
                            "likes": post.get("likeCount", 0),
                            "reposts": post.get("repostCount", 0),
                            "replies": post.get("replyCount", 0),
                            "handle": h, "date": (rec.get("createdAt") or "")[:7]}
        time.sleep(0.35)
    log(f"  bluesky: {len(out)} posts from {len(actors)} accounts")
    return out


# ═══════════════════════════════════════════════════════ GIS StackExchange
def scan_se():
    out, tags, unans = {}, Counter(), {}
    for sort, extra in (("votes", ""), ("activity", ""), ("hot", "")):
        d = fetch(f"https://api.stackexchange.com/2.3/questions?site=gis&sort={sort}"
                  f"&order=desc&pagesize=100&fromdate=1735689600{extra}", quiet=True)
        for q in (d or {}).get("items", []):
            out[q["question_id"]] = {"title": q.get("title", "")[:120],
                                     "score": q.get("score", 0),
                                     "views": q.get("view_count", 0),
                                     "answered": q.get("is_answered", False),
                                     "tags": q.get("tags", [])}
            for t in q.get("tags", []):
                tags[t] += 1
        time.sleep(1.2)
    # unanswered = unmet need
    d = fetch("https://api.stackexchange.com/2.3/questions/unanswered?site=gis"
              "&sort=votes&order=desc&pagesize=100&fromdate=1735689600", quiet=True)
    for q in (d or {}).get("items", []):
        unans[q["question_id"]] = {"title": q.get("title", "")[:120],
                                   "views": q.get("view_count", 0),
                                   "tags": q.get("tags", [])}
    log(f"  stackexchange: {len(out)} questions, {len(unans)} unanswered, {len(tags)} tags")
    return out, dict(tags), unans


# ════════════════════════════════════════════════════════════════ arXiv
ARXIV_Q = ["remote sensing foundation model", "geospatial machine learning",
           "satellite image segmentation", "gaussian splatting aerial",
           "earth observation transformer", "geospatial large language model",
           "urban digital twin", "point cloud semantic segmentation",
           "SAR image analysis", "land cover classification deep learning"]


def scan_arxiv():
    out = []
    for q in ARXIV_Q:
        xml = fetch("http://export.arxiv.org/api/query?search_query=all:"
                    + urllib.parse.quote(f'"{q}"')
                    + "&sortBy=submittedDate&sortOrder=descending&max_results=50",
                    raw=True, quiet=True)
        if xml:
            for m in re.finditer(r"<title>(.*?)</title>.*?<published>(\d{4}-\d{2})", xml, re.S):
                t = " ".join(m.group(1).split())
                if t.lower() != "arxiv query":
                    out.append({"title": t[:140], "date": m.group(2), "query": q})
        time.sleep(3.2)
    log(f"  arxiv: {len(out)} papers")
    return out


# ═════════════════════════════════════════════════════════════ packages
NPM = ["maplibre-gl", "deck.gl", "@turf/turf", "leaflet", "mapbox-gl", "ol",
       "cesium", "h3-js", "geotiff", "pmtiles", "supercluster", "proj4",
       "@loaders.gl/core", "geojson-vt", "flatgeobuf", "topojson"]
PYPI = ["geopandas", "rasterio", "shapely", "pyproj", "xarray", "rioxarray",
        "earthengine-api", "pystac-client", "stackstac", "folium", "leafmap",
        "duckdb", "obstore", "lonboard", "geoarrow-pyarrow", "odc-stac",
        "planetary-computer", "titiler", "morecantile", "h3", "osmnx", "pdal"]
CRATES = ["geo", "gdal", "geojson", "proj", "martin", "geozero", "h3o", "geoarrow",
          "tiff", "las"]


def scan_packages():
    npm, pypi, crates = {}, {}, {}
    for p in NPM:
        d = fetch(f"https://api.npmjs.org/downloads/point/last-month/{urllib.parse.quote(p)}",
                  quiet=True)
        if d and d.get("downloads"):
            npm[p] = d["downloads"]
        time.sleep(0.3)
    for p in PYPI:
        d = fetch(f"https://pypistats.org/api/packages/{p}/recent", quiet=True)
        if d and d.get("data"):
            pypi[p] = d["data"].get("last_month", 0)
        time.sleep(2.0)          # pypistats 429s easily
    for p in CRATES:
        d = fetch(f"https://crates.io/api/v1/crates/{p}", quiet=True)
        if d and d.get("crate"):
            crates[p] = d["crate"].get("downloads", 0)
        time.sleep(1.0)
    log(f"  packages: npm {len(npm)}/{len(NPM)}, pypi {len(pypi)}/{len(PYPI)}, "
        f"crates {len(crates)}/{len(CRATES)}")
    return npm, pypi, crates


# ═══════════════════════════════════════════════════════════════ driver
def main():
    t0 = time.time()
    log("=" * 78)
    log("GEOSPATIAL TREND SCAN v2".center(78))
    log(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}".center(78))
    log("=" * 78)
    log("\nrunning 8 collectors in parallel...\n")

    jobs = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        jobs["github"] = ex.submit(scan_github)
        jobs["issues"] = ex.submit(scan_issues)
        jobs["hn"] = ex.submit(scan_hn)
        jobs["reddit"] = ex.submit(scan_reddit)
        jobs["bluesky"] = ex.submit(scan_bluesky)
        jobs["se"] = ex.submit(scan_se)
        jobs["arxiv"] = ex.submit(scan_arxiv)
        jobs["pkg"] = ex.submit(scan_packages)
        res = {k: f.result() for k, f in jobs.items()}

    data = {"github": res["github"], "issues": res["issues"], "hn": res["hn"],
            "reddit": res["reddit"], "bluesky": res["bluesky"],
            "arxiv": res["arxiv"]}
    data["se"], data["se_tags"], data["se_unanswered"] = res["se"]
    data["npm"], data["pypi"], data["crates"] = res["pkg"]

    os.makedirs(os.path.join(HERE, "snapshots"), exist_ok=True)
    path = os.path.join(HERE, "snapshots", f"{datetime.now(timezone.utc):%Y-%m-%d}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=list)

    total = sum(len(v) for v in data.values() if hasattr(v, "__len__"))
    log("\n" + "=" * 78)
    log(f"TOTAL {total} records in {time.time()-t0:.0f}s  ->  {path}")
    log("=" * 78)
    report(data)


def report(d):
    gh, iss, hn, rd, bs = d["github"], d["issues"], d["hn"], d["reddit"], d["bluesky"]

    log("\n" + "─" * 78)
    log("WHAT IS BEING BUILT — top repos created 2024+")
    log("─" * 78)
    for n, v in sorted(gh.items(), key=lambda x: -x[1]["stars"])[:30]:
        log(f"  {v['stars']:>6}*  {v['lang'][:10]:12}{n[:33]:35}{v['desc'][:36]}")

    log("\n" + "─" * 78)
    log("HOTTEST TOPICS — median stars, topics with >=5 repos")
    log("─" * 78)
    bt = {}
    for v in gh.values():
        for t in v["topics"]:
            bt.setdefault(t, []).append(v["stars"])
    rank = [(sorted(s)[len(s)//2], len(s), t) for t, s in bt.items() if len(s) >= 5]
    for med, n, t in sorted(rank, reverse=True)[:26]:
        log(f"  {med:>6} median  ({n:>3} repos)  {t}")

    log("\n" + "─" * 78)
    log("WHAT IS BROKEN — most-reacted open GitHub issues")
    log("─" * 78)
    for v in sorted(iss.values(), key=lambda x: -x["reactions"])[:22]:
        lab = ",".join(v["labels"][:2])
        log(f"  {v['reactions']:>3}R {v['comments']:>3}c  {v['repo'][:26]:28}{v['title'][:44]}")

    log("\n" + "─" * 78)
    log("UNANSWERED ON GIS STACKEXCHANGE — unmet need")
    log("─" * 78)
    for v in sorted(d["se_unanswered"].values(), key=lambda x: -x["views"])[:18]:
        log(f"  {v['views']:>6} views  {v['title'][:60]}")

    log("\n" + "─" * 78)
    log("WHAT PEOPLE ASK ON REDDIT — help/problem posts")
    log("─" * 78)
    helps = [v for v in rd.values() if v["is_help"]]
    log(f"  {len(helps)} of {len(rd)} posts are questions/problems\n")
    for v in helps[:24]:
        log(f"  r/{v['sub'][:16]:18}{v['title'][:54]}")

    log("\n" + "─" * 78)
    log("REDDIT TOPIC FREQUENCY — what the community talks about")
    log("─" * 78)
    stop = set("the a an of for and with to in on is are my me you your this that "
               "what how why when where can do does did any some best help need "
               "using use used from have has get got new all not but its it's "
               "anyone someone please thanks would could should about into more "
               "just like make made want looking free open data map maps gis".split())
    w = Counter()
    for v in rd.values():
        for word in re.findall(r"[a-zA-Z][a-zA-Z\-]{3,}", v["title"].lower()):
            if word not in stop:
                w[word] += 1
    log("  " + ", ".join(f"{x}({c})" for x, c in w.most_common(30)))

    log("\n" + "─" * 78)
    log("TOP ON HACKER NEWS")
    log("─" * 78)
    for v in sorted(hn.values(), key=lambda x: -x["score"])[:20]:
        log(f"  {v['score']:>5}pts {v['comments']:>4}c  {v['title'][:60]}")

    log("\n" + "─" * 78)
    log("TOP ON BLUESKY")
    log("─" * 78)
    for v in sorted(bs.values(), key=lambda x: -x["likes"])[:18]:
        log(f"  {v['likes']:>4}L {v['reposts']:>3}rt  @{v['handle'][:20]:22}{v['text'][:40]}")

    log("\n" + "─" * 78)
    log("PACKAGE DOWNLOADS")
    log("─" * 78)
    for name, dd, unit in (("npm/month", d["npm"], ""), ("pypi/month", d["pypi"], ""),
                           ("crates all-time", d["crates"], "")):
        log(f"  {name}:")
        for p, n in sorted(dd.items(), key=lambda x: -x[1])[:14]:
            log(f"    {n:>13,}  {p}")

    log("\n" + "─" * 78)
    log("STACKEXCHANGE TAGS")
    log("─" * 78)
    log("  " + ", ".join(f"{t}({c})" for t, c in
                         sorted(d["se_tags"].items(), key=lambda x: -x[1])[:30]))

    log("\n" + "─" * 78)
    log("RESEARCH DIRECTION — arXiv term frequency")
    log("─" * 78)
    stop2 = set("the a an of for and with to in on using from via based image images "
                "data model models learning network networks new towards deep high "
                "remote sensing satellite geospatial earth observation".split())
    w2 = Counter()
    for p in d["arxiv"]:
        for word in re.findall(r"[a-z]{4,}", p["title"].lower()):
            if word not in stop2:
                w2[word] += 1
    log("  " + ", ".join(f"{x}({c})" for x, c in w2.most_common(26)))
    log("")


if __name__ == "__main__":
    main()
