import sys
from pathlib import Path

_JOB_AGENT_ROOT = Path(__file__).resolve().parents[1] / "job_agent"
if str(_JOB_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_JOB_AGENT_ROOT))
