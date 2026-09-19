from job_agent.canonical import canonical_key, normalize_company, normalize_location, normalize_title, split_locations


def test_company_legal_suffix_stripped():
    assert "gmbh" not in normalize_company("Acme GmbH")
    assert normalize_company("Acme GmbH") == "acme"


def test_company_ag():
    assert normalize_company("Rohde & Schwarz AG") == "rohde & schwarz"


def test_title_mwd_stripped():
    assert "(m/w/d)" not in normalize_title("Product Manager (m/w/d)")
    assert normalize_title("Product Manager (m/w/d)") == "product manager"


def test_title_all_genders_stripped():
    t = normalize_title("Senior Engineer (all genders)")
    assert "all genders" not in t
    assert t == "senior engineer"


def test_location_muenchen_to_munich():
    assert normalize_location("München") == "munich"
    assert normalize_location("München, Germany") == "munich"
    assert normalize_location("Muenchen") == "munich"


def test_location_de_to_germany():
    assert normalize_location("DE") == "germany"
    assert normalize_location("Deutschland") == "germany"


def test_split_locations():
    result = split_locations("Munich, Germany, Remote")
    assert "munich" in result
    assert "germany" in result


def test_canonical_key_deterministic():
    k1 = canonical_key("Acme GmbH", "Product Manager (m/w/d)", "München")
    k2 = canonical_key("Acme", "Product Manager", "Munich")
    assert k1 == k2


def test_canonical_key_uses_concrete_location():
    k = canonical_key("X", "PM", "Munich, Germany, Remote")
    # The concrete city token wins over "germany" and "remote"
    assert "munich" in k
    # Remote should not appear as location component when concrete token exists
    parts = k.split("\x1f")
    assert parts[2] == "munich"


def test_canonical_key_none_fields():
    k = canonical_key(None, None, None)
    assert "\x1f" in k
    assert k.count("\x1f") == 2
