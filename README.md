# Geospatial trend scan

Measures what the geospatial field is **building, reading, asking about and installing** —
from every platform that permits automated access.

Every other geospatial trend report I could find is editorial: an analyst's judgement about
what matters this year. This one is measured, reproducible, and publishes its raw data. You
can disagree with the conclusions and re-run the numbers yourself.

**Latest scan:** 6,059 records — 2,377 GitHub repos across 70 topics, 405 open issues,
404 Hacker News stories, 1,813 Bluesky posts from 198 accounts, 248 Reddit posts,
300 GIS StackExchange questions, 166 arXiv papers, 48 package registries.

---

## Quick start

```bash
python scan.py            # collect everything -> snapshots/YYYY-MM-DD.json
python analyze.py         # report, with a geospatial relevance filter applied
python watchlist.py       # refresh star history for every repo ever seen
python compare.py         # what changed between two snapshots   <- the point
```

A GitHub token is optional but strongly recommended — it raises the API limit from 60 to
5,000 requests/hour. Set `GITHUB_TOKEN`, or pass it as the first argument.

---

## Why snapshots, and why the second run matters more than the first

A single scan tells you what is **big**. Big is mostly a function of age — an established
library outranks everything newer simply by having existed longer.

Comparing two scans tells you what is **rising**, which is the only part that predicts
anything. So:

| Run | What you get |
|---|---|
| 1 | Baseline. Interesting, but it is a photograph |
| 2 | First deltas — what moved in a week |
| 4+ | Acceleration; momentum becomes meaningful |

`.github/workflows/weekly.yml` runs it Mondays and commits the snapshot. Actions minutes are
unlimited on public repositories, so the whole thing costs nothing to operate.

---

## Sources

| Source | Measures | Auth |
|---|---|---|
| GitHub — 70 topics | adoption; what people build on | optional token |
| GitHub Issues — 18 pain queries | what is actually broken | optional token |
| Hacker News (Algolia) | attention **outside** the geospatial niche | none |
| Reddit — 20 subreddits | topics and problems (see caveat) | none / optional OAuth |
| GIS StackExchange | what people are stuck on, incl. **unanswered** = unmet need | none |
| arXiv — 10 queries | research direction, ~1–2 years ahead of tooling | none |
| npm · PyPI · crates.io | installs — the honest adoption number | none |
| Bluesky | social signal via public AppView | none |

`r/MapPorn` and `r/dataisbeautiful` are included deliberately — they are where visually
striking maps reach general audiences, which the practitioner sources structurally cannot see.

---

## Known limitations

Read these before quoting any number.

**The geo filter is heuristic.** `analyze.py` drops ~31% of collected repos as non-geospatial,
but it is regex over names, descriptions and topics. "mapping" still matches SLAM; "routing"
still matches network proxies. A few non-geo repos survive into the ranked lists.

**Reddit has no scores.** Reddit returns HTTP 403 to `/*.json` from datacenter IPs regardless
of User-Agent — verified across three UA formats including their own documented convention.
The public RSS feeds still serve, and that is what this uses, but RSS carries titles only. So
**Reddit contributes topic and problem signal, never ranking.** Set `REDDIT_CLIENT_ID` and
`REDDIT_CLIENT_SECRET` (free app at reddit.com/prefs/apps) to switch to the full OAuth API.

**Bluesky search needs auth.** `searchPosts` returns 403; `searchActors` and `getAuthorFeed`
are public. So it discovers geospatial accounts, then reads their feeds. Coverage is therefore
account-based, not query-based.

**Currencies are not convertible.** A GitHub star, an HN point, a Reddit upvote and a monthly
download measure different things. General-audience subreddits produce the biggest raw numbers
purely because their population is larger. **Compare within a column, never across.**

**Stars measure attention, not use**, and attention is gameable. Where a package registry
covers the same project, believe the download count.

**LinkedIn is excluded.** Its User Agreement prohibits automated collection and enforcement is
account termination. It is also a weak proxy for industry direction — it measures what
practitioners click, which skews to career content and AI news.

---

## Before enabling the schedule

**Run `workflow_dispatch` manually first.** Reddit already blocks datacenter IPs; GitHub
Actions runners are Azure datacenter IPs, so Reddit and possibly Bluesky and pypistats may
behave differently there than locally. Confirm what actually works from CI before trusting a
schedule to run unattended.

The workflow accepts a `dry_run` input that collects and reports without committing.

---

## Attribution

- **GIS StackExchange** content is CC-BY-SA. Question titles are stored as data; if you
  republish them, attribute Stack Exchange and preserve the licence.
- **GitHub, Hacker News, arXiv, npm, PyPI, crates.io, Bluesky** are accessed through their
  public APIs under their respective terms.
- **Reddit** content is accessed via public RSS feeds and remains the property of its authors.

Snapshots contain public metadata — titles, counts, handles — not personal data beyond public
usernames.

## Licence

MIT for the code. Collected data remains under the terms of its original source.
