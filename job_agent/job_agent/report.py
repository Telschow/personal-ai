"""Report / digest writers.

Artifacts humans inspect: the daily shortlist markdown. These functions never
log content — they *write* the markdown the user reads.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .models import JobMatch


def _clean_description(text: str, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:limit]


def write_markdown(matches: list[JobMatch], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Personal Job Agent – Daily Shortlist", ""]
    for m in sorted(matches, key=lambda x: x.score.total, reverse=True):
        j, s = m.job, m.score
        lines += [
            f"## {s.total:.1f}/100 — {j.title} · {j.company}",
            f"{j.location} · [{j.url}]({j.url})",
            "",
            f"**Decision:** {s.decision}",
            f"**Why:** {'; '.join(s.reasons) or '—'}",
            f"**Gaps:** {'; '.join(s.gaps) or '—'}",
            "",
            _clean_description(j.description),
            "",
        ]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def write_digest(matches: list[JobMatch], directory: str, summary: dict | None = None) -> str:
    """Write a dated digest file and return its path."""
    Path(directory).mkdir(parents=True, exist_ok=True)
    date_slug = datetime.now().strftime("%Y-%m-%d")
    path = str(Path(directory) / f"daily-{date_slug}.md")
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    lines = ["# Personal Job Agent – Daily Digest", f"Date: {date_slug}", ""]
    if summary:
        lines += ["## Executive summary", ""]
        lines += [f"- {k}: {v}" for k, v in summary.items()]
        lines += [""]
    lines += ["## Top opportunities", ""]
    top = sorted(matches, key=lambda x: x.score.total, reverse=True)[:20]
    for m in top:
        j, s = m.job, m.score
        salary = _salary_line(j)
        lines += [
            f"## {s.total:.1f}/100 — {j.title} · {j.company}",
            f"- Location: {j.location or 'n/a'} · Mode: {j.remote_mode or 'unknown'}",
            f"- Compensation: {salary or 'n/a'} · Source: {j.source}",
            f"- URL: {j.apply_url or j.url}",
            "",
            f"**Decision:** {s.decision} ({s.confidence:.2f} confidence)",
            f"**Why:** {'; '.join(s.reasons) or '—'}",
            f"**Gaps:** {'; '.join(s.gaps) or '—'}",
            "",
            _clean_description(j.description),
            "",
        ]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return path


def _salary_line(j) -> str:
    parts = []
    if j.salary_min_eur is not None:
        parts.append(f"{j.salary_min_eur:,.0f}€")
    if j.salary_max_eur is not None and j.salary_max_eur != j.salary_min_eur:
        parts.append(f"{j.salary_max_eur:,.0f}€")
    if not parts and j.salary_currency and (j.salary_min is not None or j.salary_max is not None):
        parts.append(f"{j.salary_currency} {j.salary_min or '?'}-{j.salary_max or '?'}")
    if not parts:
        parts.append("not published")
    if j.salary_converted:
        parts.append("(approx FX)")
    return "–".join(parts)


def write_json(matches: list[JobMatch], path: str) -> None:
    Path(path).write_text(
        json.dumps([m.model_dump(mode="json") for m in matches], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
