import sys

sys.path.insert(0, ".")
from job_agent import db
from job_agent.company_radar import discover_radar
from job_agent.config import load_config
from job_agent.discovery import build_sources
from job_agent.pipeline import run_sources
from job_agent.scoring import scoring_policy_from_config

cfg = load_config()
conn = db.connect(":memory:")
run_id = "testrun"
# Run radar
metrics = discover_radar(conn, cfg, run_id=run_id)
print("radar metrics", metrics)
# Now run generic provider for first source

profile = {}
policy = scoring_policy_from_config(cfg.model_dump())
sources = build_sources(cfg)
src = sources[0]  # greenhouse helsing
result = run_sources(conn, [src], profile, policy, run_id=run_id)
print("run_sources total_fetched", result.total_fetched, "duplicates", result.total_duplicates)
cnt = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
print("total jobs in db", cnt)
