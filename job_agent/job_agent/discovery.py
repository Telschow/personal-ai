"""Source construction from typed configuration.

Wire-only module: turns ``Config.sources`` into a list of :class:`Source`
instances. No network or DB access here.
"""

from __future__ import annotations

from .config import Config, SourceEntry
from .sitemap import SitemapJobSource
from .sources import (
    AshbySource,
    DirectPageSource,
    GreenhouseSource,
    LeverSource,
    RssJsonAdapter,
    SmartRecruitersSource,
    Source,
    WorkableSource,
)

_EXAMPLE_HINTS = ("example", "your-", "sample")


def _is_example(value: str | None) -> bool:
    if not value:
        return True
    return value.lower().startswith(_EXAMPLE_HINTS)


def _entry_value(entry: SourceEntry, field: str) -> str | None:
    return getattr(entry, field)


def build_sources(cfg: Config, *, sources_filter: set[str] | None = None) -> list[Source]:
    """Build all configured, non-example sources.

    ``sources_filter`` restricts to the given source names (CLI ``--source``).
    A source configured only with example values is skipped (never fails).
    """
    s = cfg.sources
    out: list[Source] = []

    def _add(src: Source) -> None:
        if sources_filter and src.name not in sources_filter:
            return
        out.append(src)

    for entry in s.greenhouse:
        if entry.token and not _is_example(entry.token):
            _add(GreenhouseSource(entry.token))
    for entry in s.lever:
        if entry.site and not _is_example(entry.site):
            _add(LeverSource(entry.site))
    for entry in s.ashby:
        if entry.board and not _is_example(entry.board):
            _add(AshbySource(entry.board))
    for entry in s.smartrecruiters:
        if entry.company and not _is_example(entry.company):
            _add(SmartRecruitersSource(entry.company))
    for entry in s.workable:
        if entry.subdomain and not _is_example(entry.subdomain):
            _add(WorkableSource(entry.subdomain))
    for entry in s.rss_json:
        if entry.url and entry.name:
            _add(RssJsonAdapter(entry.name, entry.url))
    for entry in s.sitemap:
        if entry.url:
            _add(SitemapJobSource(entry.url))
    for domain in s.direct_company_domains:
        if domain and not _is_example(domain):
            _add(DirectPageSource(domain))
    return out


def list_configured_sources(cfg: Config) -> list[dict[str, str]]:
    """Human-readable inventory for ``sources list`` (no secrets)."""
    inventory: list[dict[str, str]] = []
    for src in build_sources(cfg):
        detail = ""
        for field in ("token", "site", "board", "company", "subdomain", "url"):
            if hasattr(src, field):
                value = getattr(src, field)
                if field in ("token",) and value:
                    value = f"{value[:6]}…"  # public board token, never full
                detail = f"{field}={value}"
                break
        inventory.append({"name": src.name, "kind": src.kind.value, "target": detail})
    return inventory
