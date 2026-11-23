"""Spending caps, enforced by Evokai before every model call.

The caps in effect (:func:`evokai.core.caps.caps_of`) are the application's (month, week, day) and
the subject's (month, week, day): the new ``caps`` in the application's currency, the old
``*_usd`` fields in USD, and a session token's overrides of the subject's. Each has a fixed
calendar window in the application's time zone; the spend of every window is read in one query
(:func:`cap_state`). Every check uses a pessimistic estimate of the next call; spend is recorded
in USD at the price of the model that answered, and each cap is compared in USD.

Browsers only ever see shares of the caps (:meth:`CapState.view`), and so do the people using the
admin console (:func:`app_shares`, :func:`report_shares`); amounts stay server-side, in the
database and in the server-to-server endpoints an application calls with its server key.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from evokai.core.caps import Cap, cap_usd, caps_of, zone_of
from evokai.core.caps import window as cap_window
from evokai.db import models as m
from evokai.i18n import locale_of, message, pack

if TYPE_CHECKING:
    from evokai.db.repositories import Store, Who

_KEYS = {
    "app_month": "limit.app",
    "app_week": "limit.appWeek",
    "app_day": "limit.appDay",
    "subject_month": "limit.subjectMonth",
    "subject_week": "limit.subjectWeek",
    "subject_day": "limit.subjectDay",
}
"""The message of each cap a turn can reach."""


DEFAULT_EVAL_CAP_USD = 2.0
"""What an application's evals may spend in a month unless it sets ``eval_monthly_usd``."""
EVAL_LIMIT = "The eval budget of this application is used up for this month."


def eval_cap_usd(app: m.Application) -> float:
    """The application's monthly eval budget (its own ``eval_monthly_usd``, else the default).

    Returns:
        USD.
    """
    value = (app.defaults or {}).get("eval_monthly_usd")
    return float(value) if value is not None else DEFAULT_EVAL_CAP_USD


def browser_pct(spent: float, cap: float) -> float:
    """A spend as a share of a cap, for browsers (one decimal, at most 100).

    Returns:
        The percentage.
    """
    return 100.0 if cap <= 0 else min(100.0, round(spent / cap * 100, 1))


@dataclass(frozen=True)
class CapUse:
    """A cap, its current window and what was spent in it (USD, server side only)."""

    cap: Cap
    start: datetime
    end: datetime
    """The next reset."""
    spent_usd: float

    @property
    def kind(self) -> str:
        """app_month, subject_week...

        Returns:
            The kind.
        """
        return self.cap.kind

    def fits(self, next_usd: float) -> bool:
        """Whether a call of this cost stays within the cap.

        Returns:
            True when it fits.
        """
        return self.spent_usd + next_usd <= self.cap.usd

    @property
    def pct(self) -> float:
        """The share used, for browsers.

        Returns:
            0 to 100.
        """
        return browser_pct(self.spent_usd, self.cap.usd)

    def share(self) -> dict[str, Any]:
        """The cap as a browser may see it: its share and its reset, never an amount.

        Returns:
            limit, scope, period, usedPct, leftPct, resetsAt.
        """
        used = self.pct
        return {
            "limit": self.kind,
            "scope": self.cap.scope,
            "period": self.cap.period,
            "usedPct": used,
            "leftPct": round(100 - used, 1),
            "resetsAt": self.end.isoformat(),
        }


@dataclass(frozen=True)
class CapState:
    """Every cap in effect for one subject (or for the application alone), with its spend."""

    uses: tuple[CapUse, ...]

    def hit(self, next_usd: float) -> CapUse | None:
        """The cap a further call would break: the application's first, then month, week, day.

        Returns:
            The cap, or None when the call fits.
        """
        return next((u for u in self.uses if not u.fits(next_usd)), None)

    def plus(self, usd: float) -> CapState:
        """The state after a call of this cost.

        Returns:
            A new state.
        """
        return CapState(tuple(replace(u, spent_usd=u.spent_usd + usd) for u in self.uses))

    def get(self, scope: str, period: str) -> CapUse | None:
        """The cap of a scope and period.

        Returns:
            It, or None.
        """
        return next((u for u in self.uses if (u.cap.scope, u.cap.period) == (scope, period)), None)

    def binding(self) -> CapUse | None:
        """The cap that limits most.

        Among the reached ones the one that resets last, else the one with the highest share
        (the later reset on a tie).

        Returns:
            The cap, or None without caps.
        """
        if not self.uses:
            return None
        out = [u for u in self.uses if u.pct >= 100]
        if out:
            return max(out, key=lambda u: u.end)
        return max(self.uses, key=lambda u: (u.pct, u.end))

    def view(self) -> dict[str, Any]:
        """What a browser may see: shares of the caps and when they reset, never amounts.

        Returns:
            dayPct, weekPct and monthPct (the subject's, None without such a cap), companyPct
            (the application's month), monthOut (a monthly cap is reached), out (any cap is
            reached: nothing more until ``resetsAt``), binding (the cap that limits most, as in
            ``caps``), resetsAt (its reset, ISO time) and caps (every cap: limit, scope, period,
            usedPct, leftPct, resetsAt).
        """

        def pct(scope: str, period: str) -> float | None:
            u = self.get(scope, period)
            return u.pct if u else None

        month, app_month = pct("subject", "month"), pct("application", "month")
        binding = self.binding()
        return {
            "dayPct": pct("subject", "day"),
            "weekPct": pct("subject", "week"),
            "monthPct": month,
            "companyPct": app_month,
            "monthOut": (month or 0) >= 100 or (app_month or 0) >= 100,
            "out": any(u.pct >= 100 for u in self.uses),
            "binding": binding.share() if binding else None,
            "resetsAt": binding.end.isoformat() if binding else None,
            "caps": [u.share() for u in self.uses],
        }


def windows_of(caps: list[Cap], now: datetime, app: Any) -> list[tuple[Cap, datetime, datetime]]:
    """Each cap with its current window.

    Returns:
        (cap, start, end) in UTC.
    """
    tz = zone_of(app)
    return [(c, *cap_window(c.period, c.resets, now, tz)) for c in caps]


async def cap_state(
    store: Store,
    app: Any,
    who: Who,
    now: datetime,
    token: dict[str, Any] | None = None,
    *,
    subject: bool = True,
) -> CapState:
    """The caps of the application and of ``who``'s subject, with what each window has spent.

    Args:
        store: Repositories.
        app: The application.
        who: The application and the subject.
        now: The current time.
        token: The session token's caps (``claims.limits``).
        subject: Include the subject's caps.

    Returns:
        The state.
    """
    spans = windows_of(caps_of(app, token, subject=subject), now, app)
    spend = await store.usage.window_spend(
        who, {c.kind: (start, c.scope == "subject") for c, start, _ in spans}
    )
    return CapState(tuple(CapUse(c, s, e, spend.get(c.kind, 0.0)) for c, s, e in spans))


def resets_text(app: Any, use: CapUse, session: Any = None) -> str:
    """When a cap resets, in the application's words and time zone: ``on Saturday at 00:00``.

    Returns:
        The text (in the session's locale, when its token chose one).
    """
    cal = pack(locale_of(app, session)).get("calendar") or {}
    local = use.end.astimezone(zone_of(app)).date()
    if use.cap.period == "day":
        return message(app, "limit.resetsDay", session=session)
    if use.cap.period == "week":
        return message(
            app, "limit.resetsWeek", session=session, weekday=cal["weekdays"][local.weekday()]
        )
    day = cal.get("firstDay", "1") if local.day == 1 else str(local.day)
    return message(
        app, "limit.resetsMonth", session=session, day=day, month=cal["months"][local.month - 1]
    )


def limit_message(app: Any, use: CapUse, session: Any = None) -> str:
    """The text for a reached cap, in the application's locale, with its own overrides.

    Says which cap and when it resets; never an amount.

    Returns:
        The message.
    """
    return message(app, _KEYS[use.kind], session=session, resets=resets_text(app, use, session))


def share_pct(spent: float, cap: float | None) -> float | None:
    """A spend as a share of a cap, for the console (two decimals: small calls still show).

    Returns:
        The percentage (may exceed 100), or None without a cap.
    """
    if cap is None:
        return None
    return 100.0 if cap <= 0 else round(spent / cap * 100, 2)


def left_pct(used: float | None) -> float | None:
    """What is left of a cap, given the share used.

    Returns:
        The percentage left (never below 0), or None without a cap.
    """
    return None if used is None else max(0.0, round(100 - used, 2))


def month_cap_usd(app: Any) -> float | None:
    """The application's monthly cap in USD, what a turn's or a trace's share is of.

    Returns:
        USD, or None.
    """
    return cap_usd(app, "application", "month")


async def app_shares(store: Store, app: Any, now: datetime) -> dict[str, Any]:
    """The application's caps as used and left shares, and its people's (no amounts).

    Returns:
        monthPct, monthLeftPct, weekPct, weekLeftPct, dayPct, dayLeftPct (the application's,
        None without such a cap) and caps: every cap (limit, scope, period, resets, usedPct,
        leftPct, resetsAt); a subject's cap gives the share of the person who used most of it
        in its current window.
    """
    from evokai.costs.service import SYSTEM_SUBJECTS
    from evokai.db.repositories import Who

    spans = windows_of(caps_of(app), now, app)
    own = [(c, s, e) for c, s, e in spans if c.scope == "application"]
    spend = await store.usage.window_spend(Who(app.id, ""), {c.kind: (s, False) for c, s, _ in own})
    out: dict[str, Any] = {}
    caps = []
    for c, start, end in spans:
        if c.scope == "application":
            spent = spend.get(c.kind, 0.0)
        else:
            spent = await store.usage.top_subject_spend(app.id, start, SYSTEM_SUBJECTS)
        used = share_pct(spent, c.usd)
        caps.append({
            "limit": c.kind,
            "scope": c.scope,
            "period": c.period,
            "resets": c.resets_label(),
            "usedPct": used,
            "leftPct": left_pct(used),
            "resetsAt": end.isoformat(),
        })
    for period in ("month", "week", "day"):
        x = next((y for y in caps if y["limit"] == f"app_{period}"), None)
        out[f"{period}Pct"] = x["usedPct"] if x else None
        out[f"{period}LeftPct"] = x["leftPct"] if x else None
    out["caps"] = caps
    return out


def caps_set(app: Any) -> dict[str, bool]:
    """Which caps the application has (their amounts stay on the server).

    Returns:
        monthly, weekly, daily, subjectMonthly, subjectWeekly, subjectDaily.
    """
    have = {(c.scope, c.period) for c in caps_of(app)}
    return {
        "monthly": ("application", "month") in have,
        "weekly": ("application", "week") in have,
        "daily": ("application", "day") in have,
        "subjectMonthly": ("subject", "month") in have,
        "subjectWeekly": ("subject", "week") in have,
        "subjectDaily": ("subject", "day") in have,
    }


def report_shares(
    app: Any, rows: list[dict[str, Any]], subject_today: dict[str, float] | None = None
) -> list[dict[str, Any]]:
    """Usage rows with shares of the caps in place of their amounts (for the console).

    Every row gets ``capPct`` (of the application's monthly cap); a row of a day gets ``dayCapPct``
    (of the daily cap), a row of a subject ``subjectCapPct`` (of the subject's monthly cap) and,
    with ``subject_today``, ``subjectTodayPct`` (what it spent today, of the subject's daily cap).

    Returns:
        The rows without ``costUsd``.
    """
    month, day = cap_usd(app, "application", "month"), cap_usd(app, "application", "day")
    s_month, s_day = cap_usd(app, "subject", "month"), cap_usd(app, "subject", "day")
    out = []
    for r in rows:
        spent = float(r.get("costUsd") or 0)
        row = {k: v for k, v in r.items() if k != "costUsd"}
        row["capPct"] = share_pct(spent, month)
        if "day" in r:
            row["dayCapPct"] = share_pct(spent, day)
        if "subject" in r:
            row["subjectCapPct"] = share_pct(spent, s_month)
            if subject_today is not None:
                row["subjectTodayPct"] = share_pct(subject_today.get(str(r["subject"]), 0.0), s_day)
        out.append(row)
    return out
