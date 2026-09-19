"""Location tier model tests — hermetic, no network."""

from __future__ import annotations

from job_agent.location import (
    LocationTier,
    LocationWeights,
    RemoteScope,
    location_fit,
    normalize_location,
    parse_location,
    preferred_location_terms,
    tier_weight,
)


def test_munich_core_is_exact_tier():
    for raw in ("Munich, Germany", "Munich", "München", "München, Germany", "Garching bei München", "Dachau, Germany"):
        p = parse_location(raw)
        assert p.tier is LocationTier.MUNICH, raw
        assert p.region == "munich"


def test_metro_area_not_penalized():
    p = parse_location("Munich Metro Area")
    assert p.tier.value.startswith(("A_", "B_"))


def test_bavaria_regional_uplift():
    p = parse_location("Augsburg, Germany")
    assert p.tier is LocationTier.BAVARIA
    assert p.region == "bavaria"


def test_germany_tier():
    for raw in ("Berlin, Germany", "Hamburg"):
        p = parse_location(raw)
        assert p.tier is LocationTier.GERMANY, raw
        assert p.region == "germany"


def test_remote_always_remote_tier():
    for raw in (
        "Remote",
        "Remote (Europe)",
        "Remote Germany",
        "Remote - Germany",
        "Remote (Worldwide)",
        "Berlin (Remote)",
        "Hybrid Berlin",
        "remote based in Germany",
    ):
        p = parse_location(raw)
        assert p.tier is LocationTier.REMOTE, raw
    assert parse_location("Remote (Europe)").remote_scope is RemoteScope.EU
    assert parse_location("Remote (Worldwide)").remote_scope is RemoteScope.WORLDWIDE
    assert parse_location("Remote Germany").remote_scope is RemoteScope.DE
    assert parse_location("Remote").remote_scope is RemoteScope.UNKNOWN


def test_remote_worldwide_explicit():
    p = parse_location("Anywhere in the world")
    assert p.remote_scope is RemoteScope.WORLDWIDE


def test_remote_europe_explicit():
    p = parse_location("Europe (remote)")
    assert p.remote_scope is RemoteScope.EU


def test_international_gap_signal():
    p = parse_location("Tokyo, Japan")
    assert p.tier is LocationTier.EUROPE
    assert p.region == "international"


def test_unknown_tier():
    for raw in ("", None, "unbekannt", "???"):
        p = parse_location(raw)
        assert p.tier is LocationTier.UNKNOWN, repr(raw)


def test_location_fit_weights():
    w = LocationWeights()
    assert location_fit("Munich, Germany", w)[0] == w.exact_city
    assert location_fit("Berlin, Germany", w)[0] == w.country
    assert location_fit("Remote Europe", w)[0] == w.remote
    assert location_fit("Tokyo, Japan", w)[0] == w.europe
    assert location_fit("", w)[0] == w.unknown


def test_relocation_without_offer_caps_international():
    w = LocationWeights()
    fit, parse = location_fit("Remote (EU)", w, willing_to_relocate=False)
    assert fit == w.remote
    fit, parse = location_fit("Europe only", w, willing_to_relocate=False)
    assert fit == w.relocate_without_offer


def test_tier_weight_ordering():
    w = LocationWeights()
    assert w.exact_city >= w.metro >= w.regional >= w.country
    assert w.country >= w.remote >= w.europe >= w.unknown >= w.relocate_without_offer
    assert tier_weight(LocationTier.MUNICH, w) == w.exact_city
    assert tier_weight(LocationTier.UNKNOWN, w) == w.unknown


def test_custom_weights_override_defaults():
    w = LocationWeights(exact_city=0.5, country=0.5, remote=0.9)
    assert location_fit("Munich", w)[0] == 0.5
    assert location_fit("Berlin, Germany", w)[0] == 0.5
    assert location_fit("Remote", w)[0] == 0.9


def test_preferred_location_terms_deterministic():
    a = preferred_location_terms("Munich")
    b = preferred_location_terms("Munich")
    assert a == b
    assert "Munich" in a
    assert "Remote Europe" in a


def test_preferred_location_terms_scoped():
    terms = preferred_location_terms("Munich", national=False, remote=False)
    assert "Germany" not in terms
    assert "Remote" not in terms


def test_normalize_location_keeps_unicode():
    assert normalize_location("München") == "munich"
    assert normalize_location("München, Germany") == "munich, germany"
