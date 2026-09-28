"""Readable PDFs plus machine-readable evidence and reports."""
from __future__ import annotations

import csv
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether

from .core import write_json

NAVY = colors.HexColor("#14273F")
TEAL = colors.HexColor("#007F87")
LIGHT = colors.HexColor("#EEF4F7")


def fonts():
    choices = [(Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
               (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf"))]
    for regular, bold in choices:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("Research", str(regular)))
            pdfmetrics.registerFont(TTFont("ResearchBold", str(bold)))
            pdfmetrics.registerFontFamily("Research", normal="Research", bold="ResearchBold", italic="Research", boldItalic="ResearchBold")
            return "Research", "ResearchBold"
    return "Helvetica", "Helvetica-Bold"


def pdf_report(report, path):
    regular, bold = fonts()
    styles = getSampleStyleSheet()
    for name in styles.byName:
        styles[name].fontName = regular
    styles.add(ParagraphStyle("Cover", fontName=bold, fontSize=29, leading=35, textColor=NAVY, spaceAfter=16))
    styles.add(ParagraphStyle("Section", fontName=bold, fontSize=16, leading=21, textColor=NAVY, spaceBefore=15, spaceAfter=9, keepWithNext=True))
    styles.add(ParagraphStyle("Sub", fontName=bold, fontSize=11, leading=15, textColor=TEAL, spaceBefore=10, spaceAfter=6, keepWithNext=True))
    styles.add(ParagraphStyle("Copy", fontName=regular, fontSize=9, leading=13, textColor=NAVY, spaceAfter=7, splitLongWords=True))
    styles.add(ParagraphStyle("SmallCopy", parent=styles["Copy"], fontSize=7.5, leading=10))
    story = []

    def p(value, style="Copy"):
        return Paragraph(escape(str(value)), styles[style])

    def add(value, style="Copy"):
        story.append(p(value, style))

    def link(title, url):
        if not url.startswith(("https://", "http://")):
            return p(title, "SmallCopy")
        return Paragraph(f'<link href={quoteattr(url)} color="#007F87">{escape(str(title))}</link>', styles["SmallCopy"])

    def table(headers, rows, widths):
        data = [[p(h, "SmallCopy") for h in headers]] + [[p(v, "SmallCopy") for v in row] for row in rows]
        item = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        item.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                  ("LINEBELOW", (0, 0), (-1, 0), 0.7, TEAL), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                                  ("TOPPADDING", (0, 0), (-1, -1), 6), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")])]))
        story.append(item)

    title = "B2C App Opportunities" if report["track"] == "apps" else "Freelance Demand & Opportunities"
    add("TRENDSCAN  /  MARKET RESEARCH", "Sub")
    add(title, "Cover")
    add(f"{report['period'].upper()}  |  {report['start']} to {report['end']}", "Section")
    add(f"Prepared {report['as_of']}  |  Markets: {', '.join(m.upper() for m in report['markets'])}")
    if report.get("quick_scan"):
        add("INTEGRATION SAMPLE: reduced collection budgets. Do not treat this as full market coverage.", "Sub")
    add(f"Coverage: {report['scan_days']} of {report['expected_days']} expected daily snapshots; previous comparison window: {report['previous_scan_days']} snapshots.")
    if report["partial_window"]:
        add("PARTIAL HISTORY: this period is incomplete. Missing days are unknown, not zero demand. Weekly and monthly windows exclude today's scan.", "Sub")
    if report["track"] == "apps":
        add(f"{report['app_count']} distinct store apps, {len(report['revenue_observations'])} attributed revenue observations, and {len(report['research_leads'])} displayed research leads.")
        add("Grossing-chart presence is a monetization signal, not an earnings figure. Paid price, in-app purchases, ratings and installs do not establish revenue or profit. Publisher disclosures and third-party estimates are labeled separately.")
    else:
        add(f"{report['project_count']} unique live project listings observed; {report['contract_count']} separate contract-job listings. {len(report['indexed_leads'])} displayed search leads; {report['seller_count']} seller listings.")
        add("Buyer projects, contract employment, indexed leads and seller competition are separate evidence types. Advertised budgets are not actual freelancer earnings. One project may appear in several relevant categories.")
    add("Decision brief", "Section")
    if not report["opportunities"]:
        add("There is not enough collected evidence in this window to recommend a niche. Review source coverage at the end of this report.")
    for n, opp in enumerate(report["opportunities"][:5], 1):
        add(f"{n}. {opp['name']} — research priority {opp['priority']}; confidence {opp['confidence']}", "Sub")
        add(opp["proposal"] + " " + opp["evidence"])

    story.append(PageBreak())
    add("Category coverage", "Section")
    if report["track"] == "apps":
        table(["Category", "Apps", "Monetization signals", "Grossing", "Confidence"],
              [[c["name"], c["count"], c["monetization_signals"], c["grossing_apps"], c["confidence"]] for c in report["categories"]], [180, 40, 105, 55, 105])
    else:
        table(["Category", "Observed projects", "Dated new posts", "Contracts", "Change*"],
              [[c["name"], c["count"], c["new_posts"], c["contracts"], "Unknown" if c["growth_pct"] is None else f"{c['growth_pct']:+g}%"] for c in report["categories"]], [185, 80, 80, 60, 80])
        add("*Change compares dated posts per scan day only when both windows have complete, healthy project-source coverage. Sources and queries cover a sample of the market.", "SmallCopy")

    add("Opportunities to validate", "Section")
    for n, opp in enumerate(report["opportunities"], 1):
        add(f"{n:02d}  {opp['name']}", "Sub")
        add(f"Priority {opp['priority']} | Confidence: {opp['confidence']} | Effort: {opp['complexity']}")
        for key in ("proposal", "evidence", "monetization", "mvp", "validation", "risk"):
            add(f"{key.replace('_', ' ').capitalize()}: {opp[key]}")
        for source in opp["sources"]:
            story.append(link(source["title"], source["url"]))

    if report["track"] == "apps":
        add("Launch-age watchlists", "Section")
        for label, key in [("New: up to 6 months", "newly_released"), ("Young: 6 months to 2 years", "young_apps"), ("Established: over 2 years", "established_apps")]:
            add(label, "Sub")
            if not report[key]:
                add("No app with a known release date in this cohort was collected.")
            for row in report[key][:10]:
                story.append(link(f"{row['title']} | {row['store']} | {row['age_days']} days old | {row['revenue_evidence']}", row["url"]))
        add("Attributed revenue evidence", "Section")
        if not report["revenue_observations"]:
            add("No attributable revenue figures were imported for this window. This report does not estimate revenue by multiplying downloads by price.")
        for row in report["revenue_observations"]:
            add(f"{row['title']}: {row['revenue_amount']:,.2f} {row['revenue_currency']} | {row['period_start']}–{row['period_end']} | {row['revenue_evidence']}", "Sub")
            add(row.get("methodology") or "Provider methodology not supplied; treat the figure cautiously.")
            story.append(link("Revenue source", row["source_url"]))

    story.append(PageBreak())
    add("Evidence by category", "Section")
    for category in report["categories"]:
        add(category["name"], "Sub")
        if not category["count"] and not category.get("contracts"):
            add("No matching observations in this window. This is a coverage gap, not proof of absent demand.")
            continue
        if report["track"] == "apps":
            pain = category["pains"]
            add(f"Review sample: {pain['sample_size']}. Complaint themes: " + (", ".join(f"{k} ({v})" for k, v in pain["themes"].items()) or "insufficient review evidence") + ". Reviews are a small recent sample, not a representative customer survey.")
            for row in category["examples"]:
                story.append(link(row["title"] + " — " + row["store"], row["url"]))
                add(f"Markets observed: {', '.join(row['markets_seen'])}; displayed metrics: {row['country'].upper()}. Released: {(row.get('released_at') or 'unknown')[:10]}. Price: {row.get('price')} {row.get('currency') or ''}; ratings: {row.get('ratings', 'unknown')}; stars: {row.get('rating', 'unknown')}. Evidence: {row['revenue_evidence']}.", "SmallCopy")
                delta = "unknown" if row["ratings_delta"] is None else f"{row['ratings_delta']:+,}"
                rank = "unknown" if row["best_comparable_rank_gain"] is None else str(row["best_comparable_rank_gain"])
                add(f"Research priority {row['score']}; score components {row['score_parts']}. Rating-count change: {delta}; best comparable chart-rank improvement: {rank}. Last observed: {row['observed_at'][:10]}.", "SmallCopy")
                if row.get("description"):
                    add(row["description"][:300], "SmallCopy")
                for observation in row.get("revenue_observations", []):
                    story.append(link(f"{observation['revenue_evidence']}: {observation['revenue_amount']:,.2f} {observation['revenue_currency']} ({observation['period_start']} to {observation['period_end']})", observation["source_url"]))
                charts = row.get("charts", [])[:3]
                if charts:
                    add("Chart observations: " + "; ".join(f"#{c['rank']} {c['chart']} / {c['category']} / {c['country']}" for c in charts), "SmallCopy")
        else:
            add(f"Suggested starter service: {category['service']}. Effort: {category['complexity']}. Median observed bids: {category['median_bids'] if category['median_bids'] is not None else 'unknown'}.")
            add("Common requested skills: " + (", ".join(f"{k} ({v})" for k, v in category["skills"].items()) or "not supplied"))
            for budget in category["budget_groups"]:
                add(f"{budget['currency']} / {budget['unit']}: median advertised upper budget {budget['median_advertised_max']:,.2f}, sample {budget['n']}; upper-budget range {budget['min_advertised_max']:,.2f}–{budget['max_advertised_max']:,.2f}.", "SmallCopy")
            for row in category["examples"] + category["contract_examples"]:
                story.append(link(row["title"] + " — " + row["platform"], row["url"]))
                add(f"Type: {row['kind']}; posted: {(row.get('published_at') or 'unknown')[:10]}; last observed: {row['observed_at'][:10]}; budget {row.get('budget_min', 'unknown')}–{row.get('budget_max', 'unknown')} {row.get('currency') or ''} / {row.get('budget_unit', 'unspecified')}; bids: {row.get('bids', 'unknown')}.", "SmallCopy")
                add(row.get("description", "")[:450], "SmallCopy")
                if row.get("location"):
                    add("Eligibility/location: " + row["location"], "SmallCopy")

    add("Further research leads", "Section")
    add("These links need direct verification. They are not counted as verified revenue or live marketplace demand.")
    for row in report.get("research_leads", report.get("indexed_leads", [])):
        story.append(link(row["title"], row["url"]))
        add(f"Source: {row['source']} | {row.get('evidence', 'unverified')} | {', '.join(row.get('categories', []))}", "SmallCopy")
    if report.get("seller_examples"):
        add("Seller competition, not buyer demand", "Sub")
        for row in report["seller_examples"]:
            story.append(link(row["title"], row["url"]))

    add("Source health and limits", "Section")
    aggregate = {}
    for scan in report["health"]:
        for name, info in scan["sources"].items():
            group = aggregate.setdefault(name, {"states": set(), "ok": 0, "failed": 0, "details": set()})
            group["states"].add(info.get("status", "unknown"))
            group["ok"] += info.get("successes", 0)
            group["failed"] += info.get("failures", 0)
            if info.get("detail"):
                group["details"].add(info["detail"])
    table(["Source", "Status", "Requests OK / failed"],
          [[name, ", ".join(sorted(v["states"])), f"{v['ok']} / {v['failed']}"] for name, v in sorted(aggregate.items())], [165, 205, 115])
    for name, info in sorted(aggregate.items()):
        if info["details"]:
            add(name + ": " + "; ".join(sorted(info["details"])), "SmallCopy")
    add(report["methodology"])
    add("Coverage is bounded by public access, provider availability and the configured query/request budget. Search results may be stale. Category classification and proposed MVPs are heuristics. A first observation is not growth. Weekly and monthly reports deduplicate observations across their actual reporting windows.")
    add("App score: monetization 0–30, rating-count adoption 0–20, launch age up to two years 0–15, measured rating growth 0–15, review gaps 0–10, approachable rating scale 0–10. Freelance priority: fresh dated posts 0–50, observed projects 0–20, independent project/contract sources 0–15, lower observed bid counts 0–15. Scores do not represent probabilities.", "SmallCopy")
    add("Source metadata and short excerpts belong to their respective providers. Remotive contract listings are attributed to Remotive.com. Full collected metadata is available in the accompanying JSON/CSV artifacts.", "SmallCopy")

    def footer(canvas, doc):
        canvas.setStrokeColor(TEAL)
        canvas.line(42, 37, 553, 37)
        canvas.setFont(regular, 7)
        canvas.setFillColor(NAVY)
        canvas.drawString(42, 25, f"TrendScan | {report['period']} | {report['start']}–{report['end']}")
        canvas.drawRightString(553, 25, str(doc.page))

    path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(str(path), pagesize=(595.28, 841.89), leftMargin=48, rightMargin=48,
                                 topMargin=42, bottomMargin=52, title=title, author="TrendScan")
    document.build(story, onFirstPage=footer, onLaterPages=footer)


def export_report(report, directory):
    directory.mkdir(parents=True, exist_ok=True)
    stem = report["track"]
    write_json(directory / f"{stem}.json", report)
    pdf_report(report, directory / f"{stem}.pdf")
    with (directory / f"{stem}.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["category", "observed", "priority", "confidence"])
        for category in report["categories"]:
            writer.writerow([category["name"], category["count"], category["priority"], category["confidence"]])
    return directory / f"{stem}.pdf"
