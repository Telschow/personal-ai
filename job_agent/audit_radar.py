import sys
sys.path.insert(0, ".")
from job_agent.config import load_config
from job_agent.discovery import build_sources
from job_agent.company_radar import _load_radar, _find_source_for_provider

cfg = load_config()
radar = _load_radar()

for comp in radar:
    name = comp.name
    token = None
    provider_type = None
    if comp.greenhouse:
        provider_type = "greenhouse"
        token = comp.greenhouse
    elif comp.ashby:
        provider_type = "ashby"
        token = comp.ashby
    elif comp.lever:
        provider_type = "lever"
        token = comp.lever
    elif comp.smartrecruiters:
        provider_type = "smartrecruiters"
        token = comp.smartrecruiters
    else:
        print(f"{name}: no provider")
        continue
    src = _find_source_for_provider(cfg, provider_type, token)
    if not src:
        print(f"{name}: provider not found {provider_type}/{token}")
        continue
    print(f"{name}: {provider_type}/{token} -> src {src.name}")
    try:
        jobs = src.fetch()
        print(f"  fetched {len(jobs)} jobs")
    except Exception as e:
        print(f"  error: {e}")
