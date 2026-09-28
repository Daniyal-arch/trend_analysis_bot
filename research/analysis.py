"""Deterministic, auditable signals. Scores prioritize research, not returns."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .core import read_json
from .taxonomy import APPS, FREELANCE

PAINS = {
    "price / subscription": r"expensive|subscription|paywall|overpriced|pay.?to|pricing",
    "reliability": r"crash|bug|freez|not work|doesn.t work|broken|slow|lag",
    "advertising": r"\bads\b|advert|pop.?up",
    "ease of use": r"confus|complicated|difficult|hard to|unintuitive",
    "missing capability": r"wish|missing|please add|needs? (?:a|an|the)|would be|offline|export",
    "privacy / account": r"privacy|tracking|personal data|sign.?in|log.?in|account",
}


def windows(period, as_of):
    if period == "daily":
        start = end = as_of
        prior_start = prior_end = as_of - timedelta(days=1)
    elif period == "weekly":
        end = as_of - timedelta(days=1)
        start = end - timedelta(days=6)
        prior_end = start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=6)
    elif period == "monthly":
        end = as_of.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
        prior_end = start - timedelta(days=1)
        prior_start = prior_end.replace(day=1)
    else:
        raise ValueError(f"Unknown period: {period}")
    return start, end, prior_start, prior_end


def due_periods(as_of):
    return ["daily"] + (["weekly"] if as_of.weekday() == 0 else []) + (["monthly"] if as_of.day == 1 else [])


def snapshots(path):
    result = []
    for file in sorted(path.glob("*.json.gz")):
        item = read_json(file)
        if item.get("schema_version") == 3:
            result.append(item)
    return result


def latest_records(scans, track=None):
    result = {}
    for scan in sorted(scans, key=lambda s: s["date"]):
        for row in scan["records"]:
            if track is None or row["track"] == track:
                result[row["id"]] = row
    return list(result.values())


def age_days(row, end):
    try:
        return (end - date.fromisoformat(row["released_at"][:10])).days
    except (TypeError, ValueError, KeyError):
        return None


def pain_points(rows):
    counts = Counter()
    snippets = []
    total = 0
    for row in rows:
        for review in row.get("reviews", []):
            total += 1
            if not review.get("rating") or review["rating"] > 3:
                continue
            for name, pattern in PAINS.items():
                if re.search(pattern, review.get("text", ""), re.I):
                    counts[name] += 1
                    if len(snippets) < 3:
                        snippets.append({"theme": name, "text": review["text"][:200], "url": row["url"]})
    return {"themes": dict(counts.most_common()), "sample_size": total, "examples": snippets}


def app_score(row, previous, end):
    evidence = row.get("revenue_evidence", "unknown")
    parts = {"monetization": {"grossing_chart": 30, "paid_listing": 12, "iap_available": 8}.get(evidence, 0)}
    count = max(0, row.get("ratings") or 0)
    parts["adoption"] = min(20, round(math.log10(count + 1) * 4))
    age = age_days(row, end)
    parts["young_product"] = 15 if age is not None and 0 <= age <= 730 else 0
    old = previous.get(row["id"])
    delta = None
    rank_gain = None
    parts["measured_growth"] = 0
    if old:
        if row.get("ratings") is not None and old.get("ratings") is not None:
            delta = row["ratings"] - old["ratings"]
            if delta > 0:
                parts["measured_growth"] = min(15, round(math.log10(delta + 1) * 5))
        def ranks(r):
            return {(c["country"], c["category"], c["chart"]): c["rank"] for c in r.get("charts", [])}
        old_ranks, new_ranks = ranks(old), ranks(row)
        changes = [old_ranks[k] - rank for k, rank in new_ranks.items() if k in old_ranks]
        rank_gain = max(changes) if changes else None
    pain = pain_points([row])
    parts["review_gaps"] = min(10, sum(pain["themes"].values()) * 2)
    parts["approachable_scale"] = 10 if 50 <= count <= 50000 else 0
    return {**row, "score": sum(parts.values()), "score_parts": parts, "age_days": age,
            "ratings_delta": delta, "best_comparable_rank_gain": rank_gain, "pains": pain,
            "momentum": "observed" if delta is not None or rank_gain is not None else "insufficient history"}


def unique_apps(rows):
    # Keep market-level metrics separate; choose one representative per app for summaries.
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["entity_id"]].append(row)
    result = []
    for group in grouped.values():
        representative = dict(max(group, key=lambda r: r["score"]))
        representative["markets_seen"] = sorted({r["country"] for r in group})
        representative["categories"] = sorted({c for r in group for c in r.get("categories", [])})
        result.append(representative)
    return sorted(result, key=lambda r: (-r["score"], r["title"]))


def budget_summary(rows):
    groups = defaultdict(list)
    for row in rows:
        if row.get("currency") and row.get("budget_max") is not None:
            groups[(row["currency"], row.get("budget_unit", "project"))].append(row)
    output = []
    for (currency, unit), group in sorted(groups.items()):
        values = sorted(float(r["budget_max"]) for r in group)
        n = len(values)
        median = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
        output.append({"currency": currency, "unit": unit, "n": n, "median_advertised_max": round(median, 2),
                       "min_advertised_max": values[0], "max_advertised_max": values[-1]})
    return output


def published_in(row, start, end, timezone="Asia/Karachi"):
    try:
        dt = datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
        day = dt.astimezone(ZoneInfo(timezone)).date() if dt.tzinfo else dt.date()
        return start <= day <= end
    except (KeyError, TypeError, ValueError):
        return False


def build_report(all_scans, track, period, as_of, config):
    start, end, prior_start, prior_end = windows(period, as_of)
    selected = [s for s in all_scans if start.isoformat() <= s["date"] <= end.isoformat()]
    previous_scans = [s for s in all_scans if prior_start.isoformat() <= s["date"] <= prior_end.isoformat()]
    current = latest_records(selected, track)
    previous = {r["id"]: r for r in latest_records(previous_scans, track)}
    expected = (end - start).days + 1
    health = [{"date": s["date"], "sources": s["health"]} for s in selected]
    report = {"track": track, "period": period, "as_of": as_of.isoformat(), "start": start.isoformat(), "end": end.isoformat(),
              "previous_start": prior_start.isoformat(), "previous_end": prior_end.isoformat(),
              "scan_days": len(selected), "expected_days": expected, "previous_scan_days": len(previous_scans),
              "partial_window": len(selected) < expected, "health": health, "records": len(current),
              "markets": sorted({m for s in selected for m in s.get("markets", config["markets"])}),
              "quick_scan": any(s.get("quick", False) for s in selected), "categories": [], "opportunities": [],
              "methodology": "Research scores are transparent heuristics, not revenue forecasts. Counts describe the collected sample, not the whole market."}
    if track == "apps":
        revenues = [r for r in current if r.get("kind") == "revenue_observation"]
        ranked = unique_apps([app_score(r, previous, end) for r in current if r.get("kind") == "app"])
        for row in ranked:
            row["revenue_observations"] = [r for r in revenues if r.get("entity_id") == row["entity_id"]]
        leads = [r for r in current if r.get("kind") == "revenue_lead"]
        report.update(app_count=len(ranked), revenue_observations=revenues, research_leads=leads[:40],
                      newly_released=[r for r in ranked if r["age_days"] is not None and 0 <= r["age_days"] <= 180][:20],
                      young_apps=[r for r in ranked if r["age_days"] is not None and 180 < r["age_days"] <= 730][:20],
                      established_apps=[r for r in ranked if r["age_days"] is not None and r["age_days"] > 730][:20])
        for name in list(APPS) + ["Other apps"]:
            apps = [r for r in ranked if name in r.get("categories", [])]
            monetized = [r for r in apps if r.get("revenue_evidence") != "unknown"]
            pain = pain_points(apps)
            hypotheses = APPS.get(name, (None, [], "a narrow utility supported by additional customer interviews"))
            top = apps[:config["report"]["examples_per_category"]]
            category = {"name": name, "count": len(apps), "monetization_signals": len(monetized),
                        "grossing_apps": sum(r.get("revenue_evidence") == "grossing_chart" for r in apps),
                        "examples": top, "pains": pain, "hypothesis": hypotheses[2],
                        "priority": round(sum(r["score"] for r in top) / len(top), 1) if top else 0,
                        "confidence": "medium" if monetized and pain["sample_size"] >= 10 else ("low" if apps else "no data")}
            report["categories"].append(category)
        for category in sorted(report["categories"], key=lambda r: -r["priority"]):
            if not category["count"]:
                continue
            top = category["examples"]
            gap = next(iter(category["pains"]["themes"]), "an unvalidated workflow gap")
            report["opportunities"].append({"name": category["name"], "priority": category["priority"], "confidence": category["confidence"],
                "proposal": "Test " + category["hypothesis"] + ".", "evidence": f"{category['count']} distinct apps; {category['grossing_apps']} on grossing charts; review theme: {gap}.",
                "monetization": "Test a one-time purchase for a finite utility, or a subscription only for recurring value. Pricing is a hypothesis, not inferred revenue.",
                "mvp": "Choose one audience and one workflow. Prototype the smallest useful version; validate before a full build.",
                "complexity": "Complex" if category["name"] in {"Social & dating", "Games", "Medical", "Navigation"} else "Simple to moderate for a narrow MVP",
                "validation": "Interview 5 target users, compare 3 competitors, and test willingness to pay with a landing page or paid pilot.",
                "risk": "Observed incumbents do not prove a new entrant can acquire customers profitably. Check acquisition costs, retention, platform fees and ongoing API costs.",
                "sources": [{"title": r["title"], "url": r["url"]} for r in top[:3]]})
    else:
        projects = [r for r in current if r.get("kind") == "project"]
        contracts = [r for r in current if r.get("kind") == "contract_job"]
        leads = [r for r in current if r.get("kind") == "indexed_lead"]
        sellers = [r for r in current if r.get("kind") == "seller_listing"]
        report.update(project_count=len(projects), contract_count=len(contracts), indexed_leads=leads[:60], seller_count=len(sellers),
                      seller_examples=sellers[:15], platforms=dict(Counter(r.get("platform", r["source"]) for r in projects + contracts + leads + sellers)))
        for name, (_, service, complexity) in FREELANCE.items():
            group = [r for r in projects if name in r.get("categories", [])]
            new = [r for r in group if published_in(r, start, end, config["timezone"])]
            old = [r for r in previous.values() if r.get("kind") == "project" and name in r.get("categories", []) and published_in(r, prior_start, prior_end, config["timezone"])]
            contracts_group = [r for r in contracts if name in r.get("categories", [])]
            bids = sorted(r["bids"] for r in group if r.get("bids") is not None)
            bid_median = bids[len(bids) // 2] if bids else None
            # Comparable coverage must include the project source on every day in BOTH windows.
            project_sources = {r["source"] for r in group + list(previous.values()) if r.get("kind") == "project"}
            def covered(scans):
                return all(s.get("health", {}).get(source, {}).get("status") == "ok" for s in scans for source in project_sources)
            comparable = (len(selected) == expected and len(previous_scans) == (prior_end - prior_start).days + 1
                          and covered(selected) and covered(previous_scans))
            growth = round(((len(new) / len(selected)) / (len(old) / len(previous_scans)) - 1) * 100, 1) if comparable and old else None
            sources = len({r["source"] for r in group + contracts_group})
            priority = min(50, len(new) * 2) + min(20, len(group)) + min(15, sources * 5)
            if bid_median is not None and group:
                priority += max(0, 15 - min(15, bid_median))
            examples = sorted(group, key=lambda r: (r.get("published_at") or "", -(r.get("bids") or 0)), reverse=True)[:config["report"]["examples_per_category"]]
            category = {"name": name, "count": len(group), "new_posts": len(new), "contracts": len(contracts_group),
                        "growth_pct": growth, "median_bids": bid_median, "budget_groups": budget_summary(group),
                        "examples": examples, "contract_examples": contracts_group[:3], "priority": priority,
                        "confidence": "medium" if len(new) >= 10 and sources >= 2 else ("low" if group or contracts_group else "no data"),
                        "service": service, "complexity": complexity,
                        "skills": dict(Counter(s for r in group for s in r.get("skills", [])).most_common(8))}
            report["categories"].append(category)
            if group or contracts_group:
                report["opportunities"].append({"name": name, "priority": priority, "confidence": category["confidence"],
                    "proposal": "Offer to " + service + ".", "evidence": f"{len(new)} dated posts in this window; {len(group)} distinct active projects observed; {len(contracts_group)} separate contract listings.",
                    "complexity": complexity, "monetization": "Quote against the actual scope and currency in the linked briefs. Advertised budgets are not accepted contract values.",
                    "mvp": "Build one relevant portfolio example and a clearly scoped service package with deliverables and exclusions.",
                    "validation": "Review 10 recent briefs, contact suitable clients through their platform, and test a small paid engagement before specializing.",
                    "risk": "Check posting recency, bid competition and client credibility. Search-index leads and seller gigs do not count as verified buyer demand.",
                    "sources": [{"title": r["title"], "url": r["url"]} for r in (examples + contracts_group)[:3]]})
        report["opportunities"].sort(key=lambda r: -r["priority"])
    report["opportunities"] = report["opportunities"][:config["report"]["top_opportunities"]]
    return report
