import pathlib
import re

# Synthetic-only domains and markers allowed in tracked fixtures.
ALLOWED_EMAIL_DOMAINS = {"example.invalid", "example.com", "example.org"}
ALLOWED_MARKERS = {"Alice Example"}

# Patterns that indicate real PII if they appear in tracked fixtures.
PROHIBITED_PATTERNS = [
    # Real-looking email address outside the synthetic allow-list.
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    # North American phone numbers, e.g. +1-555-123-4567, (555) 123-4567, 555.123.4567
    re.compile(r"\+?\d{1,3}[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"),
    # US Social Security Number XXX-XX-XXXX
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    # 13-16 digit credit-card-like runs
    re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    # Private key blocks
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # Bearer tokens / API keys
    re.compile(
        r"(?i)(bearer\s+[A-Za-z0-9._\-]{16,}|api[_-]?key\s*[:=]\s*[A-Za-z0-9._\-]{16,})"
    ),
]


def _violations(txt: str) -> list[str]:
    hits: list[str] = []
    for pat in PROHIBITED_PATTERNS:
        for m in pat.finditer(txt):
            token = m.group(0).strip()
            if pat is PROHIBITED_PATTERNS[0]:
                domain = token.split("@", 1)[1].lower()
                if domain in ALLOWED_EMAIL_DOMAINS:
                    continue
            hits.append(token)
    return hits


def test_env_not_tracked():
    gitignore = pathlib.Path(".gitignore").read_text()
    assert ".env" in gitignore


def test_private_dirs_ignored():
    gitignore = pathlib.Path(".gitignore").read_text()
    for d in ["data/", "Financial_data/", "previous_project_and_raw_data/"]:
        assert d in gitignore


def test_no_real_pii_in_fixtures():
    fixtures_dir = pathlib.Path("tests/fixtures/synthetic")
    found: dict[str, list[str]] = {}
    for f in fixtures_dir.rglob("*"):
        if not f.is_file():
            continue
        txt = f.read_text(errors="ignore")
        hits = _violations(txt)
        if hits:
            found[str(f)] = hits
    assert not found, f"Real PII detected in fixtures: {found}"
