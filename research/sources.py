"""Independent collectors. Missing access is recorded, never invented as data."""
from __future__ import annotations

import email.utils
import functools
import json
import os
import re
import ssl
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import urlopen

from .core import ROOT, SourceError, canonical_url, clean, merge_records, public_https, read_json, record, stable_id, utcnow
from .taxonomy import APPS, FREELANCE
from .scraping import PageFetcher, marketplace_links, script_texts


def iso_date(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(str(value))
        except (ValueError, TypeError):
            for fmt in ("%b %d, %Y", "%B %d, %Y"):
                try:
                    dt = datetime.strptime(str(value), fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    return dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()


def feed_items(xml):
    root = ET.fromstring(xml)
    for item in root.findall(".//item"):
        yield {"title": clean(item.findtext("title"), 250), "url": item.findtext("link") or "",
               "summary": clean(item.findtext("description"), 1500),
               "published_at": iso_date(item.findtext("pubDate")),
               "tags": [clean(e.text) for e in item.findall("category")]}


def categories_for(text):
    text = text.lower()
    scores = [(sum(bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text)) for term in terms), name)
              for name, (terms, _, _) in FREELANCE.items() if terms]
    found = [name for score, name in sorted(scores, reverse=True) if score]
    return found[:3] or ["Other services"]


def apple_category(genre):
    return next((name for name, (g, _, _) in APPS.items() if str(g) == str(genre)), "Other apps")


def apple_detail(item, country):
    app_id = str(item["trackId"])
    return record("apps", "apple", f"{country}:{app_id}", item.get("trackName"), item.get("trackViewUrl"),
                  entity_id=f"apple:{app_id}", store="App Store", country=country, kind="app",
                  categories=[apple_category(item.get("primaryGenreId"))],
                  description=clean(item.get("description"), 2000),
                  released_at=iso_date(item.get("releaseDate")), updated_at=iso_date(item.get("currentVersionReleaseDate")),
                  rating=item.get("averageUserRating"), ratings=item.get("userRatingCount"),
                  price=item.get("price"), currency=item.get("currency"),
                  developer=clean(item.get("artistName")), revenue_evidence="unknown", charts=[], reviews=[])


def apple(client, config, history):
    rows = []
    limits = config["limits"]
    countries = config["markets"]
    size = limits["chart_size"]
    interval = limits["apple_interval_seconds"]
    source = "apple"
    for country in countries:
        charts = [(None, "All categories")]
        if country == countries[0]:
            charts += [(genre, category) for category, (genre, _, _) in APPS.items() if genre]
        for genre, category in charts:
            for chart in ("topfreeapplications", "toppaidapplications", "topgrossingapplications"):
                url = f"https://itunes.apple.com/{country}/rss/{chart}/limit={size}"
                if genre:
                    url += f"/genre={genre}"
                try:
                    data = client.get(source, url + "/json", interval=interval)
                    entries = data.get("feed", {}).get("entry", [])
                    if isinstance(entries, dict):
                        entries = [entries]
                    for rank, item in enumerate(entries, 1):
                        app_id = item["id"]["attributes"]["im:id"]
                        price = item.get("im:price", {}).get("attributes", {})
                        cat = category if genre else apple_category(item.get("category", {}).get("attributes", {}).get("im:id"))
                        rows.append(record("apps", source, f"{country}:{app_id}", item["im:name"]["label"], item["id"]["label"],
                            entity_id=f"apple:{app_id}", store="App Store", country=country, kind="app", categories=[cat],
                            description=clean(item.get("summary", {}).get("label"), 2000),
                            released_at=iso_date(item.get("im:releaseDate", {}).get("label")),
                            price=float(price["amount"]) if price.get("amount") else None, currency=price.get("currency"),
                            developer=clean(item.get("im:artist", {}).get("label")),
                            revenue_evidence="grossing_chart" if chart == "topgrossingapplications" else "unknown",
                            charts=[{"chart": chart, "category": category, "country": country, "rank": rank}], reviews=[]))
                except (SourceError, KeyError, TypeError, ValueError) as exc:
                    client.status(source, "partial", f"Some chart requests failed: {type(exc).__name__}")
        # Search exposes smaller products that do not reach the charts. Rotate the secondary phrase daily.
        if country == countries[0]:
            for category, (_, terms, _) in APPS.items():
                term = terms[datetime.now(timezone.utc).toordinal() % len(terms)]
                try:
                    data = client.get(source, "https://itunes.apple.com/search", params={"term": term, "country": country,
                                      "entity": "software", "limit": limits["search_results"]}, interval=interval)
                    for item in data.get("results", []):
                        row = apple_detail(item, country)
                        row["categories"] = list(set(row["categories"] + [category]))
                        row["discovery"] = [term]
                        rows.append(row)
                except (SourceError, KeyError, TypeError, ValueError):
                    client.status(source, "partial", "Some keyword searches failed")
    rows = merge_records(rows)
    # Keep following past candidates even when they disappear from charts, within an explicit cap.
    watch = [r for r in history if r.get("source") == source and r.get("kind") == "app"]
    known = {r["id"] for r in rows}
    rows += [{**r, "charts": [], "reviews": [], "observed_at": utcnow(), "watch_only": True}
             for r in watch[:300] if r["id"] not in known]
    refreshed = set()
    for country in countries:
        selected = [r for r in rows if r["country"] == country]
        for start in range(0, len(selected), 100):
            ids = [r["entity_id"].split(":", 1)[1] for r in selected[start:start + 100]]
            try:
                data = client.get(source, "https://itunes.apple.com/lookup", params={"id": ",".join(ids), "country": country}, interval=interval)
                for item in data.get("results", []):
                    row = apple_detail(item, country)
                    # Missing chart membership is not itself evidence of a revenue change.
                    rows.append(row)
                    refreshed.add(row["id"])
            except (SourceError, KeyError, ValueError):
                client.status(source, "partial", "Some app metadata lookups failed")
    rows = merge_records(rows)
    rows = [r for r in rows if not r.get("watch_only") or r["id"] in refreshed]
    candidates = sorted(rows, key=lambda r: (not any("grossing" in c["chart"] for c in r.get("charts", [])),
                                            -(r.get("ratings") or 0)))
    # Round robin across categories avoids spending every review request on huge games.
    candidates = diversify(candidates, limits["reviews_per_store"])
    for row in candidates:
        app_id = row["entity_id"].split(":", 1)[1]
        try:
            data = client.get("apple_reviews", f"https://itunes.apple.com/{row['country']}/rss/customerreviews/id={app_id}/sortby=mostrecent/json", interval=interval)
            entries = data.get("feed", {}).get("entry", [])
            if isinstance(entries, dict):
                entries = [entries]
            row["reviews"] = [{"rating": int(e["im:rating"]["label"]), "text": clean(e.get("content", {}).get("label"), 400),
                               "published_at": iso_date(e.get("updated", {}).get("label"))}
                              for e in entries if "im:rating" in e][:limits["reviews_per_app"]]
        except (SourceError, KeyError, ValueError):
            client.status("apple_reviews", "partial", "Some review feeds unavailable")
    for row in rows:
        row["revenue_evidence"] = "grossing_chart" if any("grossing" in c["chart"] for c in row.get("charts", [])) else ("paid_listing" if (row.get("price") or 0) > 0 else "unknown")
    return rows


def diversify(rows, limit):
    groups = {}
    for row in rows:
        groups.setdefault((row.get("categories") or ["Other"])[0], []).append(row)
    result = []
    while groups and len(result) < limit:
        for key in list(groups):
            if len(result) == limit:
                break
            result.append(groups[key].pop(0))
            if not groups[key]:
                del groups[key]
    return result


def google_play(client, config, history):
    from google_play_scraper import Sort, app, reviews, search
    from google_play_scraper.utils import request as play_request

    # The upstream parser changes urllib's global SSL default on import. Restore
    # certificate verification before any collection or subsequent SMTP use.
    ssl._create_default_https_context = ssl.create_default_context
    play_request.urlopen = functools.partial(urlopen, timeout=config["limits"]["timeout_seconds"], context=ssl.create_default_context())

    limits = config["limits"]
    rows = []
    def call(fn, *args, **kwargs):
        client.pace("play.google.com", limits["google_interval_seconds"])
        try:
            result = fn(*args, **kwargs)
            client.event("google_play", True)
            return result
        except Exception as exc:
            client.event("google_play", False)
            raise SourceError(type(exc).__name__) from None

    def normalize(item, country, categories):
        app_id = item["appId"]
        if not isinstance(app_id, str) or not app_id.strip() or not isinstance(item.get("title"), str):
            raise ValueError("Invalid Play Store app identity")
        return record("apps", "google_play", f"{country}:{app_id}", item.get("title"),
            f"https://play.google.com/store/apps/details?id={quote(app_id)}&gl={country}",
            entity_id=f"google_play:{app_id}", kind="app", country=country, store="Google Play", categories=categories,
            description=clean(item.get("description") or item.get("summary"), 2000),
            rating=item.get("score"), ratings=item.get("ratings"), installs_min=item.get("minInstalls"),
            price=item.get("price"), currency=item.get("currency"), offers_iap=item.get("offersIAP"),
            iap_price=item.get("inAppProductPrice"), ads=item.get("adSupported"),
            released_at=iso_date(item.get("released")), updated_at=iso_date(item.get("updated")),
            developer=clean(item.get("developer")), charts=[], reviews=[],
            revenue_evidence="paid_listing" if (item.get("price") or 0) > 0 else ("iap_available" if item.get("offersIAP") else "unknown"))

    def discover(task):
        country, category, terms = task
        term = terms[datetime.now(timezone.utc).toordinal() % len(terms)]
        found = []
        try:
            for item in call(search, term, lang=config["language"], country=country, n_hits=limits["search_results"]):
                try:
                    row = normalize(item, country, [category])
                    row["discovery"] = [term]
                    found.append(row)
                except (KeyError, TypeError, ValueError):
                    client.status("google_play", "partial", "Malformed search entries skipped; usable app identities retained")
        except (SourceError, KeyError, TypeError, ValueError):
            pass
        return found

    tasks = [(country, category, terms) for country in config["markets"] for category, (_, terms, _) in APPS.items()]
    with ThreadPoolExecutor(max_workers=limits.get("google_workers", 4)) as pool:
        for found in pool.map(discover, tasks):
            rows.extend(found)
    rows = merge_records(rows)
    # Details/reviews are costly; use category-balanced enrichment, plus a bounded persistent watchlist.
    selected = diversify(sorted(rows, key=lambda r: -(r.get("ratings") or 0)), limits["google_details"])
    selected_ids = {r["id"] for r in selected}
    selected += [r for r in history if r.get("source") == "google_play" and r["id"] not in selected_ids][:30]
    def detail(row):
        try:
            item = call(app, row["entity_id"].split(":", 1)[1], lang=config["language"], country=row["country"])
            return normalize(item, row["country"], row["categories"])
        except (SourceError, KeyError, TypeError, ValueError):
            return None
    with ThreadPoolExecutor(max_workers=limits.get("google_workers", 4)) as pool:
        detailed = [row for row in pool.map(detail, selected) if row]
    enriched = merge_records(rows + detailed)
    review_ids = {r["id"] for r in diversify(detailed, limits["reviews_per_store"])}
    def review(row):
        try:
            found, _ = call(reviews, row["entity_id"].split(":", 1)[1], lang=config["language"], country=row["country"],
                            sort=Sort.NEWEST, count=limits["reviews_per_app"])
            row["reviews"] = [{"rating": r.get("score"), "text": clean(r.get("content"), 400),
                                "published_at": iso_date(r["at"].isoformat()) if r.get("at") else None} for r in found]
        except (SourceError, KeyError, TypeError, ValueError):
            pass
    with ThreadPoolExecutor(max_workers=limits.get("google_workers", 4)) as pool:
        list(pool.map(review, [r for r in enriched if r["id"] in review_ids]))
    return enriched


def freelancer_project(item):
    skills = [j.get("name", "") for j in item.get("jobs", [])]
    text = clean(item.get("description") or item.get("preview_description"), 2500)
    currency = item.get("currency") or {}
    budget = item.get("budget") or {}
    bids = item.get("bid_stats") or {}
    return record("freelance", "freelancer", str(item["id"]), item.get("title"),
        "https://www.freelancer.com/projects/" + (item.get("seo_url") or str(item["id"])),
        kind="project", platform="Freelancer", description=text,
        categories=categories_for(" ".join([item.get("title", ""), text, *skills])), skills=skills,
        published_at=iso_date(item.get("time_submitted")), status=item.get("status"),
        budget_min=budget.get("minimum"), budget_max=budget.get("maximum"), currency=currency.get("code"),
        budget_unit="hour" if item.get("type") == "hourly" else "project",
        bids=bids.get("bid_count"), evidence="live_project", client_payment_verified=(item.get("owner") or {}).get("payment_verified"))


def freelancer(client, config, history):
    rows = []
    limits = config["limits"]
    headers = {"freelancer-oauth-v1": os.environ["FREELANCER_TOKEN"]} if os.getenv("FREELANCER_TOKEN") else None
    queries = [None] + [terms[0] for terms, _, _ in FREELANCE.values() if terms]
    for query in queries:
        pages = limits["freelancer_pages"] if query is None else 1
        for page in range(pages):
            params = {"limit": limits["freelancer_page_size"], "offset": page * limits["freelancer_page_size"],
                      "full_description": "true", "job_details": "true"}
            if query:
                params["query"] = query
            try:
                data = client.get("freelancer", "https://www.freelancer.com/api/projects/0.1/projects/active/", params=params, headers=headers, interval=1)
                if data.get("status") != "success":
                    raise SourceError("API did not return success")
                projects = data.get("result", {}).get("projects", [])
                rows.extend(freelancer_project(p) for p in projects
                            if p.get("status") == "active" and p.get("sub_status") not in {"closed", "deleted", "awarded", "expired"})
                if len(projects) < params["limit"]:
                    break
            except (SourceError, KeyError, ValueError):
                client.status("freelancer", "partial" if rows else "failed", "Some project queries unavailable")
                break
    return merge_records(rows)


def peopleperhour_page(html):
    scripts = script_texts(html)
    match = next((re.search(r"window\.PPHReact\.initialState\s*=\s*", script) for script in scripts
                  if re.search(r"window\.PPHReact\.initialState\s*=\s*", script)), None)
    if match is None:
        raise SourceError("Public listing schema changed or access restricted")
    state = json.JSONDecoder().raw_decode(match.string[match.end():])[0]
    listing = state["freelanceJobs"]["main"]
    projects = state.get("entities", {}).get("projects", {})
    rows = []
    for ref in listing.get("data", []):
        item = projects.get(str(ref["id"]), {}).get("attributes", {})
        if item.get("item_type") != "job" or not item.get("open"):
            continue
        skills = [item.get("category", {}).get("cate_name", ""), item.get("sub_category", {}).get("subcate_name", "")]
        text = clean(item.get("proj_desc"), 2500)
        # Original currency and budget only; never use the site's converted display budget.
        rows.append(record("freelance", "peopleperhour", str(ref["id"]), item.get("title"), item.get("url"),
            kind="project", platform="PeoplePerHour", description=text, skills=skills,
            categories=categories_for(" ".join([item.get("title", ""), text, *skills])),
            published_at=(item.get("posted_dt") or "").replace(" ", "T") or None,
            posting_timezone="unspecified by source", status="open", budget_min=item.get("budget"), budget_max=item.get("budget"),
            currency=item.get("currency"), budget_unit="hour" if item.get("project_type") == "hourly" else "project",
            bids=item.get("proposalCount"), evidence="live_project", prefunded=item.get("isPreFundedJob"),
            location=item.get("location_type")))
    return rows, listing.get("meta", {}).get("total-pages", 1)


def peopleperhour(client, config, history):
    rows = []
    seen = set()
    fetcher = PageFetcher(client, config)
    for page in range(1, config["limits"]["peopleperhour_pages"] + 1):
        try:
            document = fetcher.get("peopleperhour", "https://www.peopleperhour.com/freelance-jobs",
                                   params={"page": page, "sort": "latest"}, required_text="PPHReact.initialState")
            batch, total = peopleperhour_page(document.html_content)
            new = [r for r in batch if r["id"] not in seen]
            if not new:
                if batch:
                    client.status("peopleperhour", "partial", "Pagination repeated a page; collection stopped")
                break
            rows.extend(new)
            seen.update(r["id"] for r in new)
            if page >= total:
                break
        except (SourceError, KeyError, ValueError, TypeError):
            client.status("peopleperhour", "partial" if rows else "failed", "Some public listing pages unavailable or changed")
            break
    return rows


def marketplace_pages(client, config, history):
    fetcher = PageFetcher(client, config)
    rows = []
    pages = [
        ("Guru", "https://www.guru.com/d/jobs/", r"/jobs/.+/\d+/?$"),
        ("Upwork", "https://www.upwork.com/freelance-jobs/", r"/freelance-jobs/apply/.+"),
        ("Contra", "https://contra.com/opportunities", r"/opportunities/[A-Za-z0-9_-]{5,}"),
        ("Truelancer", "https://www.truelancer.com/freelance-jobs", r"/freelance-project/.+-\d+/?$"),
    ]
    for platform, url, pattern in pages:
        source = f"pages:{platform}"
        try:
            page = fetcher.get(source, url)
            found = marketplace_links(page, url, pattern)
            for item in found:
                rows.append(record("freelance", source, stable_id(item["url"]), item["title"], item["url"],
                    kind="indexed_lead", platform=platform, categories=categories_for(item["title"]),
                    description="Discovered on the platform's public listing page; verify availability and scope on the linked page.",
                    published_at=None, evidence="public_page_lead"))
            client.status(source, "ok" if found else "empty", f"{len(found)} public-page leads; excluded from verified project counts")
        except SourceError:
            client.status(source, "failed", "Public page unavailable through HTTP/browser collection")
    return rows


def remote_feeds(client, config, history):
    rows = []
    try:
        data = client.get("remotive", "https://remotive.com/api/remote-jobs")
        for item in data.get("jobs", []):
            if item.get("job_type") not in ("contract", "freelance"):
                continue
            rows.append(record("freelance", "remotive", str(item["id"]), item.get("title"), item.get("url"),
                kind="contract_job", platform="Remotive", categories=categories_for(item.get("title", "") + " " + item.get("description", "")),
                description=clean(item.get("description"), 2000), published_at=iso_date(item.get("publication_date")),
                location=clean(item.get("candidate_required_location")), salary_text=clean(item.get("salary")),
                evidence="contract_listing", attribution="Remotive.com (delayed public feed; not marketplace project demand)"))
    except (SourceError, KeyError):
        client.status("remotive", "failed", "Contract feed unavailable")
    feeds = [{"name": "We Work Remotely", "url": "https://weworkremotely.com/remote-contract-jobs.rss"}] + config.get("extra_feeds", [])
    for feed in feeds:
        name = feed["name"]
        try:
            xml = client.get(name, public_https(feed["url"]), raw=True)
            items = list(feed_items(xml))
            for item in items:
                rows.append(record("freelance", name, stable_id(canonical_url(item["url"])), item["title"], item["url"],
                    kind="contract_job", platform=name, description=item["summary"], published_at=item["published_at"],
                    categories=categories_for(item["title"] + " " + item["summary"]), evidence="contract_listing"))
        except (SourceError, ValueError, ET.ParseError, OSError):
            client.status(name, "failed", "Feed unavailable or invalid")
    return rows


SEARCH_PLATFORMS = {
    "Upwork": ("upwork.com", "/freelance-jobs/apply/"),
    "PeoplePerHour": ("peopleperhour.com", "/freelance-jobs/"),
    "Guru": ("guru.com", "/jobs/"),
    "Contra": ("contra.com", "/opportunities/"),
    "Fiverr": ("fiverr.com", "/"),
}


def search_web(client, query):
    if os.getenv("BRAVE_SEARCH_API_KEY"):
        data = client.get("brave_search", "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 10, "freshness": "pm"}, headers={"X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"]}, interval=1.1)
        return [{"title": r.get("title"), "url": r.get("url"), "summary": clean(r.get("description")), "published_at": None}
                for r in data.get("web", {}).get("results", [])]
    xml = client.get("bing_search", "https://www.bing.com/search", params={"q": query, "format": "rss"}, raw=True, interval=1.5)
    return list(feed_items(xml))


def web_search(client, config, history):
    rows = []
    limit = config["limits"]["web_queries"]
    queries = []
    # Round robin ensures every platform receives searches even with a reduced budget.
    for category, (terms, _, _) in FREELANCE.items():
        if not terms:
            continue
        for platform, (domain, path) in SEARCH_PLATFORMS.items():
            queries.append(("freelance", category, platform, f"site:{domain}{path} {terms[0]} freelance project"))
    for category, (_, terms, _) in APPS.items():
        queries.insert(len(queries) // 2, ("apps", category, "Web", f'"{terms[0]}" app revenue subscription founder'))
    provider = "brave_search" if os.getenv("BRAVE_SEARCH_API_KEY") else "bing_search"
    accepted = 0
    platform_counts = {name: 0 for name in SEARCH_PLATFORMS}
    for track, category, platform, query in queries[:limit]:
        try:
            results = search_web(client, query)
            for item in results:
                url = canonical_url(item["url"])
                if not url:
                    continue
                if track == "freelance":
                    domain, path = SEARCH_PLATFORMS[platform]
                    host = urlparse(url).hostname or ""
                    if not (host == domain or host.endswith("." + domain)) or path not in urlparse(url).path:
                        continue
                    # Search RSS dates can be the crawl date: never treat them as posting dates.
                    kind = "seller_listing" if platform == "Fiverr" else "indexed_lead"
                else:
                    if not re.search(r"\brevenue\b|\bmrr\b|\barr\b|\bsubscription\b|\bearnings\b", item["title"] + " " + item["summary"], re.I):
                        continue
                    kind = "revenue_lead"
                rows.append(record(track, provider, stable_id(url), item["title"], url,
                    kind=kind, platform=platform, categories=[category], description=clean(item["summary"], 1000),
                    published_at=None, evidence="unverified_search_result", query=query))
                accepted += 1
                if platform in platform_counts:
                    platform_counts[platform] += 1
        except (SourceError, ET.ParseError, KeyError):
            continue
    client.status(provider, "ok" if accepted else "empty", f"{accepted} relevant leads; indexed results are not verified live projects or verified revenue")
    for platform, count in platform_counts.items():
        client.status(f"discovery:{platform}", "ok" if count else "empty", f"{count} indexed leads; not live-verified buyer demand")
    return merge_records(rows)


def revenue_news(client, config, history):
    rows = []
    for category, (_, terms, _) in list(APPS.items())[:config["limits"]["news_queries"]]:
        query = f'"{terms[0]}" app revenue when:30d'
        try:
            xml = client.get("google_news", "https://news.google.com/rss/search",
                             params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}, raw=True, interval=1)
            for item in list(feed_items(xml))[:10]:
                rows.append(record("apps", "google_news", stable_id(item["url"]), item["title"], item["url"],
                    kind="revenue_lead", categories=[category], description=item["summary"], published_at=item["published_at"],
                    evidence="article_to_verify"))
        except (SourceError, ET.ParseError):
            continue
    # Founder discussions are useful for idea discovery, but remain self-reported leads.
    for term in ("app MRR", "mobile app revenue", "Show HN app"):
        try:
            since = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())
            data = client.get("hacker_news", "https://hn.algolia.com/api/v1/search_by_date",
                params={"query": term, "tags": "story", "numericFilters": f"created_at_i>{since}", "hitsPerPage": 30})
            for hit in data.get("hits", []):
                rows.append(record("apps", "hacker_news", hit["objectID"], hit.get("title"),
                    hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}", kind="revenue_lead",
                    categories=["Other apps"], description=clean(hit.get("story_text")), published_at=iso_date(hit.get("created_at")),
                    evidence="founder_discussion", points=hit.get("points")))
        except (SourceError, KeyError):
            continue
    return merge_records(rows)


def intelligence(client, config, history):
    """Provider-neutral import: licensed estimates and attributable publisher disclosures."""
    path = ROOT / config.get("intelligence_file", "data/imports/app-intelligence.json")
    url = os.getenv("APP_INTELLIGENCE_URL")
    if url:
        data = client.get("intelligence", public_https(url))
    elif path.exists():
        data = read_json(path)
    else:
        client.status("intelligence", "not_configured", "No licensed revenue estimates or publisher disclosures configured")
        return []
    rows = []
    for item in data.get("apps", []):
        required = ("store", "app_id", "title", "url", "country", "category", "amount", "currency", "period_start", "period_end", "evidence_type", "source_url")
        if any(item.get(k) is None for k in required):
            raise SourceError("Revenue import missing required fields; see docs/INTELLIGENCE.md")
        if item["evidence_type"] not in ("publisher_reported", "third_party_estimate"):
            raise SourceError("Revenue import requires an explicit evidence_type")
        if item["store"] not in ("apple", "google_play") or not canonical_url(item["source_url"]):
            raise SourceError("Invalid revenue source or store")
        if float(item["amount"]) < 0 or not iso_date(item["period_start"]) or not iso_date(item["period_end"]):
            raise SourceError("Invalid revenue amount or reporting period")
        if iso_date(item["period_start"]) > iso_date(item["period_end"]):
            raise SourceError("Revenue period is reversed")
        key = stable_id(json.dumps(item, sort_keys=True))
        rows.append(record("apps", "intelligence", key, item["title"], item["url"], kind="revenue_observation",
            entity_id=f"{item['store']}:{item['app_id']}", country=item["country"], categories=[item["category"]],
            revenue_evidence=item["evidence_type"], revenue_amount=float(item["amount"]), revenue_currency=item["currency"],
            period_start=item["period_start"], period_end=item["period_end"], source_url=canonical_url(item["source_url"]),
            methodology=clean(item.get("methodology")), published_at=iso_date(item.get("published_at"))))
    client.status("intelligence", "ok", f"{len(rows)} attributed revenue observations")
    return rows


COLLECTORS = {f.__name__: f for f in (apple, google_play, freelancer, peopleperhour, marketplace_pages, remote_feeds, web_search, revenue_news, intelligence)}


def collect(client, config, history):
    rows = []
    def run(name):
        print(f"Starting {name}", flush=True)
        result = COLLECTORS[name](client, config, history)
        if name in client.health:
            client.health[name]["records"] = len(result)
            if not result and client.health[name].get("status") == "ok":
                client.status(name, "empty", "No matching records returned")
        print(f"Finished {name}: {len(result)} records", flush=True)
        return result
    with ThreadPoolExecutor(max_workers=len(config["sources"]) or 1) as pool:
        jobs = {pool.submit(run, name): name for name in config["sources"]}
        for job in as_completed(jobs):
            name = jobs[job]
            try:
                rows.extend(job.result())
            except Exception as exc:
                # Do not print exception URLs: optional providers can use credentials in query strings.
                client.status(name, "failed", f"Collector stopped: {type(exc).__name__}")
                print(f"Collector {name} failed ({type(exc).__name__}); other sources continue", flush=True)
    return merge_records(rows)
