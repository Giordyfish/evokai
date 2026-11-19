"""Forecasts of usage against a cap at the end of its window (a month by default).

A linear trend with weekday seasonality.

From the daily usage of the last ``HISTORY_DAYS`` complete days:

1. **Weekday factors**: each weekday's mean over the overall mean, shrunk towards 1 when a
   weekday has few days (``SHRINK`` pseudo-days at 1), so one busy Monday does not make every
   Monday busy.
2. **Trend**: an ordinary least-squares line over the de-seasonalised days (each day divided by
   its weekday factor); with fewer than ``MIN_TREND_DAYS`` days the slope is zero (the mean).
3. **Projection**: every day left in the window is the line at that day times its weekday factor
   (never below zero); what is left of today is that value times the share of the day still to
   come. The forecast is the month so far plus the projection.
4. **Band**: the residual standard deviation of the fit, scaled by the square root of the days
   left, gives a low and a high forecast.

Pure functions on plain numbers: the callers turn them into shares of a cap, never amounts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

HISTORY_DAYS = 28
SHRINK = 2.0
MIN_TREND_DAYS = 7


@dataclass(frozen=True)
class Forecast:
    """A month's usage so far and where it is heading (in the caller's unit)."""

    so_far: float
    total: float
    low: float
    high: float
    slope: float
    """Change of the de-seasonalised daily usage per day."""
    factors: dict[int, float] = field(default_factory=dict)
    """Weekday (0 = Monday) to its factor."""
    projection: dict[date, float] = field(default_factory=dict)
    """Each day left (today included, for what is left of it) to its projected usage."""
    days_left: int = 0

    @property
    def trend(self) -> str:
        """The direction of the daily usage.

        Returns:
            ``rising``, ``falling`` or ``flat`` (less than 2% of the mean per week).
        """
        mean = (self.total - self.so_far) / self.days_left if self.days_left else 0.0
        base = max(mean, 1e-12)
        weekly = self.slope * 7 / base
        if weekly > 0.02:
            return "rising"
        if weekly < -0.02:
            return "falling"
        return "flat"


def weekday_factors(days: dict[date, float]) -> dict[int, float]:
    """Each weekday's share of the mean day, shrunk towards 1.

    Returns:
        Weekday to factor (1 for weekdays without data, or when every day is zero).
    """
    values = list(days.values())
    mean = sum(values) / len(values) if values else 0.0
    if mean <= 0:
        return dict.fromkeys(range(7), 1.0)
    out = {}
    for w in range(7):
        sample = [v for d, v in days.items() if d.weekday() == w]
        raw = (sum(sample) / len(sample)) / mean if sample else 1.0
        n = len(sample)
        out[w] = (n * raw + SHRINK) / (n + SHRINK)
    return out


def fit_line(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Least squares over (x, y) points.

    Returns:
        (intercept, slope, residual standard deviation); a flat line through the mean when there
        are fewer than ``MIN_TREND_DAYS`` points.
    """
    n = len(points)
    if n == 0:
        return 0.0, 0.0, 0.0
    mean_y = sum(y for _, y in points) / n
    if n < MIN_TREND_DAYS:
        sd = math.sqrt(sum((y - mean_y) ** 2 for _, y in points) / n)
        return mean_y, 0.0, sd
    mean_x = sum(x for x, _ in points) / n
    sxx = sum((x - mean_x) ** 2 for x, _ in points)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / sxx if sxx else 0.0
    intercept = mean_y - slope * mean_x
    residuals = [y - (intercept + slope * x) for x, y in points]
    sd = math.sqrt(sum(r * r for r in residuals) / max(n - 2, 1))
    return intercept, slope, sd


def month_end(now: datetime) -> date:
    """The last day of ``now``'s UTC month.

    Returns:
        The date.
    """
    first_next = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
    return first_next.date() - timedelta(days=1)


def forecast(
    daily: dict[date, float],
    now: datetime,
    first: date | None = None,
    last: date | None = None,
) -> Forecast:
    """Forecast a cap's window (the month of ``now`` by default) from daily usage.

    Args:
        daily: Usage per day of ``now``'s time zone, this window and the weeks before (today's
            partial usage included; missing days count as zero from the first day given).
        now: The current time, in the time zone the days are counted in (UTC when naive).
        first: The window's first day (the 1st of the month by default).
        last: The window's last day (the month's last day by default).

    Returns:
        The forecast.
    """
    now = now if now.tzinfo else now.replace(tzinfo=UTC)
    tz = now.tzinfo
    today = now.date()
    first = first or today.replace(day=1)
    so_far = sum(v for d, v in daily.items() if first <= d <= today)
    start = max(min(daily, default=today), today - timedelta(days=HISTORY_DAYS))
    history = {
        start + timedelta(days=i): daily.get(start + timedelta(days=i), 0.0)
        for i in range((today - start).days)
    }
    factors = weekday_factors(history)
    origin = today
    points = [((d - origin).days, v / factors[d.weekday()]) for d, v in sorted(history.items())]
    if not points and daily.get(today):
        # A first day: today's pace so far, as a whole day.
        elapsed = max((now - datetime.combine(today, datetime.min.time(), tz)).total_seconds(), 1)
        points = [(0, daily[today] * 86_400 / elapsed)]
    intercept, slope, sd = fit_line([(float(x), y) for x, y in points])
    last = last or month_end(now)
    elapsed_share = min(
        1.0, (now - datetime.combine(today, datetime.min.time(), tz)).total_seconds() / 86_400
    )
    projection: dict[date, float] = {}
    day = today
    while day <= last:
        x = (day - origin).days
        value = max(0.0, intercept + slope * x) * factors[day.weekday()]
        if day == today:
            value *= max(0.0, 1 - elapsed_share)
        projection[day] = value
        day += timedelta(days=1)
    rest = sum(projection.values())
    days_left = (last - today).days + 1
    spread = sd * math.sqrt(days_left) if points else 0.0
    return Forecast(
        so_far=so_far,
        total=so_far + rest,
        low=so_far + max(0.0, rest - spread),
        high=so_far + rest + spread,
        slope=slope,
        factors=factors,
        projection=projection,
        days_left=days_left,
    )
