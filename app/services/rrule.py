from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr


def next_occurrence(rrule_str: str, *, after: datetime, tz_name: str) -> datetime | None:
    """First occurrence of `rrule_str` strictly after `after`, evaluated on the
    user's local wall-clock time (medication schedules are local times, not
    UTC) and returned back in UTC for storage. None if the rule is exhausted
    (e.g. a COUNT/UNTIL-bounded rule) - our schedules are open-ended daily
    rules so this shouldn't happen in practice, but callers must handle it.
    """
    tz = ZoneInfo(tz_name)
    after_local = after.astimezone(tz).replace(tzinfo=None)
    dtstart = after_local.replace(hour=0, minute=0, second=0, microsecond=0)

    rule = rrulestr(rrule_str, dtstart=dtstart)
    next_local = rule.after(after_local, inc=False)
    if next_local is None:
        return None
    return next_local.replace(tzinfo=tz).astimezone(UTC)
