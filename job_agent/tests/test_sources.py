from job_agent.sources import DirectPageSource, RssJsonAdapter, WorkableSource


class Resp:
    def __init__(self, text="", status_code=200, content=None, json_data=None, headers=None):
        self.text = text
        self.status_code = status_code
        self.content = content if content is not None else text.encode()
        self._json_data = json_data
        self.headers = headers or {"content-type": "text/html"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_jsonld_jobposting(monkeypatch):
    html = (
        '<html><script type="application/ld+json">{"@type":"JobPosting",'
        '"title":"AI Product Manager","datePosted":"2026-09-01",'
        '"hiringOrganization":{"name":"Example AI"},'
        '"jobLocation":{"address":{"addressLocality":"Munich","addressCountry":"DE"}},'
        '"description":"Lead AI product strategy."}</script></html>'
    )
    monkeypatch.setattr("job_agent.sources.httpx.get", lambda *a, **k: Resp(text=html))
    jobs = DirectPageSource("https://example.com/careers").fetch()
    assert len(jobs) == 1
    assert jobs[0].company == "Example AI"
    assert jobs[0].location.startswith("Munich")


def test_workable_board_parses_jobs(monkeypatch):
    payload = {
        "jobs": [
            {
                "id": "123",
                "title": "AI Product Manager",
                "city": "Munich",
                "country": "Germany",
                "url": "https://acme.workable.com/jobs/1",
                "application_url": "https://acme.workable.com/jobs/1/apply",
                "description": "<p>Lead AI product strategy</p>",
                "salary_min": {"amount": "120000", "currency": "EUR"},
                "published_on": "2026-09-10",
            }
        ]
    }
    monkeypatch.setattr(
        "job_agent.sources.httpx.get",
        lambda *a, **k: Resp(json_data=payload, headers={"content-type": "application/json"}),
    )
    jobs = WorkableSource("acme").fetch()
    assert len(jobs) == 1
    j = jobs[0]
    assert j.id.startswith("workable:acme")
    assert j.title == "AI Product Manager"
    assert "Munich" in j.location


def test_workable_auth_required_is_error(monkeypatch):
    monkeypatch.setattr(
        "job_agent.sources.httpx.get",
        lambda *a, **k: Resp(status_code=403, headers={"content-type": "application/json"}),
    )
    try:
        WorkableSource("restricted-corp").fetch()
        raise AssertionError("expected SourceError")
    except RuntimeError as exc:
        assert "restricted" in str(exc)


def test_rss_json_adapter_parses_items(monkeypatch):
    payload = [
        {
            "title": "Senior Product Manager",
            "link": "https://jobs.example.com/1",
            "company": "WarpCorp",
            "location": "Berlin",
            "salary_max": "140000",
            "salary_currency": "EUR",
        }
    ]
    monkeypatch.setattr(
        "job_agent.sources.httpx.get",
        lambda *a, **k: Resp(json_data=payload, headers={"content-type": "application/json"}),
    )
    jobs = RssJsonAdapter("warpjobs", "https://feed.example.com/jobs.json").fetch()
    assert len(jobs) == 1
    assert jobs[0].company == "WarpCorp"
    assert jobs[0].salary_max is not None
    assert jobs[0].id.startswith("rss:warpjobs")


def test_rss_xml_adapter_parses(monkeypatch):
    xml = (
        "<rss><channel><item><title>Product Lead</title>"
        "<link>https://x.example.com/job</link>"
        "<description>AI product leadership.</description>"
        "<pubDate>Wed, 02 Oct 2026 08:00:00 +0000</pubDate>"
        "</item></channel></rss>"
    )
    monkeypatch.setattr(
        "job_agent.sources.httpx.get",
        lambda *a, **k: Resp(text=xml, headers={"content-type": "application/rss+xml"}),
    )
    jobs = RssJsonAdapter("feed", "https://feed.example.com/rss.xml").fetch()
    assert len(jobs) == 1
    assert jobs[0].title == "Product Lead"
    assert jobs[0].date_posted is not None
