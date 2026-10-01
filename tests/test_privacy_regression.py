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

# Patterns are named rather than addressed by list index. An earlier version of
# this file indexed into a list positionally and silently drifted by one, which
# meant the bearer-token/API-key pattern was never applied to published files
# while a credit-card-shaped pattern was applied in its place. Naming them makes
# that class of mistake impossible and keeps the labels honest.
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Phone numbers, either international (+49 170 1234567) or explicitly separated
# North American ((555) 123-4567, 555-123-4567). Requiring a leading '+' or an
# explicit separator keeps this from matching long digit runs such as byte
# offsets or content hashes.
PHONE_INTL_PATTERN = re.compile(
    r"(?<![\w.])\+\d{1,3}[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
)
PHONE_NA_PATTERN = re.compile(r"(?<![\w.])\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")

# US Social Security Number XXX-XX-XXXX
SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# 13-16 digit credit-card-like runs
CARD_LIKE_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,16}\b")

# Private key blocks
PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")

# Bearer tokens / API keys.
#
# The value may be quoted. That is not cosmetic: the real-world shape of this
# finding is JSON ("apiKey": "..."), and an earlier version of this pattern
# only accepted the unquoted YAML form, so it silently missed the exact format
# in which a live provider key was once committed in this project family. The
# character class excludes '{', so the safe "{env:VAR}" placeholder still does
# not match.
CREDENTIAL_PATTERN = re.compile(
    r"(?i)(bearer\s+[\"']?[A-Za-z0-9._\-]{16,}[\"']?"
    r"|api[_-]?key[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._\-]{16,}[\"']?)"
)

# Patterns that indicate real PII if they appear in tracked fixtures.
PROHIBITED_PATTERNS = [
    EMAIL_PATTERN,
    PHONE_INTL_PATTERN,
    PHONE_NA_PATTERN,
    SSN_PATTERN,
    CARD_LIKE_PATTERN,
    PRIVATE_KEY_PATTERN,
    CREDENTIAL_PATTERN,
]

# Applied to the published tree (docs, source, config, CI). The SSN and
# credit-card-shaped patterns are deliberately excluded here: they false-positive
# on byte offsets, hashes, and version strings that legitimately appear in
# documentation and diagrams. Everything else is narrow enough to apply
# repository-wide without noise.
PUBLISHED_PATTERNS = [
    EMAIL_PATTERN,
    PHONE_INTL_PATTERN,
    PHONE_NA_PATTERN,
    PRIVATE_KEY_PATTERN,
    CREDENTIAL_PATTERN,
]

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


def _tracked_published_files() -> list[pathlib.Path]:
    """Tracked, non-test files in the public repository.

    Test source is excluded on purpose: it legitimately contains synthetic
    PII-shaped vectors (test card numbers, microsecond timestamps, reserved-TLD
    addresses) as test data, and those files are covered by
    test_no_real_pii_in_fixtures instead.

    Every other tracked file is scanned, not just .md/.yaml/.yml. Restricting the
    scan to those three suffixes left src/, *.json, *.toml and *.example
    unchecked -- including opencode.example.json and .env.example, which are
    exactly where a credential is most likely to be pasted by mistake.

    Uses ``git ls-files`` so that local-only, gitignored personal files sitting
    in the working tree are correctly out of scope: the policy governs what is
    committed, not what happens to be on one contributor's disk. The repository
    tracks no binary and no oversized files, so every tracked file can be read
    as text without special-casing.
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
        if "tests" in parts or PRUNED_DIRS.intersection(parts):
            continue
        paths.append(p)
    return paths


def _violations(txt: str, patterns: list[re.Pattern[str]] | None = None) -> list[str]:
    """Return prohibited tokens found in ``txt``.

    When ``patterns`` is supplied it replaces the default list. The synthetic
    domain allow-list is applied to EMAIL_PATTERN wherever it appears, rather
    than to "whatever is first in the list", so the result does not depend on
    ordering.
    """
    pats = PROHIBITED_PATTERNS if patterns is None else patterns
    hits: list[str] = []
    for pat in pats:
        for m in pat.finditer(txt):
            token = m.group(0).strip()
            if (
                pat is EMAIL_PATTERN
                and token.split("@", 1)[-1].lower() in ALLOWED_EMAIL_DOMAINS
            ):
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


def test_detectors_actually_fire():
    """Every published-tree detector must reject a realistic violation.

    A privacy assertion is only worth its runtime if it can fail. This pins each
    pattern to a string it is supposed to catch, so dropping a pattern or
    letting it degrade into something that matches nothing is itself a test
    failure rather than a silent reduction in coverage.
    """
    cases = [
        # A private-looking address on a reserved, non-routable TLD: this must
        # still trip the detector, so it must NOT use an allow-listed domain.
        (EMAIL_PATTERN, "contact me at private.person@not-example.invalid"),
        (PHONE_INTL_PATTERN, "call +49 170 1234567 now"),
        (PHONE_NA_PATTERN, "call (555) 123-4567 now"),
        (PRIVATE_KEY_PATTERN, "-----BEGIN RSA PRIVATE KEY-----"),
        (CREDENTIAL_PATTERN, '"apiKey": "abcd1234efgh5678ijkl"'),
        (CREDENTIAL_PATTERN, '"api_key": "abcd1234efgh5678ijkl"'),
        (CREDENTIAL_PATTERN, "apiKey: abcd1234efgh5678ijkl"),
        (CREDENTIAL_PATTERN, "Authorization: Bearer abcdefghijklmnopqrstuvwx"),
    ]
    for pattern, sample in cases:
        assert pattern.search(sample), f"detector does not fire on: {sample}"


def test_credential_detector_accepts_env_placeholders():
    """The env-var indirection used by opencode.example.json must stay clean."""
    assert _violations('"apiKey": "{env:FREELLMAPI_API_KEY}"', PUBLISHED_PATTERNS) == []
    assert _violations('"model": "freellmapi/auto"', PUBLISHED_PATTERNS) == []


def test_synthetic_values_are_allowed():
    """The allow-list must actually admit the synthetic corpus it claims to."""
    assert _violations("write to alice@example.invalid", PUBLISHED_PATTERNS) == []
    assert (
        _violations("see bob@example.org and carol@example.com", PUBLISHED_PATTERNS)
        == []
    )


def test_no_real_contact_identifiers_in_published_text():
    """The published tree must carry no real contact identifiers or credentials.

    Covers every tracked non-test file -- docs, source, config, and CI -- not
    only documentation. Scoped to email addresses outside the synthetic
    allow-list, phone numbers, private-key blocks, and bearer/API-key
    assignments. The credit-card-shaped and SSN-shaped patterns are intentionally
    excluded here: they false-positive on byte offsets, hashes, and version
    strings that legitimately appear in documentation and Mermaid/HTML diagrams.
    This asserts a specific, checkable invariant; it is not a general-purpose PII
    detector and does not replace human review.
    """
    found: dict[str, list[str]] = {}
    for f in _tracked_published_files():
        hits = _violations(f.read_text(errors="ignore"), PUBLISHED_PATTERNS)
        if hits:
            found[str(f)] = hits
    assert not found, f"Real contact identifiers in published files: {found}"
