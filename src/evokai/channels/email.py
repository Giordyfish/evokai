"""The e-mail channel's protocol: reading a message, loop protection, replies, transports.

A message reaches the agent from its mailbox (IMAP, polled) or from an inbound mail provider's
webhook (the raw RFC 822 message, signed). Before anything runs:

- **loop protection**: automatic mail is never answered: ``Auto-Submitted`` other than ``no``,
  ``X-Autoreply`` and kin, ``Precedence: bulk|junk|list``, mailing lists (``List-Id``,
  ``List-Unsubscribe``), delivery reports (``multipart/report``, an empty ``Return-Path``),
  mailer daemons and no-reply senders, the agent's own address and aliases, Evokai's own replies
  (``X-Evokai-Channel``), and more than a few replies an hour to one address;
- **allowed senders**, when the channel lists them.

Replies keep the thread (``In-Reply-To``, ``References``, ``Re:``), say they are automatic
(``Auto-Submitted: auto-replied``) and are sent (SMTP or Microsoft Graph) or saved as drafts
(IMAP ``APPEND`` to the drafts folder, or a Graph draft) as the channel says.
"""

from __future__ import annotations

import asyncio
import base64
import email
import email.policy
import email.utils
import hashlib
import imaplib
import re
import smtplib
import ssl
import time
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any

import httpx

from evokai.channels.config import EmailConfig, ImapInbound, SmtpOutbound
from evokai.connectors.base import secret
from evokai.connectors.text import html_text

DAEMONS = re.compile(
    r"^(mailer-daemon|postmaster|no-?reply|do-?not-?reply|bounces?|notifications?|"
    r"auto-?reply|autoresponder)([+.-].*)?$",
    re.I,
)
AUTO_HEADERS = ("x-autoreply", "x-autorespond", "x-autoresponse", "x-auto-response")
BULK = {"bulk", "junk", "list", "auto_reply"}
OWN_HEADER = "X-Evokai-Channel"
GRAPH = "https://graph.microsoft.com/v1.0"


@dataclass
class Inbound:
    """A message, as the channel reads it."""

    message_id: str
    sender: str
    """The sender's address, lower case."""
    sender_name: str
    subject: str
    text: str
    references: list[str] = field(default_factory=list)
    """Earlier messages of the thread (``References`` and ``In-Reply-To``), oldest first."""
    attachments: list[tuple[str, str, bytes]] = field(default_factory=list)
    """(name, type, bytes) of each attachment part."""
    raw: EmailMessage | None = None


def _ids(value: str | None) -> list[str]:
    return re.findall(r"<[^<>\s]+>", value or "")


def parse(data: bytes, max_chars: int) -> Inbound:
    """Read a raw message.

    Returns:
        The message.

    Raises:
        ValueError: Not a message (no sender).
    """
    msg = email.message_from_bytes(data, policy=email.policy.default)
    assert isinstance(msg, EmailMessage)  # ruff: ignore[assert] (policy.default builds one)
    name, addr = email.utils.parseaddr(str(msg.get("From", "")))
    if not addr or "@" not in addr:
        raise ValueError("the message has no sender")
    part = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if part is not None:
        text = str(part.get_content())
        if part.get_content_type() == "text/html":
            text = html_text(text)
    attachments = []
    for a in msg.iter_attachments():
        try:
            payload = a.get_payload(decode=True) or b""
        except Exception:
            payload = b""
        attachments.append((
            str(a.get_filename() or "attachment"),
            a.get_content_type(),
            payload if isinstance(payload, bytes) else b"",
        ))
    mid = (_ids(str(msg.get("Message-ID", ""))) or [""])[0]
    if not mid:
        digest = hashlib.sha256(data).hexdigest()[:24]
        mid = f"<{digest}@evokai.invalid>"
    refs = _ids(str(msg.get("References", ""))) + _ids(str(msg.get("In-Reply-To", "")))
    return Inbound(
        message_id=mid,
        sender=addr.lower(),
        sender_name=name,
        subject=str(msg.get("Subject", ""))[:300],
        text=text[:max_chars],
        references=list(dict.fromkeys(refs)),
        attachments=attachments,
        raw=msg,
    )


def loop_reason(m: Inbound, cfg: EmailConfig) -> str | None:
    """Why a message must not be answered, if it must not.

    Returns:
        ``auto_submitted``, ``autoreply``, ``bulk``, ``list``, ``report``, ``bounce``, ``daemon``,
        ``own``, ``not_allowed``, or None.
    """
    msg = m.raw
    own = {cfg.address.lower(), *(a.lower() for a in cfg.aliases)}
    if m.sender in own:
        return "own"
    if msg is not None:
        if msg.get(OWN_HEADER):
            return "own"
        auto = str(msg.get("Auto-Submitted", "")).strip().lower()
        if auto and auto != "no":
            return "auto_submitted"
        if any(msg.get(h) for h in AUTO_HEADERS):
            return "autoreply"
        if str(msg.get("Precedence", "")).strip().lower() in BULK:
            return "bulk"
        if msg.get("List-Id") or msg.get("List-Unsubscribe"):
            return "list"
        if msg.get_content_type() == "multipart/report":
            return "report"
        if str(msg.get("Return-Path", "")).strip() == "<>":
            return "bounce"
    if DAEMONS.match(m.sender.partition("@")[0]):
        return "daemon"
    if cfg.allowed_senders:
        domain = "@" + m.sender.partition("@")[2]
        allowed = {a.lower() for a in cfg.allowed_senders}
        if m.sender not in allowed and domain not in allowed:
            return "not_allowed"
    return None


def reply(cfg: EmailConfig, m: Inbound, text: str, channel: str) -> EmailMessage:
    """The answer to a message, in its thread.

    Returns:
        The reply.
    """
    out = EmailMessage()
    domain = cfg.address.partition("@")[2] or "evokai.invalid"
    out["From"] = email.utils.formataddr((cfg.display_name or "", cfg.address))
    out["To"] = email.utils.formataddr((m.sender_name, m.sender))
    subject = m.subject or ""
    out["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}".strip()
    out["Message-ID"] = f"<{uuid.uuid4().hex}@{domain}>"
    out["Date"] = email.utils.formatdate(localtime=False)
    out["In-Reply-To"] = m.message_id
    out["References"] = " ".join([*m.references, m.message_id][-20:])
    out["Auto-Submitted"] = "auto-replied"
    out[OWN_HEADER] = channel
    out.set_content(text)
    return out


# transports (blocking work runs in a thread)


def _smtp_send(cfg: SmtpOutbound, msg: EmailMessage) -> None:
    context = ssl.create_default_context()
    cls = smtplib.SMTP_SSL if cfg.ssl else smtplib.SMTP
    kwargs: dict[str, Any] = {"timeout": cfg.timeout_seconds}
    if cfg.ssl:
        kwargs["context"] = context
    with cls(cfg.host, cfg.port, **kwargs) as s:
        if cfg.starttls and not cfg.ssl:
            s.starttls(context=context)
        if cfg.password_env:
            user = cfg.username or secret(cfg.username_env, "SMTP")
            s.login(user, secret(cfg.password_env, "SMTP"))
        s.send_message(msg)


def _smtp_check(cfg: SmtpOutbound) -> None:
    context = ssl.create_default_context()
    cls = smtplib.SMTP_SSL if cfg.ssl else smtplib.SMTP
    kwargs: dict[str, Any] = {"timeout": cfg.timeout_seconds}
    if cfg.ssl:
        kwargs["context"] = context
    with cls(cfg.host, cfg.port, **kwargs) as s:
        if cfg.starttls and not cfg.ssl:
            s.starttls(context=context)
        if cfg.password_env:
            user = cfg.username or secret(cfg.username_env, "SMTP")
            s.login(user, secret(cfg.password_env, "SMTP"))
        s.noop()


def imap_connect(cfg: ImapInbound) -> Any:
    """Open and log in to the mailbox.

    Returns:
        The connection (``imaplib.IMAP4``).
    """
    cls = imaplib.IMAP4_SSL if cfg.ssl else imaplib.IMAP4
    conn = cls(cfg.host, cfg.port, timeout=cfg.timeout_seconds)
    user = cfg.username or secret(cfg.username_env, "IMAP")
    conn.login(user, secret(cfg.password_env, "IMAP"))
    return conn


def _quoted(text: str) -> str:
    clean = re.sub(r"[\r\n]", " ", text)[:200]
    return '"' + clean.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Mailer:
    """Sends replies, saves drafts and reads the mailbox for one e-mail channel."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        imap_factory: Any = None,
        smtp: Any = None,
        smtp_check: Any = None,
    ) -> None:
        """Configure.

        Args:
            http: The process's HTTP client (Microsoft Graph).
            imap_factory: Opens an IMAP connection (tests pass a fake).
            smtp: Sends a message over SMTP (tests pass a fake): ``(cfg, message)``.
            smtp_check: Logs in to the SMTP server without sending (tests pass a fake).
        """
        self.http = http
        self.imap_factory = imap_factory or imap_connect
        self.smtp = smtp or _smtp_send
        self.smtp_check = smtp_check or _smtp_check
        self._graph_tokens: dict[str, tuple[float, str]] = {}

    async def deliver(self, cfg: EmailConfig, msg: EmailMessage) -> str:
        """Send the reply or save it as a draft, as the channel says.

        Returns:
            ``sent`` or ``drafted``.
        """
        if cfg.transport == "graph":
            await self._graph(cfg, msg, draft=cfg.reply == "draft")
        elif cfg.reply == "draft":
            await asyncio.to_thread(self._append_draft, cfg, msg)
        else:
            await asyncio.to_thread(self.smtp, cfg.smtp, msg)
        return "drafted" if cfg.reply == "draft" else "sent"

    def _append_draft(self, cfg: EmailConfig, msg: EmailMessage) -> None:
        assert cfg.imap is not None  # ruff: ignore[assert] (validated with the configuration)
        conn = self.imap_factory(cfg.imap)
        try:
            status, _ = conn.append(
                _quoted(cfg.drafts_folder),
                r"(\Draft \Seen)",
                imaplib.Time2Internaldate(time.time()),
                msg.as_bytes(),
            )
            if status != "OK":
                raise ValueError("the mail server refused the draft")
        finally:
            try:
                conn.logout()
            except Exception:  # ruff: ignore[try-except-pass] (closing anyway)
                pass

    async def _graph_token(self, cfg: EmailConfig) -> str:
        g = cfg.graph
        assert g is not None  # ruff: ignore[assert] (validated with the configuration)
        key = f"{g.tenant_id}:{g.client_id}"
        hit = self._graph_tokens.get(key)
        if hit and hit[0] > time.time() + 60:
            return hit[1]
        r = await self.http.post(
            f"https://login.microsoftonline.com/{g.tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": g.client_id,
                "client_secret": secret(g.client_secret_env, "Graph mail"),
                "scope": "https://graph.microsoft.com/.default",
            },
            timeout=15,
        )
        if r.status_code != 200:
            raise ValueError(f"Microsoft refused the mail app's credentials ({r.status_code})")
        body = r.json()
        token = str(body["access_token"])
        self._graph_tokens[key] = (time.time() + int(body.get("expires_in") or 3600), token)
        return token

    async def _graph(self, cfg: EmailConfig, msg: EmailMessage, *, draft: bool) -> None:
        g = cfg.graph
        assert g is not None  # ruff: ignore[assert] (validated with the configuration)
        token = await self._graph_token(cfg)
        mailbox = g.mailbox or cfg.address
        path = "messages" if draft else "sendMail"
        r = await self.http.post(
            f"{GRAPH}/users/{mailbox}/{path}",
            content=base64.b64encode(msg.as_bytes()),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"},
            timeout=30,
        )
        if r.status_code >= 300:
            raise ValueError(f"Microsoft Graph refused the reply ({r.status_code})")

    async def check(self, cfg: EmailConfig) -> list[dict[str, Any]]:
        """Validate a configuration without sending anything: log in, list, ask for a token.

        Returns:
            One row per check: ``{"check", "ok", "detail"}``.
        """
        out: list[dict[str, Any]] = []

        async def run(name: str, fn: Any) -> None:
            try:
                detail = await fn()
                out.append({"check": name, "ok": True, "detail": detail or ""})
            except Exception as e:
                out.append({"check": name, "ok": False, "detail": _reason(e)})

        if cfg.imap is not None:

            async def imap() -> str:
                def work() -> str:
                    assert cfg.imap is not None  # ruff: ignore[assert] (checked above)
                    conn = self.imap_factory(cfg.imap)
                    try:
                        status, _ = conn.select(_quoted(cfg.imap.folder), readonly=True)
                        if status != "OK":
                            raise ValueError(f"no folder {cfg.imap.folder}")
                        return f"{cfg.imap.folder} readable"
                    finally:
                        try:
                            conn.logout()
                        except Exception:  # ruff: ignore[try-except-pass] (closing anyway)
                            pass

                return await asyncio.to_thread(work)

            await run("imap", imap)
        if cfg.inbound == "webhook":

            async def hook() -> str:
                await asyncio.sleep(0)
                secret(cfg.webhook_secret_env, "inbound webhook")
                return "secret set"

            await run("webhook", hook)
        if cfg.reply != "none" and cfg.transport == "smtp" and cfg.smtp is not None:
            smtp_cfg = cfg.smtp

            async def smtp_check() -> str:
                await asyncio.to_thread(self.smtp_check, smtp_cfg)
                return f"{smtp_cfg.host}:{smtp_cfg.port} accepted the login"

            await run("smtp", smtp_check)
        if cfg.reply != "none" and cfg.transport == "graph":

            async def graph() -> str:
                await self._graph_token(cfg)
                return "token issued"

            await run("graph", graph)
        return out

    def fetch_new(
        self, cfg: ImapInbound, state: dict[str, Any], limit: int = 20
    ) -> tuple[list[tuple[int, bytes]], dict[str, Any]]:
        """New messages of the mailbox since the last poll (blocking: run it in a thread).

        Messages are fetched with ``BODY.PEEK`` (nothing is marked read); the last UID seen and
        the folder's UIDVALIDITY are the state kept between polls. The first poll of a mailbox
        only notes where it is, so old mail is never answered.

        Returns:
            (uid, raw message) pairs, oldest first, and the new state.

        Raises:
            ValueError: No such folder, or the server refused the search.
        """
        conn = self.imap_factory(cfg)
        try:
            status, _ = conn.select(_quoted(cfg.folder), readonly=True)
            if status != "OK":
                raise ValueError(f"no folder {cfg.folder}")
            validity = ""
            try:
                _, resp = conn.response("UIDVALIDITY")
                validity = (resp[0] or b"").decode() if resp and resp[0] else ""
            except Exception:
                validity = ""
            last = int(state.get("lastUid") or 0)
            if state.get("uidValidity") != validity:
                last = 0
            status, found = conn.uid("SEARCH", None, "ALL")
            if status != "OK":
                raise ValueError("the mail server refused the search")
            uids = [int(x) for x in (found[0] or b"").split() if x.isdigit()]
            if "lastUid" not in state or state.get("uidValidity") != validity:
                # First poll: start from now.
                return [], {"lastUid": max(uids, default=0), "uidValidity": validity}
            new = sorted(u for u in uids if u > last)[:limit]
            out = []
            for uid in new:
                _, parts = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
                raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), None)
                if raw:
                    out.append((uid, raw))
            top = max(new, default=last)
            return out, {"lastUid": top, "uidValidity": validity}
        finally:
            try:
                conn.logout()
            except Exception:  # ruff: ignore[try-except-pass] (closing anyway)
                pass


def _reason(e: BaseException) -> str:
    text = getattr(e, "message", None) or str(e) or type(e).__name__
    return f"{type(e).__name__}: {text}"[:300]
