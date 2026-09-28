from __future__ import annotations

import gzip
import hashlib
import html
import ipaddress
import json
import re
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

ROOT = Path(__file__).resolve().parents[1]


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def clean(value, limit=3000):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()[:limit]


def canonical_url(url):
    p = urlparse(str(url or ""))
    if p.scheme not in ("http", "https") or not p.hostname:
        return ""
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_") and k not in {"ref", "source"}]
    return urlunparse(("https", p.netloc.lower(), p.path.rstrip("/"), "", urlencode(sorted(query)), ""))


def record(track, source, key, title, url, **fields):
    return {"id": f"{source}:{key}", "track": track, "source": source,
            "title": clean(title, 250), "url": canonical_url(url), "observed_at": utcnow(), **fields}


def stable_id(value):
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def read_json(path):
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(temporary, "wt", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=None if path.suffix == ".gz" else 2)
    temporary.replace(path)


def merge_records(rows):
    """Deduplicate within a scan while preserving discovery contexts."""
    merged = {}
    for row in rows:
        key = row["id"]
        if key not in merged:
            merged[key] = dict(row)
            continue
        old = merged[key]
        for field in ("categories", "discovery", "charts", "reviews"):
            combined = old.get(field, []) + row.get(field, [])
            if combined:
                unique = {json.dumps(x, sort_keys=True): x for x in combined}
                row = {**row, field: list(unique.values())}
        old.update({k: v for k, v in row.items() if v is not None and v != ""})
    return list(merged.values())


class SourceError(RuntimeError):
    pass


class Client:
    """Bounded retries, per-host pacing, source diagnostics; no credential logging."""

    def __init__(self, config):
        self.limits = config["limits"]
        self.deadline = time.monotonic() + self.limits["run_minutes"] * 60
        self.lock = threading.Lock()
        self.next_request = {}
        self.health = {}
        self.local = threading.local()
        self.browser_pages = 0

    def status(self, source, state, detail=""):
        with self.lock:
            row = self.health.setdefault(source, {"requests": 0, "successes": 0, "failures": 0})
            if state == "ok" and row["failures"]:
                state = "partial"
            if state in {"partial", "failed"}:
                row["collection_issue"] = True
            row.update(status=state, detail=detail)

    def event(self, source, success):
        with self.lock:
            row = self.health.setdefault(source, {"requests": 0, "successes": 0, "failures": 0})
            row["requests"] += 1
            row["successes" if success else "failures"] += 1
            row["status"] = "partial" if (row["failures"] or row.get("collection_issue")) and row["successes"] else ("ok" if row["successes"] else "failed")
            if row["requests"] % 50 == 0:
                print(f"{source}: {row['successes']} successful requests, {row['failures']} failed attempts", flush=True)

    def pace(self, host, interval):
        if time.monotonic() >= self.deadline:
            raise SourceError("run time budget exhausted")
        with self.lock:
            now = time.monotonic()
            delay = max(0, self.next_request.get(host, now) - now)
            self.next_request[host] = now + delay + interval
        if delay:
            time.sleep(delay)

    def get(self, source, url, *, params=None, headers=None, raw=False, interval=0.5):
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
            self.local.session.headers["User-Agent"] = "TrendScan/3.0 (+https://github.com/Daniyal-arch/trend_analysis_bot)"
        host = urlparse(url).hostname
        for attempt in range(self.limits["retries"]):
            self.pace(host, interval)
            try:
                response = self.local.session.get(url, params=params, headers=headers,
                                                  timeout=self.limits["timeout_seconds"])
                if response.status_code in (429, 500, 502, 503, 504):
                    self.event(source, False)
                    retry = response.headers.get("Retry-After", "")
                    delay = min(float(retry), 60) if retry.isdigit() else 2 ** (attempt + 1)
                    if attempt + 1 < self.limits["retries"]:
                        time.sleep(delay)
                        continue
                    raise SourceError(f"HTTP {response.status_code}; retries exhausted")
                if response.status_code >= 400:
                    self.event(source, False)
                    raise SourceError(f"HTTP {response.status_code}")
                result = response.text if raw else response.json()
                self.event(source, True)
                return result
            except (requests.RequestException, ValueError) as exc:
                self.event(source, False)
                if attempt + 1 == self.limits["retries"]:
                    raise SourceError(type(exc).__name__) from None
                time.sleep(2 ** attempt)
        raise SourceError("request failed")


def public_https(url):
    """Validate optional feed endpoints before accessing them."""
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise ValueError("Feed URL must use public HTTPS without embedded credentials")
    if any(not ipaddress.ip_address(a[4][0]).is_global for a in socket.getaddrinfo(p.hostname, p.port or 443)):
        raise ValueError("Feed URL must resolve to public addresses")
    return url
