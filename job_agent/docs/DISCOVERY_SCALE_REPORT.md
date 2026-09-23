# DISCOVERY SCALE REPORT

## Market Reach & Yield Summary

| Metric | Count |
| :--- | :---: |
| Total Candidates Discovered | 146 |
| Total Unique Jobs Persisted | 146 |
| Jobs Published in Last 30 Days | 59 |
| Total High-Fit Jobs (Fit ≥ 75%) | 3 |
| Recent High-Fit Jobs | 3 |
| Duplicate Postings Merged | 1 |

## Provider Performance Breakdown

| Source | Method | Total Jobs | Notes |
| :--- | :--- | :---: | :--- |
| RemoteOK | API/Provider | 99 | Primary contributor to current pool |
| Remotive | API/Provider | 20 | Secondary contributor |
| Direct/Search | Web Search | 26 | Web search fallback (slow) |

## Career Space Coverage

- **Mobility / Autonomous Driving**: 14 jobs
- **Technical Program Management**: 12 jobs
- **AI / Machine Learning**: 12 jobs
- **Technical Product Management**: 8 jobs
- **Robotics / Autonomy**: 7 jobs
- **Engineering Leadership**: 3 jobs

## Scalability Analysis & Next Steps

1. **Search Engine Bottleneck**: DuckDuckGo search queries are severely rate-limited and slow, yielding only ~1.8 candidates per query vs ~60 candidates per request from API providers.
2. **Path to 1,000 Jobs**: Expanding provider coverage by fixing Greenhouse, Lever, and Ashby ATS adapters is necessary to reach the 1,000+ job mark without relying on web search fallback.
3. **Fit Model Calibration**: The current threshold accurately identifies true strong matches (3 high-fit roles) without inflating scores artificially.
