from __future__ import annotations

import argparse
import os
import socket
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .analysis import build_report, due_periods, latest_records, snapshots
from .core import ROOT, Client, read_json, utcnow, write_json
from .delivery import send_digest
from .reports import export_report
from .sources import COLLECTORS, collect


def parser():
    p = argparse.ArgumentParser(description="B2C app and freelance opportunity research")
    p.add_argument("command", choices=["scan", "report", "run", "email"])
    p.add_argument("--config", type=Path, default=ROOT / "config.json")
    p.add_argument("--data-dir", type=Path, default=ROOT / "data")
    p.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    p.add_argument("--period", choices=["auto", "all", "daily", "weekly", "monthly"], default="auto")
    p.add_argument("--as-of", type=date.fromisoformat, help="Report date; cannot backdate live collection")
    p.add_argument("--sources", help="Comma-separated source subset")
    p.add_argument("--quick", action="store_true", help="Small live integration scan, isolated data recommended")
    p.add_argument("--send-email", action="store_true", help="Send two PDF attachments per due period")
    p.add_argument("--force-email", action="store_true", help="Explicitly resend an already delivered period")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    config = read_json(args.config)
    today = datetime.now(ZoneInfo(config["timezone"])).date()
    as_of = args.as_of or today
    if args.command in ("scan", "run") and as_of != today:
        raise ValueError("A live scan cannot be assigned a historical or future date")
    if args.sources:
        config["sources"] = args.sources.split(",")
    unknown = set(config["sources"]) - set(COLLECTORS)
    if unknown:
        raise ValueError(f"Unknown collectors: {sorted(unknown)}")
    if not config["markets"]:
        raise ValueError("Configure at least one market")
    if args.quick:
        if args.data_dir.resolve() == (ROOT / "data").resolve():
            raise ValueError("Quick scans need an isolated --data-dir, for example .local-data")
        config["markets"] = config["markets"][:1]
        config["limits"].update(chart_size=10, search_results=3, google_details=12, reviews_per_store=3,
                                reviews_per_app=5, freelancer_pages=1, freelancer_page_size=10,
                                peopleperhour_pages=2,
                                web_queries=5, news_queries=2, run_minutes=15)
    socket.setdefaulttimeout(config["limits"]["timeout_seconds"])
    snapshot_dir = args.data_dir / "snapshots"
    all_scans = snapshots(snapshot_dir)
    if args.command in ("scan", "run"):
        client = Client(config)
        history = latest_records(all_scans[-1:])
        rows = collect(client, config, history)
        # A run with only search snippets is not a successful store/project scan.
        coverage = {track: sum(r["track"] == track and r.get("kind") in ("app", "project", "contract_job") for r in rows)
                    for track in ("apps", "freelance")}
        snapshot = {"schema_version": 3, "date": today.isoformat(), "collected_at": utcnow(), "timezone": config["timezone"],
                    "markets": config["markets"], "quick": args.quick, "health": client.health, "coverage": coverage, "records": rows}
        # A failed rerun must not erase a healthy observation already saved that day.
        existing = next((s for s in all_scans if s["date"] == today.isoformat()), None)
        if existing and any(existing.get("coverage", {}).get(t, 0) > 0 and coverage[t] == 0 for t in coverage):
            write_json(args.output_dir / today.isoformat() / "failed-scan.json", snapshot)
            raise RuntimeError("Rerun lost an entire research track; retained today's prior snapshot. Diagnostics saved.")
        write_json(snapshot_dir / f"{today.isoformat()}.json.gz", snapshot)
        write_json(args.output_dir / today.isoformat() / "source-health.json", client.health)
        all_scans = [s for s in all_scans if s["date"] != today.isoformat()] + [snapshot]
        print(f"Saved {len(rows)} records; apps={coverage['apps']}, freelance={coverage['freelance']}", flush=True)
        if not rows:
            raise RuntimeError("No data collected; inspect source-health.json")
    periods = due_periods(as_of) if args.period == "auto" else (["daily", "weekly", "monthly"] if args.period == "all" else [args.period])
    if args.command in ("run", "report"):
        for period in periods:
            directory = args.output_dir / as_of.isoformat() / period
            for track in ("apps", "freelance"):
                report = build_report(all_scans, track, period, as_of, config)
                path = export_report(report, directory)
                print(f"Generated {path}", flush=True)
    if args.command in ("scan", "run"):
        required = set()
        if {"apple", "google_play"} & set(config["sources"]):
            required.add("apps")
        if {"freelancer", "peopleperhour", "remote_feeds"} & set(config["sources"]):
            required.add("freelance")
        missing = [t for t in required if coverage[t] == 0]
        if missing:
            raise RuntimeError("Research track has no primary observations: " + ", ".join(missing))
    if args.command == "email" or args.send_email:
        for period in periods:
            send_digest(config, period, args.output_dir / as_of.isoformat() / period,
                        args.data_dir / "delivery.json", args.force_email)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Known application failures contain no secret values. Other exceptions are type-only.
        message = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__
        print(f"TrendScan failed: {message}", file=sys.stderr)
        sys.exit(1)
