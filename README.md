# TrendScan: app opportunities and freelance demand

Daily market research to help choose **a B2C app to build** and **a freelance service to sell**. Covers all configured industries, from small utilities and content services to complex products and engineering. Geospatial is one category, not the focus.

Each digest produces **two PDFs**:

1. `apps.pdf`: store competitors, monetization evidence, new/young/established apps, review complaints, categories and MVP hypotheses.
2. `freelance.pdf`: buyer projects, budgets by currency and contract type, bid competition, skills, platform coverage and service hypotheses.

The daily GitHub Action starts at **07:17 Asia/Karachi** (02:17 UTC; GitHub may delay it). Mondays also generate the **previous seven completed days**; the first of each month also generates the **previous calendar month**. Each due digest is a separate email with exactly two PDF attachments to **daniyalnahk@gmail.com**. If daily, weekly and monthly coincide, that means three emails and six PDFs.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m research run --period all
```

On Linux/macOS use `.venv/bin/python`. Python 3.12+ is supported. The first full run can take 10–40 minutes, depending on sources. No LLM API or paid data subscription is required.

For an isolated, smaller live integration run:

```powershell
.\.venv\Scripts\python.exe -m research run --quick --period all --data-dir .local-data --output-dir reports-preview
```

Quick mode still visits every category but reduces markets, result counts, details and reviews. **Do not use quick scans as production baselines.** Full collection defaults to the US, UK, Canada, Australia and Pakistan, with global freelance listings. Primary-market Apple charts cover every configured Apple genre; other markets collect the overall charts. Google Play discovery covers every configured category in each market.

## Turn on email

The default sender is also `daniyalnahk@gmail.com` using Gmail SMTP. In [repository Actions secrets](https://github.com/Daniyal-arch/trend_analysis_bot/settings/secrets/actions), **only `SMTP_PASSWORD` is required** for this default. Other SMTP providers can override the defaults:

| Secret | Gmail example |
|---|---|
| `SMTP_HOST` | Optional override; defaults to `smtp.gmail.com` |
| `SMTP_PORT` | `587` (optional; default 587; 465 uses implicit TLS) |
| `SMTP_USERNAME` | Optional override; defaults to `daniyalnahk@gmail.com` |
| `SMTP_PASSWORD` | Sender's **app password**, not the ordinary account password |
| `SMTP_FROM` | Optional sender override |

For Gmail, enable two-step verification and create an [app password](https://support.google.com/accounts/answer/185833). Some account policies do not allow app passwords; another TLS SMTP provider works too. Keep credentials in GitHub secrets or local environment variables; never commit them or paste them into reports.

Open [Actions](https://github.com/Daniyal-arch/trend_analysis_bot/actions), choose **App and freelance research**, and run with `dry_run=true` first. Enable `quick=true` for a smaller integration sample; quick mode requires dry-run and cannot overwrite production history. Download its artifacts and inspect source coverage. Run with `dry_run=false` and `quick=false` to persist full research history and send mail. The committed default-branch workflow runs automatically every day. Scheduled scans continue to save artifacts/history if email is not configured, and the delivery step fails visibly.

Reports are in `reports/YYYY-MM-DD/{daily,weekly,monthly}/`. The action uploads PDFs, report JSON, category CSVs, diagnostics and compressed evidence snapshots as artifacts retained for 90 days. Compressed snapshots and delivery receipts are committed for longitudinal analysis. SMTP delivery is recorded after acceptance to prevent routine rerun duplicates; a crash between SMTP acceptance and receipt persistence can still cause a duplicate. Delivery does not guarantee inbox placement.

## What is collected

| Source | Evidence and limits |
|---|---|
| Apple App Store | Free, paid and grossing charts; genre charts; keyword discovery; lookup metadata; recent reviews; bounded follow-up of previously seen apps |
| Google Play | Keyword discovery in every category/market; category-balanced metadata and review enrichment; install lower bounds, ratings, price, IAP and ad signals. Unofficial parser may break; failures are visible. Search order is **not** a chart ranking. |
| Freelancer | Paginated active buyer projects plus category searches; posted dates, skills, advertised budgets and observed bid counts |
| PeoplePerHour | Paginated public buyer-project listings; posted dates, original-currency budgets and proposal counts. Its public-page parser can change; failures are reported. |
| Public marketplace pages | Scrapling HTTP and bounded browser collection of Guru, Upwork, Contra and Truelancer listing links. These remain leads until availability is verified. |
| Remotive | Contract/freelance jobs only, separately labeled and attributed. Public feed can lag. |
| We Work Remotely | Contract RSS listings, kept separate from marketplace projects |
| Upwork, Guru, Contra (and supplemental PeoplePerHour discovery) | Indexed project leads through web search. No claim that snippets are live verified projects. Access varies. |
| Fiverr | Seller/service competition; **never counted as buyer demand** |
| Google News and Hacker News | Revenue stories and founder discussions to investigate; not verified earnings |
| Optional revenue feed/import | Attributed publisher-reported amounts or third-party estimates with currency, time period and source URL |

Network collectors run independently, with per-host pacing, bounded retries and time budgets. One source failing does not erase successful sources. Both primary research tracks must have observations for a normal full scan to pass. Auth gates, rate limits and empty searches are reported. The bot uses public endpoints and configured credentials; it does not bypass login, CAPTCHA or paywalls.

### Scrapling integration

[Scrapling](https://github.com/D4Vinci/Scrapling) handles public marketplace HTML fetching and DOM selection. PeoplePerHour extracts structured public project state from script nodes; other marketplace pages yield strictly filtered same-domain project links. The HTTP fetcher falls back to headless Chromium when a page is unavailable or expected data needs rendering. The run has a shared browser-page budget, request timeouts and source diagnostics. It stops on rate limits. Browser rendering does not turn a blocked page or stale listing into evidence.

The GitHub workflow installs Chromium automatically. Locally run `python -m playwright install chromium`; on Linux, `python -m playwright install --with-deps chromium` also installs system dependencies. Set `scraping.browser_fallback=false` in `config.json` for HTTP-only collection. Structured values are validated instead of inferred when a page layout changes.

### Optional access for stronger coverage

- `BRAVE_SEARCH_API_KEY`: uses Brave's search API instead of the public Bing RSS fallback. Improves controllable indexed discovery but does not verify snippets. Provider quotas/billing apply.
- `FREELANCER_TOKEN`: optional Freelancer API OAuth access.
- `APP_INTELLIGENCE_URL`: HTTPS feed in the schema described in [docs/INTELLIGENCE.md](docs/INTELLIGENCE.md), for data you are entitled to use.
- `data/imports/app-intelligence.json`: local import of the same schema. Ignored by git; use a feed for unattended Actions.
- `extra_feeds` in `config.json`: additional public/licensed **contract-only** RSS feeds.

There is no promise of access to every platform or every industry's demand. Platform coverage appears in each report so gaps cannot masquerade as quiet markets. Search defaults have an explicit query budget; increase it in `config.json` to cover more queries, within your provider quota.

## How conclusions work

App reports separate revenue evidence from proxies. A grossing chart demonstrates store monetization activity, but not its amount or profitability. Paid listings and IAP show monetization mechanisms, not transactions. Exact amounts require attributable disclosures or licensed estimates. No revenue is inferred by multiplying downloads by price.

Every configured category gets a section, including categories with no observations. Product hypotheses use competitor evidence and small review samples; they are not validated product-market fit. Products up to six months, six months to two years, and older products are shown separately when release dates are available. No age exclusion removes established products.

App research scores use explicit components for monetization, rating adoption, observed growth, young release age, review gaps and approachable scale. Storefront observations are kept separate for metric comparisons and deduplicated by app for category totals. iOS and Android editions are not automatically merged: names alone cannot establish identity.

Freelance categories rank observed freshness, project count, independent project/contract sources and bid competition. Budgets remain separated by **currency and hourly/fixed unit**. High advertised budgets are not verified spend; clients and briefs need checking. Search snippets and seller gigs never increase buyer-project counts. Multi-category projects mean category totals are not additive.

Daily compares today with yesterday. Weekly and monthly reports aggregate unique observations in their actual completed windows, not copies of today's rankings. Missing history is reported; first discovery never becomes fabricated growth. Freelance growth is suppressed unless both windows have complete, healthy project-source coverage. Early weekly/monthly reports will be incomplete or empty until enough history accumulates.

## Commands and customization

```bash
python -m research scan                       # collect and save evidence
python -m research report --period all        # generate 6 PDFs from saved history
python -m research report --period monthly --as-of 2026-10-01
python -m research email --period auto        # deliver existing due reports
python -m research run --send-email           # collect, report and deliver
python -m research scan --sources apple,freelancer
python -m unittest discover -s tests -v
```

Edit `config.json` for markets, budgets, timeouts, recipient, report sizes and source selection. Edit `research/taxonomy.py` for categories, discovery terms and starter service/product hypotheses. The default has 27 app research categories plus an uncategorized bucket and 22 freelance categories. Collection is deterministic Python; no autonomous purchasing, account creation or client outreach occurs.

The original geospatial scripts are archived in `legacy/`; their historical `snapshots/` remain intact. They are not used by the new workflow; new schema-v3 data lives in `data/snapshots/`. Use `python -m research` for the new pipeline. `scan.py` and `analyze.py` are shortcuts for the new scanner and report generator.

## Source references

- [Apple Search API documentation](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/Searching.html)
- [Apple chart feed generator](https://rss.marketingtools.apple.com/)
- [Google Play parser documentation](https://github.com/JoMingyu/google-play-scraper)
- [Freelancer API](https://developer.freelancer.com/)
- [Remotive public API](https://github.com/remotive-com/remote-jobs-api)
- [Brave web search API](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)

MIT for code. Source content remains under its provider's terms. Reports preserve attribution and evidence links.
