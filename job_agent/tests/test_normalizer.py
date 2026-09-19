from job_agent.models import Job
from job_agent.normalizer import classify_remote_mode, normalize_job


def _job(**overrides) -> Job:
    base = dict(
        id="1",
        title="Product Manager (m/w/d)",
        company="Acme GmbH",
        url="https://acme/jobs/1",
        source="greenhouse",
        location="München, Germany",
        salary_min=100000,
        salary_max=130000,
        salary_currency="USD",
    )
    base.update(overrides)
    return Job(**base)


def test_normalize_job_computes_canonical_and_salary():
    j = normalize_job(_job(), source_type="ats_board")
    assert j.canonical_key is not None
    assert "munich" in j.canonical_key.split("\x1f")[2]
    assert j.salary_converted is True
    assert j.salary_min_eur is not None and j.salary_min_eur != j.salary_min
    assert j.source_type == "ats_board"
    assert j.status == "active"


def test_normalize_job_keeps_eur():
    j = normalize_job(_job(salary_currency="EUR"), source_type="ats_board")
    assert j.salary_converted is False
    assert j.salary_min_eur == 100000


def test_normalize_job_no_salary():
    j = normalize_job(
        _job(salary_min=None, salary_max=None, salary_currency=None),
        source_type="ats_board",
    )
    assert j.salary_min_eur is None and j.salary_max_eur is None


def test_classify_remote():
    assert (
        classify_remote_mode(Job(id="x", title="t", company="c", url="u", source="s", location="Remote (Europe)"))
        == "remote"
    )
    assert (
        classify_remote_mode(Job(id="x", title="t", company="c", url="u", source="s", location="Berlin, Hybrid"))
        == "hybrid"
    )
    assert classify_remote_mode(Job(id="x", title="t", company="c", url="u", source="s", location="Munich")) == "onsite"
