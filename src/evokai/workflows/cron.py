"""Cron expressions for scheduled workflows, read in a time zone.

Five fields: minute (0-59), hour (0-23), day of the month (1-31), month (1-12 or ``jan``-``dec``)
and day of the week (0-7 or ``sun``-``sat``, 0 and 7 are Sunday). Each field takes ``*``, a
number, a range ``a-b``, a step ``*/n`` or ``a-b/n``, and lists of these with commas. The
shorthands ``@hourly``, ``@daily``, ``@weekly``, ``@monthly`` and ``@yearly`` are accepted.

As in classic cron, when both the day of the month and the day of the week are restricted, a day
matches either of them. Times are wall-clock times of the zone: ``0 9 * * *`` in
``Europe/Rome`` is nine in the morning there, summer or winter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
_MACROS = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}
SEARCH_DAYS = 366 * 5
"""How far back or forward an occurrence is looked for (``0 0 29 2 *`` happens every 4 years)."""


class CronError(ValueError):
    """A cron expression or a time zone that cannot be used."""


def zone(name: str | None) -> ZoneInfo:
    """A time zone by name.

    Returns:
        The zone (UTC when no name is given).

    Raises:
        CronError: An unknown zone.
    """
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise CronError(f"unknown time zone {name!r}") from e


def _field(text: str, low: int, high: int, names: list[str] | None = None) -> frozenset[int]:
    out: set[int] = set()

    def value(raw: str) -> int:
        raw = raw.strip().lower()
        if names and raw in names:
            return names.index(raw) + (1 if names is _MONTHS else 0)
        if not raw.isdigit():
            raise CronError(f"{raw!r} is not a number")
        return int(raw)

    for part in text.split(","):
        if not part:
            raise CronError("an empty list item")
        rng, _, step_raw = part.partition("/")
        step = int(step_raw) if step_raw.isdigit() else None
        if step_raw and (step is None or step < 1):
            raise CronError(f"bad step in {part!r}")
        if rng == "*":
            a, b = low, high
        elif "-" in rng:
            x, _, y = rng.partition("-")
            a, b = value(x), value(y)
        else:
            a = b = value(rng)
            if step is not None:
                b = high
        if not (low <= a <= high and low <= b <= high) or a > b:
            raise CronError(f"{part!r} is outside {low}-{high}")
        out.update(range(a, b + 1, step or 1))
    return frozenset(out)


@dataclass(frozen=True)
class Cron:
    """A parsed cron expression."""

    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    """0 = Sunday."""
    any_day: bool
    any_weekday: bool

    @classmethod
    def parse(cls, text: str) -> Cron:
        """Parse an expression.

        Returns:
            The cron.

        Raises:
            CronError: Not five valid fields (or a known shorthand).
        """
        raw = _MACROS.get(text.strip().lower(), text.strip())
        parts = raw.split()
        if len(parts) != 5:
            raise CronError("a cron expression has five fields: minute hour day month weekday")
        weekdays = _field(parts[4], 0, 7, _DAYS)
        return cls(
            minutes=_field(parts[0], 0, 59),
            hours=_field(parts[1], 0, 23),
            days=_field(parts[2], 1, 31),
            months=_field(parts[3], 1, 12, _MONTHS),
            weekdays=frozenset(d % 7 for d in weekdays),
            any_day=parts[2] == "*",
            any_weekday=parts[4] == "*",
        )

    def day_matches(self, d: date) -> bool:
        """Whether a calendar day can have occurrences.

        Returns:
            True when it does.
        """
        if d.month not in self.months:
            return False
        dom = d.day in self.days
        dow = (d.isoweekday() % 7) in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return dow
        if self.any_weekday:
            return dom
        return dom or dow

    def previous(self, now: datetime, tz: ZoneInfo) -> datetime | None:
        """The latest occurrence at or before a time.

        Returns:
            The occurrence (aware, UTC), or None within ``SEARCH_DAYS``.
        """
        local = now.astimezone(tz)
        hours = sorted(self.hours, reverse=True)
        minutes = sorted(self.minutes, reverse=True)
        for back in range(SEARCH_DAYS):
            d = local.date() - timedelta(days=back)
            if not self.day_matches(d):
                continue
            for h in hours:
                if back == 0 and h > local.hour:
                    continue
                for mi in minutes:
                    if back == 0 and h == local.hour and mi > local.minute:
                        continue
                    at = datetime.combine(d, time(h, mi), tzinfo=tz).astimezone(UTC)
                    if at <= now:
                        return at
        return None

    def next(self, now: datetime, tz: ZoneInfo) -> datetime | None:
        """The first occurrence after a time.

        Returns:
            The occurrence (aware, UTC), or None within ``SEARCH_DAYS``.
        """
        local = now.astimezone(tz)
        hours = sorted(self.hours)
        minutes = sorted(self.minutes)
        for ahead in range(SEARCH_DAYS):
            d = local.date() + timedelta(days=ahead)
            if not self.day_matches(d):
                continue
            for h in hours:
                if ahead == 0 and h < local.hour:
                    continue
                for mi in minutes:
                    at = datetime.combine(d, time(h, mi), tzinfo=tz).astimezone(UTC)
                    if at > now:
                        return at
        return None
