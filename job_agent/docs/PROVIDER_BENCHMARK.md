# PROVIDER BENCHMARK REPORT

## Overview
This benchmark evaluates candidate job discovery sources based on observed yield (jobs/request), data quality, and reliability. The goal is to identify high-potential providers for scaling to >1,000 jobs.

## Benchmark Results

| Provider | Requests | Jobs | Unique | Parse % | Avg Latency | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| RemoteOK | 1 | 99 | 99 | 100% | 1.3s | OK |
| Remotive | 1 | 18 | 18 | 100% | 0.8s | OK |
| Greenhouse | 1 | 0 | 0 | 0% | 0.2s | FAILED (404) |
| Lever | 1 | 0 | 0 | 0% | 0.2s | FAILED (404) |
| Ashby | 1 | 0 | 0 | 0% | 0.2s | FAILED (404) |
| SmartRecruiters | 1 | 0 | 0 | 0% | 0.2s | OK (Zero Yield) |

## Key Findings
- **High-Yield Sources**: RemoteOK and Remotive are currently the only functional high-yield providers.
- **ATS Bottlenecks**: Major ATS platforms (Greenhouse, Lever, Ashby) are failing due to misconfigured API endpoints.
- **Strategy Shift**: Immediate engineering efforts should focus on fixing ATS API endpoints, as these are the primary keys to reaching the 1,000+ job target reliably.
