"""An IMAP mailbox, read-only: search and read messages in the folders you allow.

Folders are opened with EXAMINE (read-only) and messages fetched with ``BODY.PEEK``, so nothing is
marked as read, moved or deleted. Only ``folders`` can be listed, searched and read. Search terms
are sent as quoted strings without line breaks (no command can be injected). The password comes from
the environment (``password_env``).
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import imaplib
import re
from collections.abc import Callable
from datetime import date
from email.message import EmailMessage
from typing import Any, ClassVar, cast

from pydantic import Field, model_validator

from evokai.connectors.base import Connector, ConnectorConfig, secret, tool
from evokai.connectors.text import html_text
from evokai.mcp.client import McpTool


class ImapConfig(ConnectorConfig):
    """``connector: imap``."""

    host: str = Field(min_length=1)
    port: int = 993
    ssl: bool = True
    username: str | None = None
    username_env: str | None = None
    password_env: str = Field(min_length=1)
    folders: list[str] = Field(default_factory=lambda: ["INBOX"], min_length=1)
    max_messages: int = Field(default=20, ge=1, le=100)
    max_body_chars: int = Field(default=8_000, ge=500, le=100_000)
    timeout_seconds: float = Field(default=20, gt=0, le=120)

    @model_validator(mode="after")
    def _user(self) -> ImapConfig:
        if not (self.username or self.username_env):
            raise ValueError("give username or username_env")
        if self.writes:
            raise ValueError("the IMAP connector only reads")
        return self


def _quoted(text: str) -> str:
    """An IMAP quoted string (CR and LF are not allowed in one).

    Returns:
        The quoted text.
    """
    clean = re.sub(r"[\r\n]", " ", text)[:200]
    return '"' + clean.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _body(msg: EmailMessage, limit: int) -> tuple[str, list[str]]:
    """The readable text of a message and the names of its attachments.

    Returns:
        (text, attachment names).
    """
    attachments = [p.get_filename() or "attachment" for p in msg.iter_attachments()]
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return "", attachments
    text = part.get_content()
    if part.get_content_type() == "text/html":
        text = html_text(text)
    return str(text)[:limit], attachments


Factory = Callable[[ImapConfig], Any]
"""Opens a connection (tests pass a fake with the same methods as imaplib.IMAP4)."""


def _connect(cfg: ImapConfig) -> Any:
    cls = imaplib.IMAP4_SSL if cfg.ssl else imaplib.IMAP4
    conn = cls(cfg.host, cfg.port, timeout=cfg.timeout_seconds)
    user = cfg.username or secret(cfg.username_env, "IMAP connector")
    conn.login(user, secret(cfg.password_env, "IMAP connector"))
    return conn


class ImapConnector(Connector):
    """A mailbox over IMAP, read-only."""

    kind: ClassVar[str] = "imap"
    title: ClassVar[str] = "IMAP mailbox (read-only)"
    Config: ClassVar[type[ConnectorConfig]] = ImapConfig

    def __init__(self, name: str, config: ConnectorConfig, factory: Factory | None = None) -> None:
        """Bind to the configuration."""
        super().__init__(name, config)
        self.cfg = cast(ImapConfig, config)
        self.factory = factory or _connect
        self._lock = asyncio.Lock()

    def tools(self) -> list[McpTool]:
        """List folders, search messages, read a message.

        Returns:
            Tools.
        """
        folder = {"type": "string", "enum": self.cfg.folders}
        return [
            tool("list_folders", "The mail folders you can read."),
            tool(
                "search_messages",
                f"Find messages in a folder, newest first (at most {self.cfg.max_messages}).",
                {
                    "folder": folder,
                    "text": {"type": "string", "description": "Words in the subject or body."},
                    "from": {"type": "string", "description": "Sender address or name."},
                    "since": {"type": "string", "description": "YYYY-MM-DD"},
                    "unseen": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": self.cfg.max_messages},
                },
                ["folder"],
            ),
            tool(
                "read_message",
                "A message's sender, recipients, subject, date, text and attachment names.",
                {"folder": folder, "uid": {"type": "string"}},
                ["folder", "uid"],
            ),
        ]

    def _folder(self, name: Any) -> str:
        folder = str(name or "")
        if folder not in self.cfg.folders:
            raise PermissionError(f"folder {folder!r} is not shared with the assistant")
        return folder

    async def _with(self, fn: Callable[[Any], Any]) -> Any:
        """Run blocking IMAP work in a thread, one connection at a time.

        Returns:
            What ``fn`` returned.
        """

        def work() -> Any:
            conn = self.factory(self.cfg)
            try:
                return fn(conn)
            finally:
                try:
                    conn.logout()
                except Exception:  # ruff: ignore[try-except-pass] (closing anyway)
                    pass

        async with self._lock:
            return await asyncio.to_thread(work)

    async def run(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Run a tool.

        Returns:
            Folders, message summaries, or a message.

        Raises:
            ValueError: A uid that is not a number.
        """
        if tool_name == "list_folders":
            return {"folders": self.cfg.folders}
        folder = self._folder(args.get("folder"))
        if tool_name == "search_messages":
            criteria: list[str] = []
            if args.get("text"):
                criteria += ["TEXT", _quoted(str(args["text"]))]
            if args.get("from"):
                criteria += ["FROM", _quoted(str(args["from"]))]
            if args.get("since"):
                d = date.fromisoformat(str(args["since"])[:10])
                criteria += ["SINCE", d.strftime("%d-%b-%Y")]
            if args.get("unseen"):
                criteria.append("UNSEEN")
            limit = min(int(args.get("limit") or self.cfg.max_messages), self.cfg.max_messages)

            def search(conn: Any) -> Any:
                conn.select(_quoted(folder), readonly=True)
                status, data = conn.uid("SEARCH", None, *(criteria or ["ALL"]))
                if status != "OK":
                    raise ValueError("the mail server refused the search")
                uids = (data[0] or b"").split()[-limit:][::-1]
                out = []
                for uid in uids:
                    _, parts = conn.uid(
                        "FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
                    )
                    raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
                    h = email.message_from_bytes(raw, policy=email.policy.default)
                    out.append({
                        "uid": uid.decode(),
                        "from": str(h.get("From", "")),
                        "subject": str(h.get("Subject", "")),
                        "date": str(h.get("Date", "")),
                    })
                return {"folder": folder, "messages": out}

            return await self._with(search)
        uid = str(args.get("uid") or "")
        if not uid.isdigit():
            raise ValueError("uid is a number from search_messages")

        def read(conn: Any) -> Any:
            conn.select(_quoted(folder), readonly=True)
            status, parts = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), None)
            if status != "OK" or raw is None:
                raise LookupError("no such message")
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            text, attachments = _body(msg, self.cfg.max_body_chars)
            return {
                "uid": uid,
                "from": str(msg.get("From", "")),
                "to": str(msg.get("To", "")),
                "cc": str(msg.get("Cc", "")),
                "subject": str(msg.get("Subject", "")),
                "date": str(msg.get("Date", "")),
                "text": text,
                "attachments": attachments,
            }

        return await self._with(read)
