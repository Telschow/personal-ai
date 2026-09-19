from job_agent.salary import (
    EUR_RATES,
    SalaryInfo,
    _parse_amount,
    normalize_salary,
    salary_exceeds_floor,
    salary_reaches_target,
)

FIXED_RATES = dict(EUR_RATES)  # keep tests deterministic at module-import time


def test_eur_identity():
    s = normalize_salary(120000, 145000, "EUR")
    assert s.min_eur == 120000
    assert s.max_eur == 145000
    assert not s.converted
    assert s.uncertainty_reason is None


def test_usd_conversion():
    s = normalize_salary(130000, 150000, "USD", rates=FIXED_RATES)
    assert s.min_eur is not None
    assert abs(s.min_eur - 119600) < 100  # 130k * 0.92 ≈ 119600
    assert s.converted
    assert "FX" in (s.uncertainty_reason or "")


def test_gbp():
    s = normalize_salary(100000, 120000, "GBP", rates=FIXED_RATES)
    assert s.min_eur is not None
    assert abs(s.min_eur - 117500) < 100


def test_eur_symbol_alias():
    s = normalize_salary(100000, None, "€")
    assert s.currency == "EUR"
    assert s.min_eur == 100000


def test_unknown_currency_not_fatal():
    s = normalize_salary(100000, 120000, "INR")
    assert s.min_eur == 100000  # raw preserved
    assert not s.converted
    assert "unknown currency" in (s.uncertainty_reason or "")


def test_no_salary():
    s = normalize_salary(None, None, None)
    assert s.min_eur is None and s.max_eur is None
    assert "no compensation" in (s.uncertainty_reason or "")


def test_parse_k_suffix():
    assert _parse_amount("120k") == 120000.0
    assert _parse_amount("$90k") == 90000.0
    assert _parse_amount("€70.000") == 70000.0
    assert _parse_amount("") is None
    assert _parse_amount(None) is None
    assert _parse_amount(100000) == 100000.0


def test_reaches_target():
    s = SalaryInfo(min_eur=160000, max_eur=180000, currency="EUR", converted=False)
    assert salary_reaches_target(s, 150000)
    assert not salary_reaches_target(SalaryInfo(min_eur=140000, max_eur=None, currency="EUR", converted=False), 150000)


def test_exceeds_floor():
    s = SalaryInfo(min_eur=125000, max_eur=None, currency="EUR", converted=False)
    assert salary_exceeds_floor(s, 120000)
    assert not salary_exceeds_floor(SalaryInfo(min_eur=90000, max_eur=None, currency="EUR", converted=False), 120000)


def test_converts_none_min():
    s = normalize_salary(None, 140000, "USD", rates=FIXED_RATES)
    assert s.min_eur is None
    assert s.max_eur is not None
