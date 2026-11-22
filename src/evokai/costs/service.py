"""Cost governance per application: forecasts, alerts, the cheapest model that passes, caching.

Everything a person sees is a share of a cap or a percentage of the current model's cost; amounts
stay in this module and the database.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from evokai import privacy
from evokai.agents.definitions import AgentDef
from evokai.agents.prompts import split_skills
from evokai.core.caps import Cap, caps_of, key_period, window_key, zone_of
from evokai.core.util import new_id, utcnow
from evokai.costs import alerts as al
from evokai.costs.forecast import HISTORY_DAYS, Forecast, forecast
from evokai.costs.recommend import (
    DEFAULT_TOLERANCE,
    MAX_CASES,
    ModelResult,
    cache_suggestion,
    pick_candidates,
    recommend,
)
from evokai.db import models as m
from evokai.evals.runner import EvalLimit, summarize
from evokai.grants import grants_of
from evokai.llm.catalog import bare, normalize
from evokai.llm.prices import known_models
from evokai.llm.suggestions import CURATED
from evokai.privacy import residency_allows
from evokai.protection import protection
from evokai.sessions.limits import windows_of

if TYPE_CHECKING:
    from evokai.services import Services

log = logging.getLogger("evokai.costs")

TAGS = ["evokai:eval", "evokai:models"]
SYSTEM_SUBJECTS = ("system:", "eval:", "redteam:", "erased:")
"""Subjects that are not people: no per-person forecast."""
TOP_SUBJECTS = 10


def _pct(value: float, cap: float | None) -> float | None:
    if cap is None:
        return None
    return 100.0 if cap <= 0 else round(value / cap * 100, 2)


def period_of(now: datetime) -> str:
    """The month of ``now`` (in its own time zone).

    Returns:
        ``YYYY-MM``.
    """
    return now.strftime("%Y-%m")


def alert_view(a: m.CostAlert) -> dict[str, Any]:
    """An alert for the console and the APIs (shares only).

    Returns:
        id, scope, subjectKey, period (the cap's window, ``2026-11`` or ``2026-11-14w``), cap
        (month, week or day), threshold, forecastPct, usedPct, firedAt, delivery, acknowledgedAt.
    """
    subject = a.scope.split(":", 1)[1] if a.scope.startswith("subject:") else None
    return {
        "id": a.id,
        "scope": "subject" if subject else "application",
        "subjectKey": subject,
        "period": a.period,
        "cap": key_period(a.period),
        "threshold": a.threshold,
        "forecastPct": a.forecast_pct,
        "usedPct": a.used_pct,
        "firedAt": a.fired_at.isoformat(),
        "delivery": {
            k: v for k, v in (a.delivery or {}).items() if k in ("status", "at", "reason")
        },
        "acknowledgedAt": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
    }


class CostService:
    """Forecasts, alerts and recommendations, as one service per process."""

    def __init__(self, svc: Services) -> None:
        """Bind to the process services."""
        self.svc = svc
        self.tasks: set[asyncio.Task[None]] = set()

    async def close(self) -> None:
        """Stop the model comparisons in progress."""
        for t in list(self.tasks):
            t.cancel()
        for t in list(self.tasks):
            with contextlib.suppress(BaseException):
                await t

    # ── usage per day ────────────────────────────────────────────────────────────────────────────

    async def daily(
        self,
        application_id: str,
        start: datetime,
        *,
        by_subject: bool = False,
        tz: ZoneInfo | None = None,
    ) -> dict[str, dict[date, float]]:
        """Usage per day of a time zone that counts against the caps (evals excluded), in USD.

        In UTC the database groups by day; in another zone by UTC hour, each hour then counted on
        its local day (whole-hour offsets are exact).

        Returns:
            ``{"": {day: usd}}`` for the application, or per subject with ``by_subject``.
        """
        hourly = tz is not None and tz.key != "UTC"
        u = m.UsageRecord
        width = 13 if hourly else 10
        day = sa.func.substring(sa.cast(u.created_at, sa.String(40)), 1, width)
        cols = [day.label("day"), sa.func.coalesce(sa.func.sum(u.cost_micro_usd), 0).label("c")]
        group = [day]
        if by_subject:
            cols.insert(0, u.subject.label("subject"))
            group.insert(0, u.subject)
        q = (
            sa
            .select(*cols)
            .where(u.application_id == application_id, u.created_at >= start, u.kind != "eval")
            .group_by(*group)
        )
        async with self.svc.db.session() as s:
            rows = (await s.execute(q)).mappings().all()
        out: dict[str, dict[date, float]] = {}
        for r in rows:
            key = str(r["subject"]) if by_subject else ""
            text = str(r["day"])
            d = date.fromisoformat(text[:10])
            if hourly and tz is not None:
                at = datetime.combine(d, time(int(text[11:13] or 0)), UTC)
                d = at.astimezone(tz).date()
            days = out.setdefault(key, {})
            days[d] = days.get(d, 0.0) + int(r["c"]) / 1e6
        return out

    # ── forecasts ────────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _window(cap: Cap, start: datetime, end: datetime, tz: ZoneInfo) -> tuple[date, date, str]:
        """A window's first and last local day and its key (alerts fire once per key).

        Returns:
            (first, last, key).
        """
        first = start.astimezone(tz).date()
        last = end.astimezone(tz).date() - timedelta(days=1)
        return first, last, window_key(cap, start, tz)

    async def forecast(self, app: m.Application, now: datetime | None = None) -> dict[str, Any]:
        """The forecast of every cap at the end of its window, as shares of the caps.

        Windows are the caps' calendar windows in the application's time zone; caps in another
        currency than USD are converted with the application's rate.

        Returns:
            period (the monthly window's key), application (the monthly cap: capSet, usedPct,
            forecastPct, lowPct, highPct, trend, daysLeft, resetsAt, daily: the days so far as
            usedPct and the days left as forecastPct), caps (every application cap: limit, period,
            resets, resetsAt, window, usedPct, forecastPct, lowPct, highPct, trend, daysLeft),
            subjects (the people most likely to reach a cap: subjectKey, usedPct, forecastPct,
            cap, period, window) and thresholds.
        """
        now = now or utcnow()
        tz = zone_of(app)
        local = now.astimezone(tz)
        start = datetime.combine(local.date(), time(0), tz).astimezone(UTC) - timedelta(
            days=HISTORY_DAYS + 31
        )
        series = (await self.daily(app.id, start, tz=tz)).get("", {})
        caps = []
        month: dict[str, Any] | None = None
        for cap, s, e in windows_of(caps_of(app, subject=False), now, app):
            first, last, key = self._window(cap, s, e, tz)
            f = forecast(series, local, first, last)
            item = {
                "limit": cap.kind,
                "period": cap.period,
                "resets": cap.resets_label(),
                "resetsAt": e.isoformat(),
                "window": key,
                **self._shares(f, cap.usd),
                "trend": f.trend,
                "daysLeft": f.days_left,
            }
            caps.append(item)
            if cap.period == "month":
                daily = [
                    {"day": d.isoformat(), "usedPct": _pct(v, cap.usd)}
                    for d, v in sorted(series.items())
                    if first <= d <= local.date()
                ]
                daily += [
                    {"day": d.isoformat(), "forecastPct": _pct(v, cap.usd)}
                    for d, v in sorted(f.projection.items())
                    if d > local.date()
                ]
                month = {"capSet": True, **item, "daily": daily}
        application = month or {
            "capSet": False,
            **self._shares(forecast(series, local), None),
            "trend": "flat",
            "daysLeft": 0,
            "daily": [],
        }
        return {
            "period": month["window"] if month else period_of(local),
            "application": application,
            "caps": caps,
            "subjects": await self._subjects(app, start, now),
            "thresholds": al.thresholds_of(app.defaults),
        }

    @staticmethod
    def _shares(f: Forecast, cap: float | None) -> dict[str, float | None]:
        return {
            "usedPct": _pct(f.so_far, cap),
            "forecastPct": _pct(f.total, cap),
            "lowPct": _pct(f.low, cap),
            "highPct": _pct(f.high, cap),
        }

    async def _subjects(
        self, app: m.Application, start: datetime, now: datetime
    ) -> list[dict[str, Any]]:
        """The people's forecasts against their caps (else the application's monthly cap).

        Each person is listed with the cap they are most likely to reach.

        Returns:
            The top ones by forecast share.
        """
        tz = zone_of(app)
        local = now.astimezone(tz)
        caps = [x for x in windows_of(caps_of(app), now, app) if x[0].scope == "subject"]
        if not caps:
            caps = windows_of(caps_of(app, subject=False), now, app)
            caps = [x for x in caps if x[0].period == "month"]
        spans = [(c, *self._window(c, s, e, tz)) for c, s, e in caps]
        if not spans:
            return []
        earliest = min(first for _, first, _, _ in spans)
        out = []
        for subject, series in (await self.daily(app.id, start, by_subject=True, tz=tz)).items():
            if subject.startswith(SYSTEM_SUBJECTS):
                continue
            if not any(d >= earliest for d in series):
                continue
            best: dict[str, Any] | None = None
            for cap, first, last, key in spans:
                if not any(d >= first for d in series):
                    continue
                f = forecast(series, local, first, last)
                item: dict[str, Any] = {
                    **self._shares(f, cap.usd),
                    "cap": cap.scope,
                    "period": cap.period,
                    "window": key,
                }
                if best is None or (item["forecastPct"] or 0) > (best["forecastPct"] or 0):
                    best = item
            if best is None:
                continue
            out.append({
                "subject": subject,
                "subjectKey": privacy.subject_key(self.svc.keyring.signing_key, app.id, subject)[
                    :16
                ],
                **best,
            })
        out.sort(key=lambda x: x["forecastPct"] or 0, reverse=True)
        return out[:TOP_SUBJECTS]

    def current_windows(self, app: m.Application, now: datetime | None = None) -> list[str]:
        """The keys of the current windows of every cap of the application.

        Returns:
            Window keys (``2026-11``, ``2026-11-14w``...).
        """
        tz = zone_of(app)
        spans = windows_of(caps_of(app), now or utcnow(), app)
        return sorted({window_key(c, s, tz) for c, s, _ in spans})

    # ── alerts ───────────────────────────────────────────────────────────────────────────────────

    async def evaluate(
        self,
        app: m.Application,
        view: dict[str, Any],
        now: datetime | None = None,  # ruff: ignore[unused-method-argument]
    ) -> list[m.CostAlert]:
        """Fire the alerts a forecast crosses for the first time in a cap's window.

        Returns:
            The new alerts.
        """
        thresholds = al.thresholds_of(app.defaults)
        scopes: list[tuple[str, str, dict[str, Any]]] = [
            ("application", c["window"], c) for c in view.get("caps") or []
        ]
        if any(c.scope == "subject" for c in caps_of(app)):
            scopes += [
                (f"subject:{s['subjectKey']}", s["window"], s)
                for s in view["subjects"]
                if s.get("cap") == "subject"
            ]
        fired = await self._fired(app.id, sorted({w for _, w, _ in scopes}))
        new: list[m.CostAlert] = []
        for scope, period, shares in scopes:
            for threshold in al.crossed(
                shares.get("forecastPct"), thresholds, fired.get((scope, period), set())
            ):
                row = await self._insert(app, scope, period, threshold, shares)
                if row is not None:
                    new.append(row)
        for row in new:
            log.warning(
                "cost_alert",
                extra={
                    "applicationId": app.id,
                    "scope": row.scope.split(":", 1)[0],
                    "cap": key_period(row.period),
                    "threshold": row.threshold,
                    "forecastPct": row.forecast_pct,
                    "usedPct": row.used_pct,
                },
            )
            await self._deliver(app, row)
        return new

    async def _fired(
        self, application_id: str, periods: list[str]
    ) -> dict[tuple[str, str], set[int]]:
        if not periods:
            return {}
        async with self.svc.db.session() as s:
            a = m.CostAlert
            rows = (
                await s.execute(
                    sa.select(a.scope, a.period, a.threshold).where(
                        a.application_id == application_id, a.period.in_(periods)
                    )
                )
            ).all()
        out: dict[tuple[str, str], set[int]] = {}
        for scope, period, threshold in rows:
            out.setdefault((scope, period), set()).add(int(threshold))
        return out

    async def _insert(
        self,
        app: m.Application,
        scope: str,
        period: str,
        threshold: int,
        shares: dict[str, Any],
    ) -> m.CostAlert | None:
        """Store an alert; another instance that stored it first wins (unique constraint).

        Returns:
            The row, or None when it already exists.
        """
        row = m.CostAlert(
            id=new_id(),
            application_id=app.id,
            scope=scope,
            period=period,
            threshold=threshold,
            forecast_pct=float(shares.get("forecastPct") or 0),
            used_pct=float(shares.get("usedPct") or 0),
            fired_at=utcnow(),
            delivery={},
        )
        try:
            async with self.svc.db.session() as s:
                s.add(row)
        except IntegrityError:
            return None
        return row

    async def _deliver(self, app: m.Application, row: m.CostAlert) -> None:
        url = (app.defaults or {}).get("cost_alert_webhook")
        if not url:
            return
        enc = (app.defaults or {}).get("cost_alert_secret")
        secret = self.svc.keyring.decrypt(enc) if enc else None
        view = alert_view(row)
        payload = {
            "type": "evokai.cost_alert",
            "application": app.slug,
            **{k: v for k, v in view.items() if k not in ("delivery", "acknowledgedAt")},
        }
        result = await al.deliver(
            self.svc.http, str(url), secret, payload, self.svc.settings.alert_webhook_allow
        )
        async with self.svc.db.session() as s:
            await s.execute(
                sa.update(m.CostAlert).where(m.CostAlert.id == row.id).values(delivery=result)
            )
        row.delivery = result

    async def check_alerts(self, now: datetime | None = None) -> int:
        """Forecast every active application and fire what crosses a threshold.

        Returns:
            How many alerts fired.
        """
        n = 0
        for app in await self.svc.store.applications.all():
            if app.status != "active":
                continue
            try:
                n += len(await self.evaluate(app, await self.forecast(app, now), now))
            except Exception as e:
                log.warning(
                    "cost_alerts_failed",
                    extra={"applicationId": app.id, "error_type": type(e).__name__},
                )
        return n

    async def alerts(
        self, app: m.Application, period: str | None = None, *, open_only: bool = False
    ) -> list[dict[str, Any]]:
        """The application's alerts of a window (the current one of every cap by default).

        A window is ``2026-11`` (a month from the 1st) or ``2026-11-14w`` (a start and a period).

        Returns:
            Alerts, newest first.
        """
        periods = [period] if period else self.current_windows(app)
        async with self.svc.db.session() as s:
            a = m.CostAlert
            q = sa.select(a).where(a.application_id == app.id, a.period.in_(periods))
            if open_only:
                q = q.where(a.acknowledged_at.is_(None))
            rows = (await s.execute(q.order_by(a.fired_at.desc()))).scalars().all()
        return [alert_view(r) for r in rows]

    async def acknowledge(self, app: m.Application, alert_id: str, by: str) -> bool:
        """Mark an alert as seen (the banner goes).

        Returns:
            Whether it exists.
        """
        async with self.svc.db.session() as s:
            a = m.CostAlert
            res = await s.execute(
                sa
                .update(a)
                .where(a.id == alert_id, a.application_id == app.id)
                .values(acknowledged_at=utcnow(), acknowledged_by=by[:200])
            )
            return int(getattr(res, "rowcount", 0) or 0) > 0

    # ── prompt cache ─────────────────────────────────────────────────────────────────────────────

    async def cache_suggestions(self, app: m.Application) -> list[dict[str, Any]]:
        """Agents whose stable prompt is long enough to cache and whose cache is off.

        Returns:
            Suggestions (agent, model, stableTokens, minTokens, inputSharePct, suggested).
        """
        svc = self.svc
        grants = await grants_of(svc.store, app)
        out = []
        month = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        u = m.UsageRecord
        async with svc.db.session() as s:
            rows = (
                await s.execute(
                    sa
                    .select(u.agent_name, sa.func.avg(u.input_tokens))
                    .where(u.application_id == app.id, u.created_at >= month, u.kind == "llm")
                    .group_by(u.agent_name)
                )
            ).all()
        avg = {str(a): float(v or 0) for a, v in rows}
        for record in await svc.store.agents.active_all(app.id):
            agent = AgentDef.model_validate({**record.definition, "version": record.version})
            resolved = await svc.engine._instructions(app.id, agent)
            instructions = resolved.text if resolved else agent.instructions
            preloaded, on_demand = split_skills(
                agent, await svc.engine._skills(app.id, agent, grants)
            )
            chars = len(instructions) + sum(len(x.body) + len(x.description) for x in preloaded)
            chars += sum(len(x.name) + len(x.description) + 4 for x in on_demand)
            hint = cache_suggestion(
                agent=agent.name,
                model=agent.model,
                prompt_cache=agent.prompt_cache,
                stable_chars=chars,
                avg_input_tokens=avg.get(agent.name),
            )
            if hint:
                out.append(hint)
        return out

    # ── the cheapest model that passes ───────────────────────────────────────────────────────────

    async def candidates(self, app: m.Application, agent: AgentDef) -> list[str]:
        """The cheaper models this agent may use: allow-lists, residency, grants (as primary).

        Returns:
            Models, cheapest first (at most ``MAX_CANDIDATES``).
        """
        svc = self.svc
        grants = await grants_of(svc.store, app)
        pool = [agent.model, *agent.models, *agent.fallbacks]
        pool += known_models()
        pool += [f"bedrock:{mid}" for mid, _, provider in CURATED if provider == "bedrock"]
        pool += [p for p in app.allowed_models or [] if not any(c in p for c in "*?[")]
        usable = []
        for model in dict.fromkeys(normalize(x) for x in pool):
            if not svc.catalog.allowed(model):
                continue
            if not residency_allows(protection(app), model, svc.settings.aws_region):
                continue
            allowed = app.allowed_models or []
            if allowed and not any(fnmatch.fnmatchcase(model, normalize(p)) for p in allowed):
                continue
            if grants.model(model) != "any":
                continue
            usable.append(model)
        return pick_candidates(normalize(agent.model), usable)

    async def start_comparison(
        self,
        app: m.Application,
        agent_name: str,
        *,
        created_by: str | None,
        tolerance: float = DEFAULT_TOLERANCE,
    ) -> m.EvalRun:
        """Run the agent's cases on its model and on the cheaper allowed ones, in the background.

        Returns:
            The run (suite ``models``, status ``running``).

        Raises:
            LookupError: No active agent.
        """
        record = await self.svc.store.agents.active(app.id, agent_name)
        if record is None:
            raise LookupError(agent_name)
        agent = AgentDef.model_validate({**record.definition, "version": record.version})
        models = await self.candidates(app, agent)
        cases = [c for c in await self.svc.store.evals.cases(app.id, agent_name) if c.enabled][
            :MAX_CASES
        ]
        run = await self.svc.store.evals.start_run(
            app.id,
            agent_name,
            version=record.version,
            baseline=None,
            trigger="manual",
            created_by=created_by,
            summary={
                "current": normalize(agent.model),
                "candidates": models,
                "tolerance": tolerance,
                "cases": len(cases),
            },
            suite="models",
        )
        task = asyncio.create_task(self._compare(app, run, normalize(agent.model), models, cases))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return run

    async def _compare(
        self,
        app: m.Application,
        run: m.EvalRun,
        current: str,
        models: list[str],
        cases: list[m.EvalCase],
    ) -> None:
        store = self.svc.store
        rows: dict[str, list[dict[str, Any]]] = {x: [] for x in [current, *models]}
        status = "done"
        try:
            for case in cases:
                for model in rows:
                    row = await self.svc.evals.run_case(
                        app, run.agent_name, run.version, case, model=model, tags=TAGS
                    )
                    rows[model].append(row)
                    await store.evals.add_result(run.id, variant=model, **row)
        except EvalLimit:
            status = "limit"
        except asyncio.CancelledError:
            status = "interrupted"
            raise
        except Exception as e:
            log.exception("model_comparison_failed", extra={"error_type": type(e).__name__})
            status = "failed"
        finally:
            summary = {
                **(run.summary or {}),
                "models": {x: summarize(r) for x, r in rows.items()},
            }
            with contextlib.suppress(Exception):
                await asyncio.shield(store.evals.finish_run(run.id, status, summary))

    def comparison_view(self, run: m.EvalRun) -> dict[str, Any]:
        """A model comparison for people: pass rates and costs relative to the current model.

        Returns:
            id, status, version, current, tolerance, cases, startedAt, endedAt, models,
            recommended.
        """
        s = run.summary or {}
        current = str(s.get("current") or "")
        per = s.get("models") or {}

        def result(model: str) -> ModelResult:
            x = per.get(model) or {}
            return ModelResult(
                model=model,
                pass_rate=x.get("passRate"),
                cost_micro_usd=int(x.get("costMicroUsd") or 0),
                latency_ms=x.get("latencyMs"),
                cases=int(x.get("total") or 0),
                errors=int(x.get("errors") or 0),
            )

        out: dict[str, Any] = {
            "id": run.id,
            "status": run.status,
            "version": run.version,
            "current": current,
            "candidates": s.get("candidates") or [],
            "tolerance": s.get("tolerance", DEFAULT_TOLERANCE),
            "cases": s.get("cases"),
            "startedAt": run.started_at.isoformat(),
            "endedAt": run.ended_at.isoformat() if run.ended_at else None,
            "models": [],
            "recommended": None,
        }
        if per and current:
            others = [result(x) for x in per if x != current]
            out.update(
                recommend(result(current), others, float(s.get("tolerance", DEFAULT_TOLERANCE)))
            )
        for x in out["models"]:
            x["label"] = bare(x["model"])
        return out
