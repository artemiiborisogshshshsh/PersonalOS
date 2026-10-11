"""Calendar horizon, rolled forward for Saturday planning in local time."""

from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo


def planning_calendar_horizon(now: datetime, timezone: str | tzinfo, weeks: int = 2):
    zone = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    local = now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)
    start = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    # Keep this weekend visible while opening the next two full weeks.
    end = start + timedelta(weeks=max(1, weeks) + int(local.weekday() >= 5))
    return start, end
