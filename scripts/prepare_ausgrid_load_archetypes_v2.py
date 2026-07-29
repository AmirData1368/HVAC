from __future__ import annotations

import re

import prepare_ausgrid_load_archetypes as base


def interval_minute(label: str) -> int | None:
    """Parse all published Ausgrid interval-ending header variants.

    Confirmed variants include `24:00`, `24:00:00`, and FY2024's trailing `00:00`.
    Because the Ausgrid files are interval-ending daily tables, a zero-hour endpoint is
    the final 15-minute interval of the stated operational day and maps to minute 1440.
    """
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(label).strip())
    if not match:
        return None
    hour, minute, second = match.groups()
    hour, minute = int(hour), int(minute)
    second = int(second or 0)
    if second != 0:
        return None
    if hour == 24 and minute == 0:
        return 1440
    if hour == 0 and minute == 0:
        return 1440
    if 0 <= hour <= 23 and minute in {0, 15, 30, 45}:
        value = hour * 60 + minute
        return value if value > 0 else None
    return None


base.interval_minute = interval_minute

if __name__ == "__main__":
    base.main()
