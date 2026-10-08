"""Sanitized, deterministic Boostcamp-style CSV fixtures for workout tests.

Nothing here is real personal data: ids, names, weights and dates are
invented. The fixtures deliberately exercise the edge cases the real export
contains day-first timestamps, superset shells, skipped and open-ended sets,
RPE ranges, bodyweight exercises, and malformed rows.
"""

from __future__ import annotations

import csv
import io
import json


def _set(
    value: str = "",
    amount: str = "",
    *,
    weight_unit: str = "",
    target_type: str = "reps",
    skipped: bool = False,
    custom: bool = False,
    source: str = "user",
    intensity: object = None,
    intensity_unit: str = "",
) -> dict[str, object]:
    data: dict[str, object] = {
        "value": value,
        "amount": amount,
        "weight_unit": weight_unit,
        "target_type": target_type,
        "skipped": skipped,
        "custom": custom,
        "source": source,
    }
    if intensity is not None:
        data["intensity"] = intensity
    if intensity_unit:
        data["intensity_unit"] = intensity_unit
    return data


def _exercise(
    exercise_id: str,
    name: str,
    sets: list[dict[str, object]],
    *,
    equipment_type: str,
    notes: str = "",
    custom: bool = False,
    source: str = "user",
) -> dict[str, object]:
    data: dict[str, object] = {
        "id": exercise_id,
        "name": name,
        "type": equipment_type,
        "skipped": False,
        "custom": custom,
        "source": source,
    }
    if notes:
        data["notes"] = notes
    data["sets"] = sets
    return data


_BENCH_ROW = [
    "session-001",
    "1/2/2024 10:00:00.000+00",
    "1/2/2024 10:00:00.000+00",
    "prog-alpha",
    "user-1",
    "done",
    "1/2/2024 10:32:05.000+00",
    "1",
    "2",
    "{}",
    json.dumps(
        [
            _exercise(
                "ex-bench",
                "Bench Press (Barbell)",
                [
                    _set(
                        "80", "5", weight_unit="kg", intensity="75", intensity_unit="%"
                    ),
                    _set(
                        "82.5",
                        "3",
                        weight_unit="kg",
                        intensity="85",
                        intensity_unit="%",
                    ),
                    _set(
                        "82.5",
                        "3+",
                        weight_unit="kg",
                        intensity=[8, 9],
                        intensity_unit="RPE",
                    ),
                ],
                equipment_type="Barbell",
                notes="Paused reps",
            )
        ]
    ),
    "log-1",
    "Felt strong",
    "Feb 01 Workout",
    "1925",
    "",
    "0",
    "",
    "1/2/2024 10:32:06.000+00",
]

_SUPERSET_ROW = [
    "session-002",
    "16/6/2024 08:00:00+00",
    "16/6/2024 08:00:00+00",
    "",
    "user-1",
    "done",
    "16/6/2024 08:40:00+00",
    "3",
    "1",
    "{}",
    json.dumps(
        [
            {
                "id": "ss-1",
                "name": "",
                "type": "Superset",
                "custom": False,
                "source": "user",
                "sets": [],
                "supersets": [
                    _exercise(
                        "ch-row",
                        "Barbell Row",
                        [
                            _set("60", "8", weight_unit="kg"),
                            _set("60", "10", weight_unit="kg"),
                        ],
                        equipment_type="Barbell",
                    ),
                    _exercise(
                        "ch-curl",
                        "Bicep Curl (EZ Bar)",
                        [_set("20", "12", weight_unit="kg")],
                        equipment_type="EZ Bar",
                    ),
                ],
            }
        ]
    ),
    "log-2",
    "",
    "Superset Session",
    "2400",
    "",
    "0",
    "",
    "16/6/2024 08:41:00+00",
]

_BODYWEIGHT_ROW = [
    "session-003",
    "28/8/2030 20:00:00+00",
    "28/8/2030 20:00:00+00",
    "",
    "user-1",
    "done",
    "28/8/2030 20:20:00+00",
    "",
    "",
    "{}",
    json.dumps(
        [
            _exercise(
                "ex-dips",
                "Ring Dips",
                [
                    _set(
                        "",
                        "15",
                        weight_unit="",
                        custom=True,
                        source="coach",
                        intensity=[7, 8],
                        intensity_unit="RPE",
                    )
                ],
                equipment_type="Bodyweight",
                custom=True,
                source="coach",
            )
        ]
    ),
    "log-3",
    "",
    "Afternoon Workout",
    "1200",
    "",
    "0",
    "",
    "28/8/2030 20:21:00+00",
]

_OFFSET_ROW = [
    "session-004",
    "4/9/2024 21:00:00.500+01",
    "4/9/2024 21:00:00.500+01",
    "",
    "user-1",
    "done",
    "4/9/2024 21:45:00.500+01",
    "4",
    "5",
    "{}",
    json.dumps(
        [
            _exercise(
                "ex-dead",
                "Deadlift (Barbell)",
                [
                    _set(
                        "100",
                        "5",
                        weight_unit="kg",
                        skipped=True,
                        intensity="70",
                        intensity_unit="%",
                    ),
                    _set(
                        "100",
                        "5+",
                        weight_unit="kg",
                        intensity="75",
                        intensity_unit="%",
                    ),
                ],
                equipment_type="Barbell",
            )
        ]
    ),
    "log-4",
    "",
    "Sep 04 Workout",
    "2700",
    "",
    "0",
    "",
    "4/9/2024 21:46:00.500+01",
]

# Malformed records JSON: the row survives but keeps no exercises.
_BAD_JSON_ROW = [
    "session-005",
    "5/5/2024 09:00:00+00",
    "5/5/2024 09:00:00+00",
    "",
    "user-1",
    "done",
    "5/5/2024 09:15:00+00",
    "",
    "",
    "{}",
    "{oops",
    "log-5",
    "",
    "Broken Row",
    "900",
    "",
    "0",
    "",
    "5/5/2024 09:16:00+00",
]

# No source id: the row is skipped with a warning.
_NO_ID_ROW = [
    "",
    "6/6/2024 09:00:00+00",
    "6/6/2024 09:00:00+00",
    "",
    "user-1",
    "done",
    "6/6/2024 09:15:00+00",
    "",
    "",
    "{}",
    "[]",
    "log-6",
    "",
    "No Id Row",
    "900",
    "",
    "0",
    "",
    "6/6/2024 09:16:00+00",
]

# Unparseable timestamp: the row is skipped with a warning.
_BAD_DATE_ROW = [
    "session-007",
    "not-a-date",
    "not-a-date",
    "",
    "user-1",
    "done",
    "not-a-date",
    "",
    "",
    "{}",
    "[]",
    "log-7",
    "",
    "Bad Date Row",
    "900",
    "",
    "0",
    "",
    "not-a-date",
]

# An explicitly empty records list: valid, zero exercises.
_EMPTY_RECORDS_ROW = [
    "session-008",
    "7/7/2024 09:00:00+00",
    "7/7/2024 09:00:00+00",
    "",
    "user-1",
    "done",
    "7/7/2024 09:30:00+00",
    "",
    "",
    "{}",
    "[]",
    "log-8",
    "",
    "Empty Session",
    "1800",
    "",
    "0",
    "",
    "7/7/2024 09:31:00+00",
]

FIXTURE_ROWS = [
    _BENCH_ROW,
    _SUPERSET_ROW,
    _BODYWEIGHT_ROW,
    _OFFSET_ROW,
    _BAD_JSON_ROW,
    _NO_ID_ROW,
    _BAD_DATE_ROW,
    _EMPTY_RECORDS_ROW,
]

# The updated variant changes one weight so the ``workouts_updated`` path is
# exercised while every other row hashes identically.
_MODIFIED_BENCH_ROW = list(_BENCH_ROW)
_records = json.loads(_MODIFIED_BENCH_ROW[10])
_records[0]["sets"][0]["value"] = "85"
_MODIFIED_BENCH_ROW[10] = json.dumps(_records)

MODIFIED_ROWS = [
    _MODIFIED_BENCH_ROW,
    _SUPERSET_ROW,
    _BODYWEIGHT_ROW,
    _OFFSET_ROW,
    _BAD_JSON_ROW,
    _NO_ID_ROW,
    _BAD_DATE_ROW,
    _EMPTY_RECORDS_ROW,
]

_SECOND_FILE_ROW = [
    "second-100",
    "3/2/2024 07:00:00+00",
    "3/2/2024 07:00:00+00",
    "prog-second",
    "user-1",
    "done",
    "3/2/2024 07:25:00+00",
    "1",
    "1",
    "{}",
    json.dumps(
        [
            _exercise(
                "ex-ohp",
                "Overhead Press (Barbell)",
                [_set("40", "5", weight_unit="kg")],
                equipment_type="Barbell",
            )
        ]
    ),
    "log-second",
    "",
    "Feb 03 Workout",
    "1500",
    "",
    "0",
    "",
    "3/2/2024 07:26:00+00",
]

HEADER = [
    "id",
    "created_at",
    "updated_at",
    "program_id",
    "user_id",
    "status",
    "finished_at",
    "week",
    "day",
    "workout",
    "records",
    "program_log_id",
    "user_notes",
    "title",
    "duration",
    "legacy",
    "program_variation_index",
    "share_image",
    "finished_v2_at",
]


def to_csv(rows: list[list[str]], header: list[str] | None = None) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header or HEADER)
    writer.writerows(rows)
    return buffer.getvalue()


FIXTURE_CSV = to_csv(FIXTURE_ROWS)
MODIFIED_CSV = to_csv(MODIFIED_ROWS)
SECOND_FILE_CSV = to_csv([_SECOND_FILE_ROW])
