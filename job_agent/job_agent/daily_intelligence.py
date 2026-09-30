"""Daily intelligence report generator."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .calibration import generate_calibration_report
from .db import count_jobs
from .feedback import get_feedback_summary


def generate_daily_intelligence(conn: sqlite3.Connection, output_path: Path) -> Path:
    """Generate daily intelligence report with delta tracking."""
    # Count jobs
    total_jobs = count_jobs(conn)

    # Get calibration data
    calibration = generate_calibration_report(conn)

    # Get feedback summary
    feedback_summary = get_feedback_summary(conn)

    # Build report
    report = f"""# Daily Intelligence Report

## Today: {datetime.now(UTC).strftime("%Y-%m-%d")}

### Corpus Stats
- Total jobs: {total_jobs}
- Jobs with feedback: {feedback_summary["unique_jobs"]}
- Total feedback records: {feedback_summary["total_records"]}

### Precision Metrics
- Precision@5: {calibration["precision_at_5"]:.2%}
- Precision@10: {calibration["precision_at_10"]:.2%}
- Precision@20: {calibration["precision_at_20"]:.2%}

### False Positive/Negative
- False positive rate (top 10): {calibration["false_positive_rate_10"]:.2%}
- False negative rate: {calibration["false_negative_rate"]:.2%}

### Feedback Distribution
"""

    for label, count in calibration["feedback_summary"]["label_distribution"].items():
        report += f"- {label}: {count}\n"

    report += """
### Score-Feedback Correlation
"""

    correlation = calibration["score_feedback_correlation"]
    if "average_scores_by_label" in correlation:
        for label, avg_score in correlation["average_scores_by_label"].items():
            report += f"- {label}: avg score {avg_score:.2f} (n={correlation['label_counts'].get(label, 0)})\n"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)
    return output_path


def main() -> None:
    import argparse

    from .config import load_config
    from .db import connect

    parser = argparse.ArgumentParser(description="Generate daily intelligence report")
    parser.add_argument("--config", default=None)
    parser.add_argument("--database", default=None)
    parser.add_argument("--output", default="output/reports/daily_intelligence.md")
    args = parser.parse_args()

    cfg = load_config(path=args.config)
    db_path = args.database or cfg.database_path

    conn = connect(db_path)
    try:
        output_path = Path(args.output)
        generate_daily_intelligence(conn, output_path)
        print(f"Report written to {output_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
