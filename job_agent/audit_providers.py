import sys
sys.path.insert(0, ".")
from job_agent.config import load_config
from job_agent.discovery import build_sources

cfg = load_config()
sources = build_sources(cfg)
for src in sources:
    print(src.name, src.kind.value, getattr(src, "token", getattr(src, "board", getattr(src, "company", None))))
    try:
        jobs = src.fetch()
        print("  fetched", len(jobs))
    except Exception as e:
        print("  error:", e)
