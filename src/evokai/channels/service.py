"""Deliveries from the channels become runs and turns; their answers become replies.

```
e-mail ── IMAP poll or signed webhook ──▶ loop protection ──▶ subject ──▶ server run (channel email)
                                                         ──▶ reply sent or drafted, in the thread
Teams  ── Bot Framework JWT ──▶ subject ──▶ turn (channel teams) ──▶ proactive reply, adaptive cards
Slack  ── signed event ──▶ subject ──▶ turn (channel slack) ──▶ reply in the thread, buttons
```

- An e-mail is **untrusted input** (a server run scans it for hidden instructions and marks it as
  data); Teams and Slack messages are a person talking to the agent, like the web panel, so they
  are turns with confirmations, questions and connection cards.
- Each channel maps its person to a subject (``evokai.channels.mapping``); a thread of the
  channel is bound to one subject's conversation, so a reply or a button pressed by someone else
  never continues another person's conversation.
- Rate limits per person and per channel apply before anything runs, and the application's guard
  and caps apply to every run and turn.
- Deliveries are answered at once and handled in the background (Slack and Teams retry slow
  answers); a repeated delivery is handled once.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from evokai.channels import email as mail
from evokai.channels.config import EmailConfig, SlackConfig, TeamsConfig, env_names, validate
from evokai.channels.mapping import ChannelUser, Mapper, MappingRefused
from evokai.channels.slack import Slack, button
from evokai.channels.slack import text_of as slack_text
from evokai.channels.teams import Teams, card, reference_of
from evokai.channels.teams import text_of as teams_text
from evokai.core.errors import AppError, NotFound
from evokai.core.security import SessionClaims
from evokai.core.util import new_id
from evokai.db import models as m
from evokai.db.repositories import Who
from evokai.grants import grants_of
from evokai.i18n import message
from evokai.identity.models import SessionPrincipal
from evokai.sessions.runs import AgentRunner, Attachment
from evokai.sessions.turns import TurnInput

log = logging.getLogger("evokai.channels")

SEEN_TTL = 7 * 86_400
"""How long a delivery's id is remembered (a repeated delivery is handled once)."""
POLL_EVERY = 15
"""Seconds between two looks at the mailboxes due for a poll."""
QUOTED = re.compile(
    r"^(On .{3,200} wrote:|Il .{3,200} ha scritto:|-{2,}\s*Original Message\s*-{2,}|"
    r"-{2,}\s*Messaggio originale\s*-{2,}|From: .+|Da: .+)\s*$",
    re.I | re.M,
)


def _h(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def strip_quoted(text: str) -> str:
    """A reply without the earlier messages it quotes (the conversation already has them).

    Returns:
        The new text (the whole text when nothing new is left).
    """
    found = QUOTED.search(text)
    head = text[: found.start()] if found else text
    lines = [x for x in head.splitlines() if not x.lstrip().startswith(">")]
    new = "\n".join(lines).strip()
    return new or text.strip()


def slack_markdown(text: str) -> str:
    """Markdown as Slack writes it (bold, links, headings).

    Returns:
        The text.
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<\2|\1>", text)
    return re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", text, flags=re.M)


@dataclass
class Outcome:
    """What a turn produced, for a channel to render."""

    reason: str = "end_turn"
    text: str = ""
    confirm: dict[str, Any] | None = None
    ask: dict[str, Any] | None = None
    connects: list[dict[str, Any]] = field(default_factory=list)
    """Providers to connect, each with its ``url`` when one could be made."""
    error: str | None = None


def confirm_items(app: Any, confirm: dict[str, Any]) -> list[str]:
    """The items of a confirmation card, each with why it waits when a rule or the policy asked.

    Returns:
        One line per item (a change needs no reason: the card says it).
    """
    why = confirm.get("why") or []
    out = []
    for i, item in enumerate(confirm.get("items") or []):
        w = why[i] if i < len(why) and isinstance(why[i], dict) else {}
        key = w.get("key")
        reason = ""
        if key == "confirm.why.rule":
            reason = str(w.get("label") or message(app, "channel.whyRule"))
        elif key == "confirm.why.policy":
            reason = message(app, "channel.whyPolicy")
        out.append(f"{item} ({reason})" if reason else str(item))
    return out


class ChannelService:
    """Every channel of the process: deliveries, pollers, configuration checks."""

    def __init__(self, svc: Any) -> None:
        """Wire the service.

        Args:
            svc: The process services.
        """
        self.svc = svc
        self.mapper = Mapper(svc.http, svc.keyring.signing_key)
        self.mailer = mail.Mailer(svc.http)
        self.teams = Teams(svc.http)
        self.slack = Slack(svc.http)
        self.runner = AgentRunner(svc.engine)
        self._tasks: set[asyncio.Task] = set()
        self._poller: asyncio.Task | None = None

    # lifecycle

    def spawn(self, coro: Any) -> asyncio.Task:
        """Handle a delivery in the background (kept until it ends).

        Returns:
            The task.
        """
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        """Wait for the deliveries being handled (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def start(self) -> None:
        """Start polling the IMAP mailboxes."""
        if self._poller is None:
            self._poller = asyncio.create_task(self._poll_loop())

    async def close(self) -> None:
        """Stop polling and the deliveries being handled."""
        if self._poller is not None:
            self._poller.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._poller
            self._poller = None
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # lookup

    async def channel(
        self, app_slug: str, kind: str, name: str
    ) -> tuple[m.Application, m.ChannelRecord, Any]:
        """An enabled channel of an active application, with its configuration.

        Returns:
            The application, the channel and its validated configuration.

        Raises:
            NotFound: Unknown, disabled, or with an invalid configuration.
        """
        app = await self.svc.store.applications.by_slug(app_slug)
        rec = await self.svc.store.channels.get(app.id, kind, name) if app else None
        if app is None or app.status != "active" or rec is None or not rec.enabled:
            raise NotFound("Unknown channel.", code="unknown_channel")
        try:
            cfg = validate(kind, rec.config)
        except AppError as e:
            raise NotFound("Unknown channel.", code="unknown_channel") from e
        return app, rec, cfg

    # shared steps

    async def _once(self, rec: m.ChannelRecord, delivery: str) -> bool:
        return await self.svc.shared.set_once(
            f"channel:seen:{rec.id}:{_h(delivery)[:32]}", "1", SEEN_TTL
        )

    async def _rate(self, rec: m.ChannelRecord, cfg: Any, subject: str) -> bool:
        """Whether a delivery is within the channel's rate limits.

        Returns:
            True when it may go on.
        """
        shared = self.svc.shared
        per_user = await shared.incr(f"channel:rate:{rec.id}:{_h(subject)[:24]}", 3600)
        per_channel = await shared.incr(f"channel:rate:{rec.id}", 60)
        return (
            per_user <= cfg.rate.per_user_per_hour
            and per_channel <= cfg.rate.per_channel_per_minute
        )

    async def _subject(
        self, app: m.Application, rec: m.ChannelRecord, cfg: Any, user: ChannelUser
    ) -> str | None:
        try:
            return await self.mapper.subject(app.id, rec.name, cfg.mapping, user)
        except (MappingRefused, AppError) as e:
            log.info("channel_unknown_person", extra={"channel": rec.kind, "reason": str(e)[:80]})
            return None

    async def _failed(self, rec: m.ChannelRecord, e: BaseException) -> None:
        log.warning(
            "channel_delivery_failed",
            extra={"channel": rec.kind, "channelName": rec.name, "error": type(e).__name__},
        )
        with contextlib.suppress(Exception):
            await self.svc.store.channels.seen(
                rec.id, status="error", error=getattr(e, "message", None) or type(e).__name__
            )

    async def _turn(
        self,
        app: m.Application,
        rec: m.ChannelRecord,
        subject: str,
        conversation_id: str,
        *,
        text: str | None = None,
        confirm: dict[str, Any] | None = None,
    ) -> Outcome:
        """Run a turn of the conversation for the person, and collect what it showed.

        Returns:
            The outcome.
        """
        claims = SessionClaims(
            application_id=app.id,
            subject=subject,
            agent=rec.agent,
            conversation_id=conversation_id,
            token_id=f"channel:{rec.id}",
            expires_at=int(time.time()) + 900,
            tags=[f"channel:{rec.kind}"],
            storage_mode="evokai",
            issued_at=time.time(),
        )
        inp = TurnInput(
            principal=SessionPrincipal(claims=claims),
            text=text,
            confirm=confirm,
            channel=rec.kind,
        )
        out = Outcome()
        try:
            run = await self.svc.engine.open(inp)
        except AppError as e:
            out.reason, out.error = (
                "refused",
                (
                    message(app, "channel.tooMany")
                    if e.status == 429
                    else message(app, "channel.notAvailable")
                ),
            )
            log.info("channel_turn_refused", extra={"channel": rec.kind, "code": e.code})
            return out
        events: list[dict[str, Any]] = []

        def on_event(e: dict[str, Any]) -> None:
            if e.get("turnId") == run.turn_id:
                events.append(e)

        stop = run.channel.listen(on_event)
        try:
            await self.svc.engine.execute(run, inp)
        finally:
            stop()
            self.svc.hub.drop_if_idle(run.session.id)
        out.text = "".join(e.get("delta", "") for e in events if e["type"] == "text").strip()
        for e in events:
            if e["type"] == "confirm":
                out.confirm = e
            elif e["type"] == "ask":
                out.ask = e
            elif e["type"] == "connect":
                out.connects.append(await self._connect(app, subject, conversation_id, rec, e))
            elif e["type"] == "error":
                out.error = str(e.get("message") or "")
            elif e["type"] == "done":
                out.reason = str(e.get("reason") or "end_turn")
        return out

    async def _connect(
        self,
        app: m.Application,
        subject: str,
        conversation_id: str,
        rec: m.ChannelRecord,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        """A connection card's link for a person of a channel (no session there to start it).

        Returns:
            The event with ``url`` when one could be made.
        """
        conns = self.svc.connections
        try:
            started = await conns.start(
                app,
                subject,
                str(event.get("provider")),
                redirect_uri=conns.redirect_uri(),
                conversation_id=conversation_id,
                via=rec.kind,
            )
            return {**event, "url": started["authorizeUrl"]}
        except AppError as e:
            log.info("channel_connect_unavailable", extra={"code": e.code})
            return dict(event)

    async def _conversation(
        self, rec: m.ChannelRecord, subject: str, key: str, reference: dict[str, Any]
    ) -> str:
        """The conversation of a channel thread (a new one for a new thread).

        Returns:
            The conversation id.
        """
        store = self.svc.store.channels
        th = await store.thread(rec.id, [key])
        conversation_id = th.conversation_id if th else new_id()
        await store.link(
            rec, key, subject=subject, conversation_id=conversation_id, reference=reference
        )
        return conversation_id

    # e-mail

    async def email(
        self, app: m.Application, rec: m.ChannelRecord, cfg: EmailConfig, raw: bytes
    ) -> str:
        """Handle one inbound message.

        Returns:
            What happened: ``sent``, ``drafted``, ``answered`` (no reply), or why it was ignored
            (``duplicate``, ``rate``, ``unknown``, a loop reason, ``unreadable``, ``failed``).
        """
        try:
            msg = mail.parse(raw, cfg.max_body_chars)
        except ValueError:
            return "unreadable"
        reason = mail.loop_reason(msg, cfg)
        if reason is not None:
            log.info("channel_mail_ignored", extra={"reason": reason, "channelName": rec.name})
            return reason
        if not await self._once(rec, msg.message_id):
            return "duplicate"
        replies = await self.svc.shared.incr(
            f"channel:replies:{rec.id}:{_h(msg.sender)[:24]}", 3600
        )
        if replies > cfg.max_replies_per_sender_per_hour:
            log.info("channel_mail_ignored", extra={"reason": "rate", "channelName": rec.name})
            return "rate"
        user = ChannelUser("email", msg.sender, name=msg.sender_name or None, email=msg.sender)
        subject = await self._subject(app, rec, cfg, user)
        if subject is None:
            return "unknown"
        if not await self._rate(rec, cfg, subject):
            return "rate"
        try:
            return await self._email_run(app, rec, cfg, msg, subject)
        except Exception as e:
            await self._failed(rec, e)
            return "failed"

    async def _email_run(
        self,
        app: m.Application,
        rec: m.ChannelRecord,
        cfg: EmailConfig,
        msg: mail.Inbound,
        subject: str,
    ) -> str:
        store = self.svc.store
        who = Who(app.id, subject)
        keeps = (app.storage_mode or "evokai") == "evokai"
        keys = [_h(subject, r) for r in msg.references]
        th = await store.channels.thread(rec.id, keys) if keys else None
        conversation_id = th.conversation_id if th else None
        history: list[dict[str, Any]] = []
        sess = await store.sessions.get(who, conversation_id) if conversation_id else None
        if sess is not None:
            thread = await store.sessions.thread(sess.id)
            history = [
                {"role": x.role, "text": x.text}
                for x in await store.messages.recent(who, thread.id, 20)
                if x.text
            ]
        body = strip_quoted(msg.text) if history else msg.text
        attachments = await self._attachments(app, cfg, msg)
        agent = await self.svc.agents.active(app.id, rec.agent)
        result = await self.runner.run(
            app,
            agent,
            text=f"E-mail from {msg.sender_name or 'the sender'}. Subject: {msg.subject}\n\n{body}",
            messages=history or None,
            attachments=attachments,
            subject=subject,
            tags=["channel:email"],
            store_conversation=keeps,
            channel="email",
            conversation_id=conversation_id if sess is not None else None,
        )
        answer = result.text or (
            result.output
            if isinstance(result.output, str)
            else json.dumps(result.output, ensure_ascii=False, indent=2)
        )
        outcome = "answered"
        out_id = None
        if cfg.reply != "none" and answer:
            reply = mail.reply(cfg, msg, answer, rec.name)
            out_id = str(reply["Message-ID"])
            outcome = await self.mailer.deliver(cfg, reply)
        if keeps and result.conversation_id:
            for mid in [msg.message_id, *([out_id] if out_id else [])]:
                await store.channels.link(
                    rec,
                    _h(subject, mid),
                    subject=subject,
                    conversation_id=result.conversation_id,
                    reference={"kind": "email"},
                )
        await store.channels.seen(rec.id, status="ok")
        log.info("channel_mail", extra={"channelName": rec.name, "outcome": outcome})
        return outcome

    async def _attachments(
        self, app: m.Application, cfg: EmailConfig, msg: mail.Inbound
    ) -> list[Attachment]:
        """A message's attachments as run inputs (and knowledge documents when configured).

        Returns:
            Text attachments; one note lists what was left out.
        """
        from evokai.knowledge.extract import Unreadable, extract

        a = cfg.attachments
        out: list[Attachment] = []
        skipped: list[str] = []
        for i, (name, mime, data) in enumerate(msg.attachments):
            if a.mode == "ignore" or i >= a.max_count or len(data) > a.max_bytes or not data:
                skipped.append(name)
                continue
            try:
                doc = await asyncio.to_thread(extract, name, mime, data)
            except (Unreadable, ValueError):
                skipped.append(name)
                continue
            text = "\n".join(b.text for b in doc.blocks)[: a.max_chars]
            out.append(Attachment(name=name[:200], media_type=mime, text=text))
            if a.mode == "knowledge" and a.knowledge_base and self.svc.knowledge is not None:
                await self._to_knowledge(app, a.knowledge_base, name, mime, data, msg)
        if skipped:
            out.append(
                Attachment(
                    name="attachments-not-read",
                    media_type="text/plain",
                    text="Not read (too large, too many or not text): " + ", ".join(skipped[:20]),
                )
            )
        return out

    async def _to_knowledge(
        self, app: m.Application, base: str, name: str, mime: str, data: bytes, msg: mail.Inbound
    ) -> None:
        try:
            kb = next(
                (b for b in await self.svc.store.knowledge.bases(app.id) if b.name == base), None
            )
            if kb is None:
                return
            await self.svc.knowledge.add_file(
                kb,
                name=name,
                mime=mime,
                data=data,
                source_kind="email",
                source=f"email:{_h(msg.message_id)[:16]}:{name}"[:500],
            )
        except AppError as e:
            log.info("channel_attachment_not_kept", extra={"code": e.code})

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("channel_poll_failed", extra={"error": type(e).__name__})
            await asyncio.sleep(POLL_EVERY)

    async def poll_once(self) -> int:
        """Read the IMAP mailboxes whose poll is due.

        Returns:
            How many messages were handled.
        """
        handled = 0
        for rec in await self.svc.store.channels.enabled("email"):
            try:
                cfg = validate("email", rec.config)
            except AppError:
                continue
            if cfg.inbound != "imap" or cfg.imap is None:
                continue
            # One instance polls a mailbox at a time, once per its poll period.
            if not await self.svc.shared.set_once(
                f"channel:poll:{rec.id}", "1", cfg.imap.poll_seconds
            ):
                continue
            app = await self.svc.store.applications.get(rec.application_id)
            if app is None or app.status != "active":
                continue
            try:
                found, state = await asyncio.to_thread(
                    self.mailer.fetch_new, cfg.imap, dict(rec.state or {})
                )
            except Exception as e:
                await self._failed(rec, e)
                continue
            await self.svc.store.channels.set_state(rec.id, state)
            for _uid, raw in found:
                await self.email(app, rec, cfg, raw)
                handled += 1
            if not found:
                await self.svc.store.channels.seen(rec.id, status="ok")
        return handled

    # Teams

    async def teams_activity(
        self, app: m.Application, rec: m.ChannelRecord, cfg: TeamsConfig, activity: dict[str, Any]
    ) -> None:
        """Handle a verified activity (a message, or a card's button)."""
        if activity.get("type") != "message":
            return
        ref = reference_of(activity)
        allowed = cfg.allowed_tenants or ([cfg.tenant_id] if cfg.tenant_id else [])
        if allowed and ref.get("tenantId") not in allowed:
            log.info("channel_teams_tenant_refused", extra={"channelName": rec.name})
            return
        sender = activity.get("from") or {}
        aad = str(sender.get("aadObjectId") or "")
        if not aad or not await self._once(rec, str(activity.get("id") or new_id())):
            return
        try:
            user = ChannelUser("teams", aad, name=sender.get("name"), tenant=ref.get("tenantId"))
            subject = await self._subject(app, rec, cfg, user)
            if subject is None:
                await self.teams.send(cfg, ref, {"text": message(app, "channel.unknownUser")})
                return
            if not await self._rate(rec, cfg, subject):
                await self.teams.send(cfg, ref, {"text": message(app, "channel.tooMany")})
                return
            raw_value = activity.get("value")
            value: dict[str, Any] = raw_value if isinstance(raw_value, dict) else {}
            text, confirm = teams_text(activity), None
            if value.get("evokai") == "confirm":
                confirm = {
                    "actionId": str(value.get("actionId") or ""),
                    "approve": bool(value.get("approve")),
                }
            elif value.get("evokai") == "ask":
                text = str(value.get("answer") or "")
            if not (text or confirm):
                return
            conversation_id = await self._conversation(
                rec, subject, _h(subject, str(ref.get("conversationId"))), ref
            )
            outcome = await self._turn(
                app, rec, subject, conversation_id, text=text, confirm=confirm
            )
            for reply in self.teams_replies(app, outcome, confirm):
                await self.teams.send(cfg, ref, reply)
            await self.svc.store.channels.seen(rec.id, status="ok")
        except Exception as e:
            await self._failed(rec, e)

    @staticmethod
    def teams_replies(
        app: m.Application, o: Outcome, confirm: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """A turn's outcome as Teams activities.

        Returns:
            Activities (text, adaptive cards).
        """
        out: list[dict[str, Any]] = []
        if confirm is not None and not o.text and not o.error:
            key = "channel.approved" if confirm.get("approve") else "channel.cancelled"
            out.append({"text": message(app, key)})
        if o.text:
            out.append({"text": o.text, "textFormat": "markdown"})
        if o.error and not o.text:
            out.append({"text": o.error})
        for c in o.connects:
            body = [
                {
                    "type": "TextBlock",
                    "text": message(app, "channel.connectText", provider=c.get("title")),
                    "wrap": True,
                }
            ]
            actions = (
                [
                    {
                        "type": "Action.OpenUrl",
                        "title": message(app, "channel.connect", provider=c.get("title")),
                        "url": c["url"],
                    }
                ]
                if c.get("url")
                else []
            )
            out.append({"attachments": [card(body, actions)]})
        if o.confirm:
            body = [
                {
                    "type": "TextBlock",
                    "text": message(app, "channel.confirmTitle"),
                    "weight": "Bolder",
                    "wrap": True,
                },
                *(
                    {"type": "TextBlock", "text": f"- {item}", "wrap": True}
                    for item in confirm_items(app, o.confirm)
                ),
            ]
            action = str(o.confirm.get("actionId"))
            actions = [
                {
                    "type": "Action.Submit",
                    "title": message(app, "channel.approve"),
                    "style": "positive",
                    "data": {"evokai": "confirm", "actionId": action, "approve": True},
                },
                {
                    "type": "Action.Submit",
                    "title": message(app, "channel.cancel"),
                    "data": {"evokai": "confirm", "actionId": action, "approve": False},
                },
            ]
            out.append({"attachments": [card(body, actions)]})
        if o.ask:
            body = [{"type": "TextBlock", "text": str(o.ask.get("question")), "wrap": True}]
            actions = [
                {"type": "Action.Submit", "title": opt, "data": {"evokai": "ask", "answer": opt}}
                for opt in o.ask.get("options") or []
            ]
            out.append({"attachments": [card(body, actions)]})
        return out

    # Slack

    async def slack_event(
        self, app: m.Application, rec: m.ChannelRecord, cfg: SlackConfig, payload: dict[str, Any]
    ) -> None:
        """Handle a verified Events API callback (a direct message or a mention)."""
        if payload.get("type") != "event_callback":
            return
        team = str(payload.get("team_id") or "")
        if cfg.team_id and team != cfg.team_id:
            return
        event = payload.get("event") or {}
        kind = event.get("type")
        if kind not in {"message", "app_mention"}:
            return
        if kind == "message" and event.get("channel_type") != "im":
            return  # in channels the app answers when mentioned (app_mention)
        if event.get("bot_id") or event.get("subtype"):
            return
        own = {str(a.get("user_id")) for a in payload.get("authorizations") or []}
        if not event.get("user") or event.get("user") in own:
            return
        if not await self._once(rec, str(payload.get("event_id") or event.get("ts") or "")):
            return
        channel = str(event.get("channel") or "")
        thread_ts = str(event.get("thread_ts") or event.get("ts") or "")
        try:
            user = ChannelUser("slack", f"{team}:{event['user']}")
            subject = await self._subject(app, rec, cfg, user)
            if subject is None:
                await self._slack_post(cfg, channel, thread_ts, message(app, "channel.unknownUser"))
                return
            if not await self._rate(rec, cfg, subject):
                await self._slack_post(cfg, channel, thread_ts, message(app, "channel.tooMany"))
                return
            text = slack_text(event)
            if not text:
                return
            ref = {"channel": channel, "threadTs": thread_ts, "team": team}
            conversation_id = await self._conversation(
                rec, subject, _h(subject, channel, thread_ts), ref
            )
            outcome = await self._turn(app, rec, subject, conversation_id, text=text)
            await self._slack_reply(app, cfg, channel, thread_ts, outcome)
            await self.svc.store.channels.seen(rec.id, status="ok")
        except Exception as e:
            await self._failed(rec, e)

    async def slack_action(
        self, app: m.Application, rec: m.ChannelRecord, cfg: SlackConfig, payload: dict[str, Any]
    ) -> None:
        """Handle a verified interaction: a confirmation's or a question's button."""
        if payload.get("type") != "block_actions":
            return
        team = str((payload.get("team") or {}).get("id") or "")
        if cfg.team_id and team != cfg.team_id:
            return
        actions = payload.get("actions") or []
        if not actions:
            return
        action = actions[0]
        try:
            value = json.loads(str(action.get("value") or "{}"))
        except ValueError:
            return
        msg = payload.get("message") or {}
        channel = str((payload.get("channel") or {}).get("id") or "")
        thread_ts = str(msg.get("thread_ts") or msg.get("ts") or "")
        user_id = str((payload.get("user") or {}).get("id") or "")
        if not user_id or not await self._once(
            rec, f"{payload.get('trigger_id') or ''}:{action.get('action_ts') or ''}"
        ):
            return
        try:
            subject = await self._subject(app, rec, cfg, ChannelUser("slack", f"{team}:{user_id}"))
            if subject is None:
                return
            th = await self.svc.store.channels.thread(rec.id, [_h(subject, channel, thread_ts)])
            if th is None:
                return  # someone else's conversation: nothing to continue for this person
            if not await self._rate(rec, cfg, subject):
                return
            aid = str(action.get("action_id") or "")
            text, confirm = None, None
            if aid.startswith("evokai_confirm"):
                confirm = {
                    "actionId": str(value.get("actionId") or ""),
                    "approve": bool(value.get("approve")),
                }
                done = message(
                    app, "channel.approved" if confirm["approve"] else "channel.cancelled"
                )
            elif aid.startswith("evokai_ask"):
                text = str(value.get("answer") or "")
                done = text
            else:
                return
            if msg.get("ts"):
                # The buttons go away once pressed: the message keeps its text and the choice.
                with contextlib.suppress(ValueError):
                    await self.slack.call(
                        cfg,
                        "chat.update",
                        {
                            "channel": channel,
                            "ts": msg.get("ts"),
                            "text": f"{msg.get('text') or ''}\n{done}".strip(),
                            "blocks": [],
                        },
                    )
            outcome = await self._turn(
                app, rec, subject, th.conversation_id, text=text, confirm=confirm
            )
            await self._slack_reply(app, cfg, channel, thread_ts, outcome)
            await self.svc.store.channels.seen(rec.id, status="ok")
        except Exception as e:
            await self._failed(rec, e)

    async def _slack_post(
        self, cfg: SlackConfig, channel: str, thread_ts: str, text: str, blocks: list | None = None
    ) -> None:
        body: dict[str, Any] = {"channel": channel, "thread_ts": thread_ts, "text": text[:3_900]}
        if blocks:
            body["blocks"] = blocks
        await self.slack.call(cfg, "chat.postMessage", body)

    async def _slack_reply(
        self, app: m.Application, cfg: SlackConfig, channel: str, thread_ts: str, o: Outcome
    ) -> None:
        for text, blocks in self.slack_replies(app, o):
            await self._slack_post(cfg, channel, thread_ts, text, blocks)

    @staticmethod
    def slack_replies(app: m.Application, o: Outcome) -> list[tuple[str, list[dict] | None]]:
        """A turn's outcome as Slack messages.

        Returns:
            (fallback text, blocks or None) per message.
        """
        out: list[tuple[str, list[dict] | None]] = []
        if o.text:
            out.append((slack_markdown(o.text), None))
        elif o.error:
            out.append((o.error, None))
        for c in o.connects:
            text = message(app, "channel.connectText", provider=c.get("title"))
            blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
            if c.get("url"):
                blocks.append({
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": message(app, "channel.connect", provider=c.get("title"))[
                                    :75
                                ],
                            },
                            "url": c["url"],
                            "action_id": "evokai_connect",
                        }
                    ],
                })
            out.append((text, blocks))
        if o.confirm:
            items = "\n".join(f"• {x}" for x in confirm_items(app, o.confirm))
            title = message(app, "channel.confirmTitle")
            action = str(o.confirm.get("actionId"))
            out.append((
                f"{title}\n{items}",
                [
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*\n{items}"}},
                    {
                        "type": "actions",
                        "elements": [
                            button(
                                message(app, "channel.approve"),
                                "evokai_confirm_yes",
                                {"actionId": action, "approve": True},
                                "primary",
                            ),
                            button(
                                message(app, "channel.cancel"),
                                "evokai_confirm_no",
                                {"actionId": action, "approve": False},
                            ),
                        ],
                    },
                ],
            ))
        if o.ask:
            question = str(o.ask.get("question"))
            out.append((
                question,
                [
                    {"type": "section", "text": {"type": "mrkdwn", "text": question}},
                    {
                        "type": "actions",
                        "elements": [
                            button(opt, f"evokai_ask_{i}", {"answer": opt})
                            for i, opt in enumerate(o.ask.get("options") or [])
                        ],
                    },
                ],
            ))
        return out

    # the console

    def endpoints(self, app: m.Application, rec: m.ChannelRecord, base: str) -> dict[str, str]:
        """The URLs a channel's provider calls.

        Returns:
            Name → URL.
        """
        root = f"{base.rstrip('/')}/v1/channels/{app.slug}/{rec.kind}/{rec.name}"
        if rec.kind == "email":
            return {"inbound": f"{root}/inbound"}
        if rec.kind == "teams":
            return {"messaging": f"{root}/messages"}
        return {"events": f"{root}/events", "interactivity": f"{root}/interactions"}

    async def view(self, app: m.Application, base: str) -> list[dict[str, Any]]:
        """An application's channels for the console.

        Returns:
            One row per channel: configuration, status, endpoints, environment variables,
            conversations, whether it is granted and whether its agent allows it.
        """
        import os

        grants = await grants_of(self.svc.store, app)
        counts = await self.svc.store.channels.counts(app.id)
        agents = {a.name: a for a in await self.svc.store.agents.active_all(app.id)}
        out = []
        for rec in await self.svc.store.channels.all(app.id):
            error = None
            try:
                cfg = validate(rec.kind, rec.config)
                envs = env_names(cfg)
            except AppError as e:
                error, envs = e.message, env_names(rec.config)
            agent = agents.get(rec.agent)
            out.append({
                "kind": rec.kind,
                "name": rec.name,
                "agent": rec.agent,
                "enabled": rec.enabled,
                "config": rec.config,
                "valid": error is None,
                "error": error,
                "status": rec.status,
                "lastError": rec.last_error,
                "lastEventAt": rec.last_event_at.isoformat() if rec.last_event_at else None,
                "endpoints": self.endpoints(app, rec, base),
                "env": [{"name": n, "set": bool(os.environ.get(n))} for n in envs],
                "conversations": counts.get(rec.id, 0),
                "granted": grants.channel(rec.kind),
                "agentFound": agent is not None,
                "agentAllows": bool(
                    agent and rec.kind in ((agent.definition or {}).get("channels") or [])
                ),
            })
        return out

    async def test(self, app: m.Application, rec: m.ChannelRecord) -> list[dict[str, Any]]:
        """Validate a channel without sending anything to anyone.

        Returns:
            One row per check: ``{"check", "ok", "detail"}``.
        """
        import os

        out: list[dict[str, Any]] = []
        try:
            cfg = validate(rec.kind, rec.config)
            out.append({"check": "config", "ok": True, "detail": ""})
        except AppError as e:
            return [{"check": "config", "ok": False, "detail": e.message}]
        missing = [n for n in env_names(cfg) if not os.environ.get(n)]
        out.append({
            "check": "env",
            "ok": not missing,
            "detail": ", ".join(missing) if missing else "",
        })
        grants = await grants_of(self.svc.store, app)
        out.append({"check": "granted", "ok": grants.channel(rec.kind), "detail": rec.kind})
        try:
            agent = await self.svc.agents.active(app.id, rec.agent)
            ok = rec.kind in agent.channels
            out.append({"check": "agent", "ok": ok, "detail": rec.agent})
        except AppError as e:
            out.append({"check": "agent", "ok": False, "detail": e.message})
        if missing:
            return out
        if isinstance(cfg, EmailConfig):
            out += await self.mailer.check(cfg)
        elif isinstance(cfg, TeamsConfig):
            try:
                await self.teams.token(cfg)
                out.append({"check": "teams", "ok": True, "detail": "token issued"})
            except Exception as e:
                out.append({"check": "teams", "ok": False, "detail": str(e)[:300]})
        elif isinstance(cfg, SlackConfig):
            try:
                who = await self.slack.call(cfg, "auth.test", {})
                team = str(who.get("team_id") or "")
                ok = not cfg.team_id or cfg.team_id == team
                out.append({
                    "check": "slack",
                    "ok": ok,
                    "detail": f"{who.get('team') or team} as {who.get('user') or ''}".strip(),
                })
            except Exception as e:
                out.append({"check": "slack", "ok": False, "detail": str(e)[:300]})
        ok = all(x["ok"] for x in out)
        await self.svc.store.channels.seen(
            rec.id, status="ok" if ok else "error", error=None if ok else "test failed"
        )
        return out
