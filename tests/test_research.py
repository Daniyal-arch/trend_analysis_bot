import copy
import json
import tempfile
import unittest
import ssl
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from research.analysis import app_score, budget_summary, build_report, due_periods, latest_records, windows, published_in
from research.core import ROOT, Client, SourceError, canonical_url, merge_records, read_json, write_json
from research.delivery import send_digest
from research.reports import export_report
from research.scraping import PageFetcher, marketplace_links
from scrapling.parser import Selector
from research.sources import categories_for, feed_items, freelancer_project, iso_date, intelligence, peopleperhour_page, google_play, freelancer


def app(**kwargs):
    return {"id": "apple:us:1", "entity_id": "apple:1", "track": "apps", "source": "apple", "store": "App Store",
            "country": "us", "kind": "app", "title": "Example & <App>", "url": "https://apps.apple.com/app/id1",
            "observed_at": "2026-09-28T02:00:00+00:00", "categories": ["Utilities"], "ratings": 100,
            "price": 0, "revenue_evidence": "unknown", "charts": [], "reviews": [], **kwargs}


def project(**kwargs):
    return {"id": "freelancer:1", "track": "freelance", "source": "freelancer", "platform": "Freelancer", "kind": "project",
            "title": "Automation", "url": "https://www.freelancer.com/projects/1", "categories": ["AI & automation"],
            "published_at": "2026-09-28T01:00:00+00:00", "observed_at": "2026-09-28T02:00:00+00:00",
            "budget_max": 100, "budget_min": 30, "currency": "USD", "budget_unit": "project", "bids": 5, **kwargs}


def scan(day, *rows):
    return {"date": day, "schema_version": 3, "records": list(rows), "health": {"freelancer": {"status": "ok", "successes": 1}}}


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.config = read_json(ROOT / "config.json")

    def test_calendar_boundaries_and_leap_year(self):
        self.assertEqual(windows("monthly", date(2024, 3, 1)), (date(2024, 2, 1), date(2024, 2, 29), date(2024, 1, 1), date(2024, 1, 31)))
        self.assertEqual(windows("weekly", date(2026, 9, 28))[:2], (date(2026, 9, 21), date(2026, 9, 27)))
        self.assertEqual(windows("monthly", date(2026, 1, 1))[:2], (date(2025, 12, 1), date(2025, 12, 31)))
        self.assertEqual(due_periods(date(2026, 6, 1)), ["daily", "weekly", "monthly"])

    def test_merge_preserves_chart_membership(self):
        chart = {"chart": "paid", "country": "us", "category": "Utilities", "rank": 3}
        combined = merge_records([app(charts=[chart]), app(charts=[], ratings=120)])
        self.assertEqual(combined[0]["charts"], [chart])
        self.assertEqual(combined[0]["ratings"], 120)

    def test_first_observation_is_not_growth(self):
        row = app_score(app(), {}, date(2026, 9, 28))
        self.assertIsNone(row["ratings_delta"])
        self.assertEqual(row["score_parts"]["measured_growth"], 0)
        self.assertEqual(row["score_parts"]["monetization"], 0)

    def test_market_and_chart_context_must_match(self):
        old = app(charts=[{"country": "us", "category": "Utilities", "chart": "paid", "rank": 20}])
        row = app(charts=[{"country": "gb", "category": "Utilities", "chart": "paid", "rank": 2}], ratings=80)
        result = app_score(row, {old["id"]: old}, date(2026, 9, 28))
        self.assertIsNone(result["best_comparable_rank_gain"])
        self.assertEqual(result["ratings_delta"], -20)

    def test_period_deduplicates_and_excludes_outside_dates(self):
        data = [scan("2026-09-20", project(id="old")), scan("2026-09-21", project()), scan("2026-09-22", project(bids=10)), scan("2026-09-28", project(id="future"))]
        report = build_report(data, "freelance", "weekly", date(2026, 9, 28), self.config)
        self.assertEqual(report["project_count"], 1)
        self.assertTrue(report["partial_window"])
        cat = next(c for c in report["categories"] if c["name"] == "AI & automation")
        self.assertIsNone(cat["growth_pct"])
        self.assertEqual(cat["examples"][0]["bids"], 10)

    def test_currency_and_hourly_budgets_never_combined(self):
        result = budget_summary([project(), project(currency="PKR", budget_max=10000), project(budget_unit="hour", budget_max=20)])
        self.assertEqual(len(result), 3)

    def test_seller_and_index_results_not_buyer_demand(self):
        rows = [project(kind="indexed_lead"), project(id="seller", kind="seller_listing", platform="Fiverr")]
        report = build_report([scan("2026-09-28", *rows)], "freelance", "daily", date(2026, 9, 28), self.config)
        self.assertEqual(report["project_count"], 0)
        self.assertEqual(report["seller_count"], 1)
        self.assertEqual(report["opportunities"], [])

    def test_country_variants_count_as_one_app(self):
        rows = [app(), app(id="apple:gb:1", country="gb")]
        report = build_report([scan("2026-09-28", *rows)], "apps", "daily", date(2026, 9, 28), self.config)
        self.assertEqual(report["app_count"], 1)

    def test_dates_use_report_timezone(self):
        row = project(published_at="2026-09-27T22:00:00+00:00")
        self.assertTrue(published_in(row, date(2026, 9, 28), date(2026, 9, 28)))

    def test_growth_only_with_comparable_healthy_coverage(self):
        data = [scan("2026-09-27", project(published_at="2026-09-27T01:00:00Z")),
                scan("2026-09-28", project(), project(id="freelancer:2"))]
        report = build_report(data, "freelance", "daily", date(2026, 9, 28), self.config)
        category = next(c for c in report["categories"] if c["name"] == "AI & automation")
        self.assertEqual(category["growth_pct"], 100)
        data[0]["health"]["freelancer"]["status"] = "partial"
        report = build_report(data, "freelance", "daily", date(2026, 9, 28), self.config)
        self.assertIsNone(next(c for c in report["categories"] if c["name"] == "AI & automation")["growth_pct"])

    def test_retry_health_cannot_be_overwritten_as_healthy(self):
        client = Client(self.config)
        client.event("source", False)
        client.event("source", True)
        client.status("source", "ok")
        self.assertEqual(client.health["source"]["status"], "partial")

    def test_revenue_import_rejects_missing_provenance(self):
        client = Client(self.config)
        with patch.dict("os.environ", {"APP_INTELLIGENCE_URL": "https://example.com/feed"}), patch("research.sources.public_https", side_effect=lambda x: x), patch.object(client, "get", return_value={"apps": [{"amount": 100}]}):
            with self.assertRaises(SourceError):
                intelligence(client, self.config, [])

    def test_peopleperhour_uses_actual_listed_jobs_and_original_budget(self):
        state = {"freelanceJobs": {"main": {"data": [{"id": "12"}], "meta": {"total-pages": 2}}},
                 "entities": {"projects": {"12": {"attributes": {"item_type": "job", "open": True, "title": "Need automation",
                    "url": "https://www.peopleperhour.com/freelance-jobs/12", "budget": 100, "budget_converted": 10000,
                    "currency": "GBP", "project_type": "hourly", "posted_dt": "2026-09-28 12:00:00", "proposalCount": 4,
                    "client": {"email": "must-not-be-collected@example.com"}}}}}}
        rows, pages = peopleperhour_page("<script>window.PPHReact.initialState=" + json.dumps(state) + ";</script>")
        self.assertEqual(pages, 2)
        self.assertEqual(rows[0]["budget_max"], 100)
        self.assertEqual(rows[0]["budget_unit"], "hour")
        self.assertNotIn("client", rows[0])

    def test_google_parallel_enrichment_preserves_discovery_and_verifies_tls(self):
        config = copy.deepcopy(self.config)
        config["markets"] = ["us"]
        config["limits"]["google_interval_seconds"] = 0
        item = {"appId": "com.example", "title": "Test", "price": 0}
        detail = {**item, "ratings": 250, "offersIAP": True, "released": "Jan 1, 2025"}
        with patch("research.sources.APPS", {"Utilities": (6002, ["scanner"], "utility")}), patch("google_play_scraper.search", return_value=[{"appId": None}, item]), patch("google_play_scraper.app", return_value=detail), patch("google_play_scraper.reviews", return_value=([{"score": 2, "content": "Crashes on export"}], None)):
            rows = google_play(Client(config), config, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ratings"], 250)
        self.assertEqual(rows[0]["discovery"], ["scanner"])
        self.assertEqual(rows[0]["revenue_evidence"], "iap_available")
        self.assertEqual(len(rows[0]["reviews"]), 1)
        self.assertIs(ssl._create_default_https_context, ssl.create_default_context)

    def test_freelancer_excludes_closed_and_uses_recent_default_order(self):
        client = Client(self.config)
        data = {"status": "success", "result": {"projects": [
            {"id": 1, "title": "Old closed project", "status": "closed"},
            {"id": 2, "title": "Open project", "status": "active"},
            {"id": 3, "title": "Already awarded", "status": "active", "sub_status": "awarded"}]}}
        with patch("research.sources.FREELANCE", {}), patch.object(client, "get", return_value=data) as get:
            rows = freelancer(client, self.config, [])
        self.assertEqual([r["id"] for r in rows], ["freelancer:2"])
        self.assertNotIn("reverse_sort", get.call_args.kwargs["params"])

    def test_scrapling_links_are_scoped_to_real_project_paths(self):
        page = Selector('<a href="https://evil.example/jobs/test/123/">Wrong domain project</a>'
                        '<a href="/jobs/build-a-dashboard/123/">Build a sales dashboard</a>'
                        '<a href="/d/jobs/">Browse all jobs</a>'
                        '<a href="/jobs/build-a-dashboard/123/">Duplicate project link</a>')
        found = marketplace_links(page, "https://www.guru.com/d/jobs/", r"/jobs/.+/\d+/?$")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["title"], "Build a sales dashboard")

    def test_scrapling_rate_limit_does_not_trigger_browser(self):
        client = Client(self.config)
        response = MagicMock(status=429)
        with patch("research.scraping.Fetcher.get", return_value=response), patch("research.scraping.DynamicFetcher.fetch") as browser:
            with self.assertRaises(SourceError):
                PageFetcher(client, self.config).get("test", "https://example.com")
        browser.assert_not_called()

    def test_scrapling_browser_budget_is_shared_between_collectors(self):
        client = Client(self.config)
        config = copy.deepcopy(self.config)
        config["scraping"]["max_browser_pages"] = 1
        response = MagicMock(status=200, html_content="expected")
        with patch.object(client, "pace"), patch("research.scraping.Fetcher.get", side_effect=TimeoutError), patch("research.scraping.DynamicFetcher.fetch", return_value=response) as browser:
            PageFetcher(client, config).get("one", "https://example.com")
            with self.assertRaisesRegex(SourceError, "budget"):
                PageFetcher(client, config).get("two", "https://example.org")
        self.assertEqual(browser.call_count, 1)

    def test_parsers_and_classification(self):
        self.assertIn("AI & automation", categories_for("Need n8n automation"))
        self.assertNotIn("Design & UX", categories_for("Linux migration"))
        self.assertEqual(iso_date("Jan 2, 2025")[:10], "2025-01-02")
        items = list(feed_items("<rss><channel><item><title>A &amp; B</title><link>https://example.com</link><pubDate>Mon, 28 Sep 2026 00:00:00 GMT</pubDate></item></channel></rss>"))
        self.assertEqual(items[0]["title"], "A & B")
        p = freelancer_project({"id": 1, "title": "Build app", "type": "hourly", "currency": {"code": "USD"}, "budget": {"maximum": 50}})
        self.assertEqual(p["budget_unit"], "hour")

    def test_roundtrip_compressed_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json.gz"
            write_json(path, {"records": [app()]})
            self.assertEqual(read_json(path)["records"][0]["title"], app()["title"])

    def test_pdf_renders_untrusted_text_and_empty_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            for track in ("apps", "freelance"):
                report = build_report([scan("2026-09-28", app(), project())], track, "daily", date(2026, 9, 28), self.config)
                path = export_report(report, Path(directory))
                self.assertTrue(path.read_bytes().startswith(b"%PDF-"))
                empty = build_report([], track, "monthly", date(2026, 9, 28), self.config)
                export_report(empty, Path(directory) / "empty")

    def test_email_requires_credentials_and_exactly_two_pdfs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for track in ("apps", "freelance"):
                write_json(root / f"{track}.json", {"start": "2026-09-28", "end": "2026-09-28", "scan_days": 1, "expected_days": 1,
                           "app_count": 1, "project_count": 1, "contract_count": 0, "opportunities": []})
                (root / f"{track}.pdf").write_bytes(b"%PDF-test")
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "SMTP_PASSWORD"):
                    send_digest(self.config, "daily", root, root / "ledger.json")
            env = {"SMTP_HOST": "smtp.example.com", "SMTP_USERNAME": "test@example.com", "SMTP_PASSWORD": "test"}
            with patch.dict("os.environ", env, clear=True), patch("research.delivery.smtplib.SMTP") as smtp:
                smtp.return_value.__enter__.return_value.send_message.return_value = {}
                self.assertTrue(send_digest(self.config, "daily", root, root / "ledger.json"))
                message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
                self.assertEqual(len(list(message.iter_attachments())), 2)
                self.assertFalse(send_digest(self.config, "daily", root, root / "ledger.json"))
                self.assertEqual(smtp.call_count, 1)


if __name__ == "__main__":
    unittest.main()
