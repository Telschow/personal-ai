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

import os
import pathlib
import re
import subprocess

import pytest

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

# Directories that can never appear in `git ls-files`, listed so their absence
# from a scan is a deliberate decision rather than an oversight.
#
# `.opencode` was pruned here on the grounds that agent config is "local state".
# That was wrong: 25 files under .opencode/ are tracked, and an agent or command
# definition is exactly where somebody pastes a provider key. The scan covers it
# now. The policy governs what is committed, and these files are committed.
NEVER_TRACKED_DIRS = {
    ".venv",
    "__pycache__",
    ".git",
}


def _tracked_files() -> list[str]:
    """Every path tracked by git, as relative POSIX strings."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [name for name in out.split("\0") if name]


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
        if "tests" in parts or NEVER_TRACKED_DIRS.intersection(parts):
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
            if pat is CREDENTIAL_PATTERN and _is_env_reference(token):
                continue
            hits.append(token)
    return hits


def _is_env_reference(token: str) -> bool:
    """True when a credential-pattern hit is an indirection, not a secret.

    ``const apiKey = process.env.API_KEY`` and ``Bearer eyJ...`` (a truncated
    example) match the credential shape but carry no secret. Treating them as
    violations produces noise that gets suppressed by pruning whole
    directories, which is how a tracked directory ends up unscanned. The
    allow-list keeps the scan honest instead.
    """
    lowered = token.lower()
    if any(
        marker in lowered
        for marker in ("process.env", "os.environ", "getenv", "${", "{{", "$env:")
    ):
        return True
    return token.endswith(("...", "…"))


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


# =====================================================================
# Personal career policy must not ship in the repository
# =====================================================================

# Real employer names must not appear in tracked text, but a list of them in a
# public file would itself publish the names. The deny-list therefore lives
# outside the repository: one term per line in the file named by
# PERSONAL_AI_PRIVACY_DENYLIST, or in the gitignored `.privacy-denylist.local`.
# Without such a file the employer tests have nothing to check and are skipped.
_DENYLIST_ENV = "PERSONAL_AI_PRIVACY_DENYLIST"
_DENYLIST_DEFAULT = ".privacy-denylist.local"


def _local_denylist() -> list[str]:
    path = os.environ.get(_DENYLIST_ENV) or _DENYLIST_DEFAULT
    f = pathlib.Path(path)
    if not f.is_file():
        return []
    lines = f.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


# A salary figure is a personal negotiating position, not documentation. The
# detector is contextual: a pay keyword followed closely by a figure written as
# 5 or 6 digits (`95000`, `95_000`, `95.000`) or with a k suffix (`95k`). A bare
# "any six-figure number" pattern is unusable here because it flags colour
# literals and hashes.
_SALARY_CONTEXT = re.compile(
    r"(salary|gehalt|compensation|pay floor)"
    r"[^\n]{0,40}?"
    r"(?<![\w.])(\d{2,3}[ _.,]000|\d{5,6}|\d{2,3} ?[kK])(?![\w.])",
    re.IGNORECASE,
)

# Fixtures that deliberately contain a salary-shaped figure to exercise the
# memory policy, which must escalate salary statements for approval.
_SALARY_FIXTURE_DIRS = ("tests/",)


def _matches(text: str, terms: list[str]) -> list[str]:
    """Terms present as whole words (substring matching hits base64 data)."""
    return [t for t in terms if re.search(rf"\b{re.escape(t)}\b", text)]


def test_no_personal_employer_in_tracked_text():
    """No tracked file may name an employer from the local deny-list."""
    terms = _local_denylist()
    if not terms:
        pytest.skip("no local privacy deny-list configured")
    found: dict[str, list[str]] = {}
    for f in _tracked_published_files():
        hits = _matches(f.read_text(errors="ignore"), terms)
        if hits:
            found[str(f)] = hits
    assert not found, f"Deny-listed terms in tracked files: {found}"


def test_no_personal_employer_in_test_sources():
    """Test fixtures are scanned too.

    _tracked_published_files() skips anything under a tests/ directory because
    test data legitimately contains PII-shaped vectors. That exemption is
    correct for synthetic PII and wrong for a real employer name.
    """
    terms = _local_denylist()
    if not terms:
        pytest.skip("no local privacy deny-list configured")
    found: dict[str, list[str]] = {}
    for name in _tracked_files():
        if not name.endswith(".py"):
            continue
        hits = _matches(pathlib.Path(name).read_text(errors="ignore"), terms)
        if hits:
            found[name] = hits
    assert not found, f"Deny-listed terms in Python sources: {found}"


def test_no_salary_figure_in_published_files():
    """A published salary figure is a personal policy, not documentation."""
    found: dict[str, list[str]] = {}
    for f in _tracked_published_files():
        if str(f).startswith(_SALARY_FIXTURE_DIRS):
            continue
        hits = [
            m.group(0) for m in _SALARY_CONTEXT.finditer(f.read_text(errors="ignore"))
        ]
        if hits:
            found[str(f)] = hits
    assert not found, f"Salary figures in published files: {found}"


def test_salary_detector_catches_the_shapes_people_write():
    must_catch = [
        "salary floor 95000",
        "salary: 95_000",
        "Gehalt 95.000 EUR",
        "my salary is 95k",
        "pay floor: 105000",
        "compensation 120 K",
    ]
    for sample in must_catch:
        assert _SALARY_CONTEXT.search(sample), f"salary detector missed: {sample}"

    must_not_catch = [
        "salary band is confidential",
        "port 8080",
        "version 3.14.4",
        "1000 vectors",
        "salary/Gehalt/salario keywords",
        "hash 0f3a9c1d5b7e",
    ]
    for sample in must_not_catch:
        assert not _SALARY_CONTEXT.search(sample), (
            f"salary detector false positive on: {sample}"
        )


# The key prefix is assembled so this file does not contain it literally.
_API_KEY_SHAPE = re.compile("freellm" + r"api-[A-Za-z0-9_\-]{16,}")


def test_no_api_key_shape_in_any_tracked_file():
    """Backs the Gitleaks path allow-list: tests/ and .opencode/ are scanned here."""
    found = [
        name
        for name in _tracked_files()
        if _API_KEY_SHAPE.search(pathlib.Path(name).read_text(errors="ignore"))
    ]
    assert not found, f"API key shape in tracked files: {found}"


def test_env_reference_allowlist_does_not_hide_real_secrets():
    """The env-indirection allow-list must not become a blanket exemption."""
    assert not _is_env_reference('"apiKey": "abcd1234efgh5678ijkl"')
    assert not _is_env_reference("Authorization: Bearer abcdefghijklmnopqrstuvwx")
    assert _is_env_reference("const apiKey = process.env.API_KEY")
    assert _is_env_reference('bearer "${AUTH_TOKEN}"')
    assert _is_env_reference("Bearer eyJhbGciOiJIUzI1NiIs...")
