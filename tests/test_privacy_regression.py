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

# Employers and institutions that appeared in this repository's history as the
# maintainer's own. The combination (employer + role + domain + institution)
# reconstructs one person's CV, so any of them appearing in tracked text is a
# finding regardless of how neutral the surrounding sentence looks.
PERSONAL_CAREER_TERMS = [
    "BCG",
    "BMW",
    "Helsing",
    "Quantum Systems",
    "Rohde",
    "TUMCREATE",
]

# A default that encodes somebody's job search makes a fresh checkout rank
# postings by a policy nobody in the repository chose. These are the values the
# shipped config must not have.
#
# The list is literal on purpose. A general "any six-figure number" pattern was
# tried and is unusable on this tree: it flags hex colour literals in
# styles.css, sdist hashes in uv.lock, and every synthetic salary fixture in
# job_agent/tests/. The figures below are the ones that actually appeared in this
# repository's history, enumerated in every separator style a person writes
# them, so the detector stays checkable.
#
# The `110k`/`130k` entries were added after those exact figures were found
# published in job_agent/docs/IMPLEMENTATION_PLAN.md -- the four earlier
# entries (120000/150000 and underscore variants) did not match them at all.
PERSONAL_SALARY_VALUES = ["110000", "120000", "130000", "140000", "150000"]


def _salary_spellings(value: str) -> list[str]:
    """Every way a person writes one figure, for the literal match list.

    The separator goes *inside* the number (`110_000`, `110.000`), while the
    `k` abbreviation replaces the last three digits (`110k`, `110 k`, `110K`).
    Building these as `value + sep` would produce `110000k`, which is why the
    first version of this list matched nothing in prose at all.
    """
    head, tail = value[:-3], value[-3:]
    return [
        value,
        f"{head}_{tail}",
        f"{head}.{tail}",
        f"{head}k",
        f"{head}K",
        f"{head} k",
    ]


PERSONAL_DEFAULT_VALUES = sorted(
    {s for value in PERSONAL_SALARY_VALUES for s in _salary_spellings(value)}
)


def _job_agent_config():
    """Import the shipped Job Agent config module.

    The module uses relative imports, so it has to be imported as part of its
    package with that package root on sys.path -- loading the file directly
    raises "attempted relative import with no known parent package".
    """
    import importlib
    import sys

    root = str(pathlib.Path("job_agent").resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module("job_agent.config")


def _matches(text: str, terms: list[str]) -> list[str]:
    """Terms present as whole words.

    Plain substring matching is unusable on this tree: the committed
    docs/architecture/personal-ai.html embeds base64 font data, in which "BCG"
    and "BMW" occur as ordinary base64 triplets. Word boundaries keep the
    detector honest about what it is actually catching.
    """
    return [t for t in terms if re.search(rf"\b{re.escape(t)}\b", text)]


def test_shipped_job_agent_defaults_carry_no_career_policy():
    """A fresh Config must express no salary, city, sector, or network policy."""
    config = _job_agent_config()
    cfg = config.Config()

    assert cfg.jobs.salary.minimum_eur == 0
    assert cfg.jobs.salary.target_eur == 0
    assert cfg.career.location.preferred_city == ""
    assert cfg.search.industries_preferred == []
    assert cfg.search.global_enabled is False

    # Local services by loopback: a committed host reference is a machine
    # fingerprint and can point at a developer's private LAN address.
    assert cfg.llm.base_url.startswith("http://127.0.0.1:")
    assert "docker.internal" not in cfg.llm.base_url


def test_salary_floor_is_opt_in_not_inherited():
    """Enabling the floor must require an explicit, complete operator choice."""
    config = _job_agent_config()
    pydantic = pytest.importorskip("pydantic")

    with pytest.raises(pydantic.ValidationError):
        config.Config(jobs={"salary": {"minimum_eur": 100000, "target_eur": 0}})
    with pytest.raises(pydantic.ValidationError):
        config.Config(jobs={"salary": {"minimum_eur": 0, "target_eur": 100000}})
    with pytest.raises(pydantic.ValidationError):
        config.Config(jobs={"salary": {"minimum_eur": 150000, "target_eur": 100000}})

    enabled = config.Config(
        jobs={"salary": {"minimum_eur": 100000, "target_eur": 150000}}
    )
    assert enabled.jobs.salary.minimum_eur == 100000


def test_no_personal_employer_in_tracked_text():
    """No tracked file may name the maintainer's actual employers."""
    found: dict[str, list[str]] = {}
    for f in _tracked_published_files():
        txt = f.read_text(errors="ignore")
        hits = _matches(txt, PERSONAL_CAREER_TERMS)
        if hits:
            found[str(f)] = hits
    assert not found, f"Personal employer references in tracked files: {found}"


def test_no_personal_employer_in_test_sources():
    """Test fixtures reconstruct the same CV, so they are scanned too.

    _tracked_published_files() skips anything under a tests/ directory because
    test data legitimately contains PII-shaped vectors. That exemption is
    correct for synthetic PII and wrong for a real employer name, which carries
    the same identity in a fixture as it does in a document. This test holds
    the employer list to the stricter standard.
    """
    found: dict[str, list[str]] = {}
    this_file = pathlib.Path(__file__).resolve()
    for name in _tracked_files():
        if not name.endswith(".py"):
            continue
        f = pathlib.Path(name)
        # This file necessarily contains the term list it searches for.
        if f.resolve() == this_file:
            continue
        hits = _matches(f.read_text(errors="ignore"), PERSONAL_CAREER_TERMS)
        if hits:
            found[str(f)] = hits
    assert not found, f"Personal employer references in Python sources: {found}"


def test_no_salary_figure_in_shipped_defaults_or_docs():
    """A published salary figure is a personal policy, not documentation."""
    found: dict[str, list[str]] = {}
    for f in _tracked_published_files():
        txt = f.read_text(errors="ignore")
        hits = _matches(txt, PERSONAL_DEFAULT_VALUES)
        if hits:
            found[str(f)] = hits
    assert not found, f"Salary figures in published files: {found}"


def test_offline_example_config_performs_no_requests():
    """A copied example config must not reach the network."""
    import yaml

    example = pathlib.Path("job_agent/config.example.yaml")
    raw = yaml.safe_load(example.read_text()) or {}

    cfg = _job_agent_config().Config.model_validate(raw)
    job_raw = raw.get("jobs", {})
    assert job_raw.get("salary", {}).get("minimum_eur", 0) == 0
    assert job_raw.get("salary", {}).get("target_eur", 0) == 0
    assert raw.get("search", {}).get("global_enabled", False) is False
    assert raw.get("search", {}).get("countries", []) == []
    assert cfg.search.global_enabled is False
    assert cfg.career.location.preferred_city == ""


def test_salary_detector_catches_every_spelling():
    """The literal salary list must match the shapes people actually write.

    The list previously held only `120000`/`150000` and underscore variants. It
    did not match `110k` or `130k`, which is exactly how the personal floor was
    published in job_agent/docs/IMPLEMENTATION_PLAN.md. A second version built
    the variants by appending to the whole number (`110000k`) and matched nothing
    in prose. These cases pin both separators and the k-abbreviation.
    """
    must_catch = [
        "110000",
        "110_000",
        "110.000",
        "110k",
        "110K",
        "110 k",
        "120k-150k",
        "€120.000 per year",
        "target_eur: 130000",
        "floor 110k / target 130k",
    ]
    for sample in must_catch:
        assert _matches(sample, PERSONAL_DEFAULT_VALUES), (
            f"salary detector missed: {sample}"
        )

    must_not_catch = ["0", "3.14.4", "8080", "utf-8", "2.1.0", "1000 vectors"]
    for sample in must_not_catch:
        assert not _matches(sample, PERSONAL_DEFAULT_VALUES), (
            f"salary detector false positive on: {sample}"
        )


def test_env_reference_allowlist_does_not_hide_real_secrets():
    """The env-indirection allow-list must not become a blanket exemption."""
    assert not _is_env_reference('"apiKey": "abcd1234efgh5678ijkl"')
    assert not _is_env_reference("Authorization: Bearer abcdefghijklmnopqrstuvwx")
    assert _is_env_reference("const apiKey = process.env.API_KEY")
    assert _is_env_reference('bearer "${AUTH_TOKEN}"')
    assert _is_env_reference("Bearer eyJhbGciOiJIUzI1NiIs...")
