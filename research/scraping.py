"""Scrapling public-page transport and conservative, source-linked extraction."""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse

from scrapling.fetchers import DynamicFetcher, Fetcher
from scrapling.parser import Selector

from .core import SourceError, canonical_url, clean

# Scrapling's HTTP logger can include the complete URL. Only public URLs enter
# this adapter, but keep operational logs concise and preserve our health ledger.
logging.getLogger("scrapling").setLevel(logging.WARNING)


class PageFetcher:
    def __init__(self, client, config):
        self.client = client
        self.settings = config.get("scraping", {})

    def get(self, source, url, *, params=None, required_text=None):
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        self.client.pace(urlparse(url).hostname, 2)
        failure = "HTTP page unavailable"
        try:
            page = Fetcher.get(url, impersonate="chrome", timeout=self.client.limits["timeout_seconds"],
                               retries=0, verify=True, stealthy_headers=False)
            if page.status == 200 and (not required_text or required_text in page.html_content):
                self.client.event(source, True)
                return page
            failure = f"HTTP {page.status} or missing page data"
            self.client.event(source, False)
            # A rate limit requires backing off, not more browser traffic.
            if page.status == 429:
                raise SourceError("Rate limited; public-page collection stopped")
        except SourceError:
            raise
        except Exception as exc:
            self.client.event(source, False)
            failure = type(exc).__name__
        if not self.settings.get("browser_fallback", False):
            raise SourceError(failure)
        with self.client.lock:
            if self.client.browser_pages >= self.settings.get("max_browser_pages", 8):
                raise SourceError("Browser page budget exhausted")
            self.client.browser_pages += 1
        self.client.pace(urlparse(url).hostname, 3)
        try:
            system_chrome = bool(shutil.which("google-chrome") or shutil.which("google-chrome-stable")
                                 or Path("C:/Program Files/Google/Chrome/Application/chrome.exe").exists()
                                 or Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe").exists())
            page = DynamicFetcher.fetch(url, headless=True, disable_resources=True,
                timeout=self.settings.get("browser_timeout_seconds", 40) * 1000,
                network_idle=False, google_search=False, real_chrome=system_chrome)
            if page.status != 200 or (required_text and required_text not in page.html_content):
                raise SourceError("Rendered page is unavailable or lacks expected public data")
            self.client.event(source, True)
            self.client.status(source, "partial", "Collected through Scrapling browser fallback after HTTP failure")
            return page
        except Exception as exc:
            self.client.event(source, False)
            raise SourceError("Browser fallback failed: " + type(exc).__name__) from None


def script_texts(html):
    """DOM parsing tolerates whitespace and script ordering changes."""
    return Selector(html).css("script::text").getall()


def marketplace_links(page, base_url, pattern):
    """Discovery only: a public anchor does not verify a live project or budget."""
    domain = urlparse(base_url).hostname.removeprefix("www.")
    seen = set()
    result = []
    for anchor in page.css("a[href]"):
        url = canonical_url(urljoin(base_url, anchor.attrib.get("href", "")))
        host = (urlparse(url).hostname or "").removeprefix("www.")
        if host != domain or url in seen or not re.search(pattern, urlparse(url).path):
            continue
        title = clean(" ".join(anchor.xpath(".//text()").getall()), 250)
        if len(title) < 12 or title.lower() in {"view project", "apply for this job", "view details"}:
            continue
        seen.add(url)
        result.append({"title": title, "url": url})
    return result
