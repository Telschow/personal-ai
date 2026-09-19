"""Sitemap-based job discovery.

Fetch an XML sitemap, extract page URLs, and parse each candidate page as a
structured ``JobPosting``. Falls back to the DirectPageSource normalizer for
any page that parses.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from .logging_setup import get_logger
from .models import Job
from .sources import DEFAULT_TIMEOUT, UA, SourceKind, StructuredPageSource

log = get_logger("sitemap")


class SitemapJobSource(StructuredPageSource):
    name = "sitemap"
    kind = SourceKind.STRUCTURED_PAGE

    def __init__(self, sitemap_url: str, max_pages: int = 100) -> None:
        self.sitemap_url = sitemap_url
        self.max_pages = max_pages

    def fetch(self) -> list[Job]:
        resp = httpx.get(self.sitemap_url, headers={"User-Agent": UA}, timeout=DEFAULT_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        urls = [x.text for x in root.iter() if x.tag.endswith("loc") and x.text]
        jobs: list[Job] = []
        for u in urls[: self.max_pages]:
            try:
                jobs.extend(DirectPageSourceProxy(u).fetch())
            except Exception as exc:  # noqa: BLE001 — per-page isolation
                log_event("sitemap_page_failed", url=u[:120], error=str(exc)[:120])
        return jobs


class DirectPageSourceProxy(StructuredPageSource):
    """Re-export wrapper to keep sitemap decoupled from direct imports."""

    name = "sitemap_direct"

    def __init__(self, url: str) -> None:
        self.url = url

    def fetch(self) -> list[Job]:
        from .sources import DirectPageSource

        return DirectPageSource(self.url).fetch()


def log_event(event: str, **fields: object) -> None:
    from .logging_setup import log_event as _le

    _le(log, event, **fields)
