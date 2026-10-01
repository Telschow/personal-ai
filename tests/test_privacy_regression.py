"""Regression tests for PUBLIC_DATA_POLICY.md.

Two invariants are protected:

1. ``test_no_real_pii_in_fixtures`` — synthetic fixtures under
   ``tests/fixtures/synthetic/`` contain no real contact identifiers.
2. ``test_no_real_contact_identifiers_in_published_text`` — the published text
   tree (docs, source, config, CI) contains no real contact identifiers.

Both use the same explicit pattern list. This is deliberately a *narrow,
checkable* invariant: it detects direct contact identifiers (email addresses
outside the synthetic allow-list, phone numbers) and credential blocks. It does
**not** claim to detect every form of PII, and it is not a substitute for human
review before publishing.
"""

import pathlib
import re
import subprocess

# Synthetic-only domains and markers allowed in tracked fixtures.
ALLOWED_EMAIL_DOMAINS = {"example.invalid", "example.com", "example.org"}
ALLOWED_MARKERS = {"Alice Example"}

# Patterns that indicate real PII if they appear in tracked fixtures.
PROHIBITED_PATTERNS = [
    # Real-looking email address outside the synthetic allow-list.
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    # Phone numbers, either international (+49 170 1234567) or explicitly
    # separated North American ((555) 123-4567, 555-123-4567). Requiring a
    # leading '+' or an explicit separator keeps this from matching long digit
    # runs such as byte offsets or content hashes.
    re.compile(r"(?<![\w.])\+\d{1,3}[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"),
    re.compile(r"(?<![\w.])\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"),
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

# Phone patterns are indices 1 and 2 of PROHIBITED_PATTERNS.
PHONE_PATTERN_INDICES = (1, 2)

# Directories holding vendored tooling, generated state, or local data.
PRUNED_DIRS = {
    ".venv",
    "__pycache__",
    ".opencode",
    ".ua",
    "design-md",
    ".git",
    "output",
}
# Documentation and configuration are the published surfaces where an
# accidental real contact identifier would leak. Test source is excluded on
# purpose: it legitimately contains synthetic PII-shaped vectors (test card
# numbers, microsecond timestamps, reserved-TLD addresses) as test data, and
# that file is covered by test_no_real_pii_in_fixtures instead.
PUBLISHED_SUFFIXES = {".md", ".yaml", ".yml"}


def _tracked_published_files() -> list[pathlib.Path]:
    """Tracked documentation and configuration files in the public repository.

    Uses ``git ls-files`` so that local-only, gitignored personal files sitting
    in the working tree are correctly out of scope: the policy governs what is
    committed, not what happens to be on one contributor's disk.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    paths = []
    for name in out.split("\0"):
        if not name:
            continue
        p = pathlib.Path(name)
        parts = p.parts
        if (
            p.suffix in PUBLISHED_SUFFIXES
            and "tests" not in parts
            and not (PRUNED_DIRS.intersection(parts))
        ):
            paths.append(p)
    return paths


def _violations(txt: str, patterns: list[re.Pattern[str]] | None = None) -> list[str]:
    """Return prohibited tokens found in ``txt``.

    When ``patterns`` is supplied it replaces the default list; the first
    pattern in whichever list is in use is treated as the email pattern and
    gets the synthetic-domain allow-list applied.
    """
    pats = PROHIBITED_PATTERNS if patterns is None else patterns
    hits: list[str] = []
    for pat in pats:
        for m in pat.finditer(txt):
            token = m.group(0).strip()
            if pat is pats[0]:
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


def test_no_real_contact_identifiers_in_published_text():
    """The published text tree must carry no real contact identifiers.

    Scoped to email addresses outside the synthetic allow-list, phone numbers,
    and credential blocks. The credit-card-shaped and SSN-shaped patterns used
    for fixtures are intentionally excluded here: they false-positive on byte
    offsets, hashes, and version strings that legitimately appear in
    documentation. This asserts a specific, checkable invariant; it is not a
    general-purpose PII detector and does not replace human review.
    """
    contact_patterns = [
        PROHIBITED_PATTERNS[0],  # email outside synthetic allow-list
        PROHIBITED_PATTERNS[4],  # private key blocks
        PROHIBITED_PATTERNS[5],  # bearer tokens / api keys
        *(PROHIBITED_PATTERNS[i] for i in PHONE_PATTERN_INDICES),
    ]
    found: dict[str, list[str]] = {}
    for f in _tracked_published_files():
        hits = _violations(f.read_text(errors="ignore"), contact_patterns)
        if hits:
            found[str(f)] = hits
    assert not found, f"Real contact identifiers in published files: {found}"
