"""Spending caps: whom they bind, over which calendar window, and in which currency.

A cap is a maximum over a fixed calendar window, never a rolling one:

- ``day``: from 00:00 to 00:00 the next day;
- ``week``: seven days from 00:00 of its reset weekday (``resets: saturday``; Monday by default);
- ``month``: from 00:00 of its reset day of the month to the same day of the next month
  (``resets: 15``; the 1st by default; a day the month does not have means its last day).

Times are wall-clock times of the application's time zone (``timezone``, UTC by default), so a
window starts at local midnight on both sides of a daylight saving change.

Caps are expressed in the application's currency (``currency``, USD by default). Provider prices
and the recorded spend stay in USD; a cap in another currency is converted with the application's
``exchange_rate`` (units of its currency per 1 USD). The old fields (``monthly_cap_usd``,
``daily_cap_usd``, ``subject_monthly_usd``, ``subject_daily_usd``) keep their meaning: a monthly
or daily cap in USD, whatever the currency. An application always has a monthly cap: when its
``caps`` set none, ``monthly_cap_usd`` (10 USD unless changed) applies.

Pure functions and models only: the database and the routes build on them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

log = logging.getLogger("evokai.caps")

Period = Literal["day", "week", "month"]
Scope = Literal["application", "subject"]
PERIODS: tuple[str, ...] = ("month", "week", "day")
"""In the order caps are checked within a scope."""
SCOPES: tuple[str, ...] = ("application", "subject")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
DEFAULT_CURRENCY = "USD"
DEFAULT_MONTHLY_USD = 10.0
"""The old monthly cap of a new application (``monthly_cap_usd``)."""
CURRENCY = re.compile(r"^[A-Z]{3}$")
"""An ISO 4217 code (USD, EUR, GBP...)."""

KINDS = {
    ("application", "month"): "app_month",
    ("application", "week"): "app_week",
    ("application", "day"): "app_day",
    ("subject", "month"): "subject_month",
    ("subject", "week"): "subject_week",
    ("subject", "day"): "subject_day",
}
"""The ``limit`` a blocked turn reports, per scope and period."""

LEGACY = {
    ("application", "month"): "monthly_cap_usd",
    ("application", "day"): "daily_cap_usd",
    ("subject", "month"): "subject_monthly_usd",
    ("subject", "day"): "subject_daily_usd",
}
"""The old fields, in USD, and the cap each one stands for."""


class CapSpec(BaseModel):
    """One cap: a period, the most it allows in the application's currency, when it resets."""

    model_config = ConfigDict(extra="forbid")

    period: Period
    amount: float = Field(ge=0)
    """The maximum over one window, in the application's currency."""
    resets: str | int | None = None
    """``week``: the weekday its window starts on (``monday`` by default); ``month``: the day of
    the month (1 to 31, 1 by default; a later day than a month has means its last day); ``day``:
    nothing (always 00:00). Always at 00:00 in the application's time zone."""

    @field_validator("resets", mode="before")
    @classmethod
    def _lower(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip().isdigit():
            return v.strip().lower()
        if isinstance(v, str):
            return int(v)
        return v

    @model_validator(mode="after")
    def _check_resets(self) -> CapSpec:
        if self.period == "day" and self.resets is not None:
            raise ValueError("a daily cap resets at 00:00: resets is not allowed")
        if self.period == "week" and self.resets is not None and self.resets not in WEEKDAYS:
            raise ValueError(f"a weekly cap resets on a weekday: {', '.join(WEEKDAYS)}")
        if self.period == "month" and self.resets is not None:
            v = self.resets
            if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 31:
                raise ValueError("a monthly cap resets on a day of the month, 1 to 31")
        return self

    def explicit(self) -> dict[str, Any]:
        """The cap with its reset spelled out (``monday``, ``1``), as the application keeps it.

        Returns:
            period, amount and, for a week or a month, resets.
        """
        out: dict[str, Any] = {"period": self.period, "amount": self.amount}
        if self.period == "week":
            out["resets"] = self.resets or "monday"
        elif self.period == "month":
            out["resets"] = self.resets or 1
        return out


def _one_per_period(caps: list[CapSpec], where: str) -> list[CapSpec]:
    seen = [c.period for c in caps]
    if len(seen) != len(set(seen)):
        raise ValueError(f"{where}: one cap per period")
    return sorted(caps, key=lambda c: PERIODS.index(c.period))


class CapsSpec(BaseModel):
    """The caps of an application (``application``) and of each person using it (``subject``)."""

    model_config = ConfigDict(extra="forbid")

    application: list[CapSpec] = Field(default_factory=list, max_length=3)
    """What the whole application may spend per window."""
    subject: list[CapSpec] = Field(default_factory=list, max_length=3)
    """What each subject (person) may spend per window; a session token may override them."""

    @model_validator(mode="after")
    def _unique(self) -> CapsSpec:
        self.application = _one_per_period(self.application, "caps.application")
        self.subject = _one_per_period(self.subject, "caps.subject")
        return self

    def stored(self) -> dict[str, Any] | None:
        """As the application row keeps it.

        Returns:
            The caps, or None when there are none.
        """
        out = {
            "application": [c.explicit() for c in self.application],
            "subject": [c.explicit() for c in self.subject],
        }
        return out if out["application"] or out["subject"] else None


def check_currency(currency: str, rate: float | None) -> None:
    """A currency and its exchange rate, as an application may set them.

    Raises:
        ValueError: Not an ISO code, a currency other than USD without a positive rate, or a
            rate that is not positive.
    """
    if not CURRENCY.fullmatch(currency):
        raise ValueError("currency is an ISO 4217 code such as USD or EUR")
    if rate is not None and not rate > 0:
        raise ValueError("exchange_rate is greater than zero")
    if currency != DEFAULT_CURRENCY and rate is None:
        raise ValueError(
            f"exchange_rate (units of {currency} per 1 USD) is required for {currency}"
        )


def legacy_conflicts(new: CapsSpec | dict[str, Any] | None, old: dict[str, Any]) -> list[str]:
    """The caps set both in the new form and through an old ``*_usd`` field.

    Args:
        new: The new form (``caps``).
        old: Old fields by name (``monthly_cap_usd``...), None meaning unset.

    Returns:
        Messages, one per conflicting cap (empty when there is none).
    """
    if new is None:
        return []
    spec = new if isinstance(new, CapsSpec) else CapsSpec.model_validate(new)
    out = []
    for scope in SCOPES:
        for cap in getattr(spec, scope):
            name = LEGACY.get((scope, cap.period))
            if name and old.get(name) is not None:
                out.append(f"{name} and caps.{scope} ({cap.period}) set the same cap: keep one")
    return out


def zone_of(app: Any) -> ZoneInfo:
    """The application's time zone (``defaults.timezone``), UTC when unset or unknown.

    Returns:
        The zone.
    """
    name = str(((getattr(app, "defaults", None) or {}).get("timezone")) or "UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown_timezone", extra={"timezone": name[:64]})
        return ZoneInfo("UTC")


def currency_of(app: Any) -> str:
    """The application's currency.

    Returns:
        An ISO code, USD by default.
    """
    return str(getattr(app, "currency", None) or DEFAULT_CURRENCY)


def rate_of(app: Any) -> float | None:
    """Units of the application's currency per 1 USD.

    Returns:
        1 for USD, the application's ``exchange_rate`` otherwise (None when it is missing).
    """
    if currency_of(app) == DEFAULT_CURRENCY:
        return 1.0
    rate = getattr(app, "exchange_rate", None)
    return float(rate) if rate and rate > 0 else None


def to_usd(amount: float, currency: str, rate: float | None) -> float:
    """An amount in a currency, in USD.

    Returns:
        USD; 0 for a currency without a rate (a cap that cannot be converted blocks, never lets
        everything through).
    """
    if currency == DEFAULT_CURRENCY:
        return amount
    if not rate:
        log.warning("cap_without_exchange_rate", extra={"currency": currency})
        return 0.0
    return amount / rate


def _midnight(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time(0), tzinfo=tz).astimezone(UTC)


def _month_day(year: int, month: int, day: int) -> date:
    first_next = date(year + month // 12, month % 12 + 1, 1)
    return date(year, month, min(day, (first_next - timedelta(days=1)).day))


def window(
    period: str, resets: int | None, now: datetime, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """The calendar window of a cap that contains ``now``.

    Args:
        period: day, week or month.
        resets: The weekday (0 = Monday) of a week, the day of the month of a month.
        now: An aware time.
        tz: The application's time zone.

    Returns:
        Its start and its end (the next reset), in UTC.
    """
    today = now.astimezone(tz).date()
    if period == "day":
        start, end = today, today + timedelta(days=1)
    elif period == "week":
        start = today - timedelta(days=(today.weekday() - (resets or 0)) % 7)
        end = start + timedelta(days=7)
    else:
        day = resets or 1
        this = _month_day(today.year, today.month, day)
        if today >= this:
            y, mo = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            start, end = this, _month_day(y, mo, day)
        else:
            y, mo = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
            start, end = _month_day(y, mo, day), this
    return _midnight(start, tz), _midnight(end, tz)


@dataclass(frozen=True)
class Cap:
    """A cap in effect, whatever form set it."""

    scope: str
    """application or subject."""
    period: str
    """day, week or month."""
    amount: float
    """The maximum, in ``currency``."""
    currency: str
    usd: float
    """The same maximum in USD, what the recorded spend is compared with."""
    resets: int | None
    """The weekday (0 = Monday) of a weekly cap, the day of the month of a monthly cap."""
    source: str
    """``caps`` (the new form), ``legacy`` (an old ``*_usd`` field) or ``token`` (the session)."""

    @property
    def kind(self) -> str:
        """The ``limit`` a blocked turn reports (app_month, subject_week...).

        Returns:
            The kind.
        """
        return KINDS[self.scope, self.period]

    def resets_label(self) -> str | int | None:
        """When it resets, as the config writes it (``saturday``, ``15``).

        Returns:
            A weekday, a day of the month or None for a day.
        """
        if self.period == "week":
            return WEEKDAYS[self.resets or 0]
        return self.resets if self.period == "month" else None

    def view(self) -> dict[str, Any]:
        """The cap as an admin configured it (a maximum, for the admin API and console).

        Returns:
            kind, scope, period, amount, currency, resets, source.
        """
        return {
            "kind": self.kind,
            "scope": self.scope,
            "period": self.period,
            "amount": self.amount,
            "currency": self.currency,
            "resets": self.resets_label(),
            "source": self.source,
        }


def _reset_value(spec: CapSpec) -> int | None:
    if spec.period == "week":
        return WEEKDAYS.index(str(spec.resets or "monday"))
    return int(spec.resets or 1) if spec.period == "month" else None


def caps_of(app: Any, token: dict[str, Any] | None = None, *, subject: bool = True) -> list[Cap]:
    """Every cap in effect for an application and, with ``subject``, for one of its subjects.

    Args:
        app: The application (``caps``, ``currency``, ``exchange_rate``, the old fields).
        token: The session token's overrides of the subject's caps: the old
            ``subject_monthly_usd`` and ``subject_daily_usd`` (USD) and ``caps``, a list of caps
            in the application's currency; each replaces the subject's cap of its period.
        subject: Include the subject's caps (off for work no person asked for).

    Returns:
        Caps, the application's first, then month, week, day.
    """
    currency, rate = currency_of(app), rate_of(app)
    stored = getattr(app, "caps", None) or {}
    defaults = getattr(app, "defaults", None) or {}
    tok = token or {}
    out: dict[tuple[str, str], Cap] = {}

    def add_new(scope: str, raw: Any, source: str, inherit: bool = False) -> None:
        spec = raw if isinstance(raw, CapSpec) else CapSpec.model_validate(raw)
        resets = _reset_value(spec)
        known = out.get((scope, spec.period))
        if inherit and known is not None and spec.resets is None:
            resets = known.resets
        usd = to_usd(spec.amount, currency, rate)
        out[scope, spec.period] = Cap(
            scope, spec.period, spec.amount, currency, usd, resets, source
        )

    def add_usd(scope: str, period: str, value: Any, source: str) -> None:
        usd = float(value)
        resets = 1 if period == "month" else None
        out[scope, period] = Cap(scope, period, usd, DEFAULT_CURRENCY, usd, resets, source)

    for raw in stored.get("application") or []:
        add_new("application", raw, "caps")
    for (scope, period), name in LEGACY.items():
        if scope != "application" or (scope, period) in out:
            continue
        value = getattr(app, name, None)
        if value is not None:
            add_usd(scope, period, value, "legacy")
    if subject:
        for raw in stored.get("subject") or []:
            add_new("subject", raw, "caps")
        for (scope, period), name in LEGACY.items():
            if scope == "subject" and (scope, period) not in out and defaults.get(name) is not None:
                add_usd(scope, period, defaults[name], "legacy")
        for (scope, period), name in LEGACY.items():
            if scope == "subject" and tok.get(name) is not None:
                add_usd(scope, period, tok[name], "token")
        for raw in tok.get("caps") or []:
            add_new("subject", raw, "token", inherit=True)
    return sorted(out.values(), key=lambda c: (SCOPES.index(c.scope), PERIODS.index(c.period)))


def month_cap(app: Any) -> Cap | None:
    """The application's monthly cap (the new form's, else ``monthly_cap_usd``).

    Returns:
        The cap, or None.
    """
    return next((c for c in caps_of(app, subject=False) if c.period == "month"), None)


def cap_usd(app: Any, scope: str, period: str) -> float | None:
    """A cap of the application's defaults in USD (no token overrides).

    Returns:
        USD, or None without such a cap.
    """
    return next((c.usd for c in caps_of(app) if (c.scope, c.period) == (scope, period)), None)


def window_key(cap: Cap, start: datetime, tz: ZoneInfo) -> str:
    """A short name of a cap's window, for alerts (once per window and threshold).

    Returns:
        ``YYYY-MM`` for a month that starts on the 1st (as before windows existed), else the
        local start date with ``m``, ``w`` or ``d``: ``2026-11-14w``.
    """
    local = start.astimezone(tz).date()
    if cap.period == "month" and local.day == 1:
        return local.strftime("%Y-%m")
    return f"{local.isoformat()}{cap.period[0]}"


def key_period(key: str) -> str:
    """The period of a window key (:func:`window_key`).

    Returns:
        month, week or day.
    """
    return {"w": "week", "d": "day", "m": "month"}.get(key[-1:], "month")
