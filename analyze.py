"""
Analyse a snapshot with a geospatial relevance filter.

The scan casts a deliberately wide net so nothing is missed. That means some
topics ("duckdb", "webgl", "navigation", "climate", "foundation-model") drag in
repos with no geospatial content at all — a robot vacuum, a crypto miner, an AI
proxy. Ranking on the raw set overstates those topics and produces a top-20 that
is half noise.

This script keeps a repo only if it is geospatial on the evidence:
  * matched a CORE geo topic (geospatial, gis, openstreetmap, stac, ...), or
  * its name/description contains geo vocabulary.

An AMBIGUOUS topic alone ("duckdb") is not enough.

    python analyze.py [snapshot-date]
"""
import glob, gzip, io, json, os, re, sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                              errors='replace', line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))

CORE = {
    "geospatial", "gis", "cartography", "openstreetmap", "geodata", "mapping",
    "spatial-analysis", "webgis", "qgis", "arcgis", "postgis", "geoparquet",
    "stac", "cloud-optimized-geotiff", "vector-tiles", "pmtiles", "geojson",
    "maplibre", "cesium", "leaflet", "openlayers", "3d-tiles", "remote-sensing",
    "satellite-imagery", "earth-observation", "sentinel", "synthetic-aperture-radar",
    "hyperspectral", "geoai", "cadastre", "surveying", "gnss", "hydrology",
    "geocoding", "bathymetry", "oceanography", "isochrone", "h3", "geofencing",
    "photogrammetry", "lidar",
}

GEO_TEXT = re.compile(
    r"geospatial|geo-?spatial|\bgis\b|cartograph|openstreetmap|\bosm\b|map(s|ping|box|libre|tile)?\b|"
    r"satellit|remote sens|earth observation|sentinel-?[12]|landsat|raster|vector tile|"
    r"shapefile|geotiff|\bcog\b|geojson|postgis|\bqgis\b|arcgis|\bstac\b|lidar|point ?cloud|"
    r"photogramm|terrain|elevation|\bdem\b|bathymet|geocod|routing|isochrone|"
    r"coordinate|projection|latitude|longitude|\bgnss\b|\bgps\b|cadastr|survey|"
    r"land ?cover|land ?use|urban|city|cities|geographic|geography|globe|atlas|"
    r"aerial|drone|\buav\b|orbit|planetary|topograph|hydrolog|watershed|"
    r"spatial (data|analys|index|quer)|3d tiles|cesium|deck\.gl|h3\b", re.I)


def is_geo(name, desc, topics):
    if CORE & set(topics):
        return True
    return bool(GEO_TEXT.search(f"{name} {desc}"))


def load(tag=None):
    """Snapshots are gzipped once committed by CI; plain JSON when run locally."""
    files = sorted(glob.glob(os.path.join(HERE, "snapshots", "*.json*")))
    if not files:
        sys.exit("no snapshots — run scan.py first")
    f = next((x for x in files if tag and tag in x), files[-1])
    op = gzip.open(f, "rt", encoding="utf-8") if f.endswith(".gz") else open(f, encoding="utf-8")
    name = os.path.basename(f).replace(".json.gz", "").replace(".json", "")
    return name, json.load(op)


def main():
    tag, d = load(sys.argv[1] if len(sys.argv) > 1 else None)
    gh_all = d.get("github", {})
    gh = {n: v for n, v in gh_all.items()
          if is_geo(n, v.get("desc", ""), v.get("topics", []))}
    dropped = len(gh_all) - len(gh)

    print("=" * 78)
    print(f"GEOSPATIAL TREND ANALYSIS — snapshot {tag}".center(78))
    print("=" * 78)
    print(f"\nrepos: {len(gh_all)} collected -> {len(gh)} geospatial "
          f"({dropped} non-geo dropped, {dropped*100//max(len(gh_all),1)}%)")

    print("\n" + "─" * 78)
    print("TOP GEOSPATIAL REPOS (created 2024+, filtered)")
    print("─" * 78)
    for n, v in sorted(gh.items(), key=lambda x: -x[1]["stars"])[:30]:
        print(f"  {v['stars']:>6}*  {v['lang'][:10]:12}{n[:34]:36}{v['desc'][:34]}")

    print("\n" + "─" * 78)
    print("HOT TOPICS — median stars among GEOSPATIAL repos only (>=5 repos)")
    print("─" * 78)
    bt = {}
    for v in gh.values():
        for t in v.get("topics", []):
            bt.setdefault(t, []).append(v["stars"])
    rank = [(sorted(s)[len(s)//2], len(s), t) for t, s in bt.items() if len(s) >= 5]
    for med, n, t in sorted(rank, reverse=True)[:24]:
        mark = "" if t in CORE else "  (ambiguous topic)"
        print(f"  {med:>6} median  ({n:>3} repos)  {t}{mark}")

    print("\n" + "─" * 78)
    print("LANGUAGE MIX — geospatial repos, stars-weighted")
    print("─" * 78)
    lang = Counter()
    for v in gh.values():
        lang[v["lang"]] += v["stars"]
    tot = sum(lang.values()) or 1
    for l, s in lang.most_common(9):
        print(f"  {l[:13]:15}{s:>9,}  {s*100//tot:>3}%  {'#'*(s*40//tot)}")

    # ---- momentum: young repos that already have traction
    print("\n" + "─" * 78)
    print("RISING — repos created in 2025+ with the most stars")
    print("─" * 78)
    young = [(v["stars"], n, v) for n, v in gh.items() if v.get("created", "") >= "2025"]
    for s, n, v in sorted(young, reverse=True)[:18]:
        print(f"  {s:>6}*  {v['created']}  {v['lang'][:10]:12}{n[:30]:32}{v['desc'][:26]}")

    # ---- problems
    iss_all = d.get("issues", {})
    iss = {k: v for k, v in iss_all.items()
           if GEO_TEXT.search(f"{v.get('repo','')} {v.get('title','')}")}
    print("\n" + "─" * 78)
    print(f"WHAT IS BROKEN — geo-filtered open issues ({len(iss)} of {len(iss_all)})")
    print("─" * 78)
    for v in sorted(iss.values(), key=lambda x: -x["reactions"])[:22]:
        print(f"  {v['reactions']:>3}R {v['comments']:>3}c  {v['repo'][:24]:26}{v['title'][:44]}")

    print("\n" + "─" * 78)
    print("ISSUE THEMES — words recurring in geo issue titles")
    print("─" * 78)
    stop = set("the a an of for and with to in on is are not when if but or from "
               "this that does not doesn support add adding using use new bug it "
               "should would could can cannot after before while into via".split())
    w = Counter()
    for v in iss.values():
        for x in re.findall(r"[a-zA-Z][a-zA-Z\-]{3,}", v["title"].lower()):
            if x not in stop:
                w[x] += 1
    print("  " + ", ".join(f"{x}({c})" for x, c in w.most_common(28)))

    # ---- bluesky
    bs = d.get("bluesky", {})
    geo_bs = {k: v for k, v in bs.items() if GEO_TEXT.search(v.get("text", ""))}
    print("\n" + "─" * 78)
    print(f"TOP BLUESKY POSTS — geo-filtered ({len(geo_bs)} of {len(bs)})")
    print("─" * 78)
    for v in sorted(geo_bs.values(), key=lambda x: -x["likes"])[:16]:
        print(f"  {v['likes']:>4}L {v['reposts']:>3}rt  @{v['handle'][:18]:20}{v['text'][:38]}")

    # ---- unanswered SE, themed
    un = d.get("se_unanswered", {})
    print("\n" + "─" * 78)
    print("UNMET NEED — unanswered GIS StackExchange, by tag")
    print("─" * 78)
    tc = Counter()
    for v in un.values():
        for t in v.get("tags", []):
            tc[t] += 1
    print("  " + ", ".join(f"{t}({c})" for t, c in tc.most_common(22)))
    print()


if __name__ == "__main__":
    main()
