"""Google Workspace as the connected person: Gmail, Google Calendar and Google Drive.

With the person's own delegated token (``evokai.connections``). Families:

- ``mail``: ``mail_search``, ``mail_read``; with ``writes``, ``mail_draft`` (a Gmail draft, never
  sent: the person sends it);
- ``calendar``: ``calendar_events`` (the primary calendar); with ``writes``,
  ``calendar_create_event``;
- ``files``: ``files_search``, ``files_read`` (Docs, Sheets and Slides exported as text, Office
  and text files read as text).

Mutating tools are annotated destructive: the turn always stops for the person's confirmation.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any, ClassVar
from urllib.parse import quote

from evokai.connections.providers import GOOGLE, Provider
from evokai.connectors.base import ConnectorConfig, tool
from evokai.connectors.delegated import DelegatedConfig, DelegatedConnector
from evokai.connectors.text import Unreadable, extract_text, html_text
from evokai.mcp.client import McpTool

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary"
DRIVE = "https://www.googleapis.com/drive/v3"
EXPORTS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
FAMILY = {
    "mail_search": "mail",
    "mail_read": "mail",
    "mail_draft": "mail",
    "calendar_events": "calendar",
    "calendar_create_event": "calendar",
    "files_search": "files",
    "files_read": "files",
}


def _header(payload: dict[str, Any], name: str) -> str:
    for h in payload.get("headers") or []:
        if str(h.get("name", "")).lower() == name.lower():
            return str(h.get("value") or "")
    return ""


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _text_of(payload: dict[str, Any]) -> str:
    """The readable text of a Gmail message payload (plain first, else HTML as text).

    Returns:
        The text.
    """
    plain: list[str] = []
    rich: list[str] = []
    stack = [payload]
    while stack:
        part = stack.pop(0)
        stack += part.get("parts") or []
        data = (part.get("body") or {}).get("data")
        if not data or part.get("filename"):
            continue
        mime = str(part.get("mimeType") or "")
        if mime == "text/plain":
            plain.append(_decode(data))
        elif mime == "text/html":
            rich.append(html_text(_decode(data)))
    return "\n".join(plain) if plain else "\n".join(rich)


class GoogleWorkspaceConnector(DelegatedConnector):
    """Google's APIs as the connected person."""

    kind: ClassVar[str] = "google_workspace"
    title: ClassVar[str] = "Google Workspace (as the connected person)"
    provider: ClassVar[Provider] = GOOGLE
    Config: ClassVar[type[ConnectorConfig]] = DelegatedConfig

    def family_of(self, tool_name: str) -> str:
        """The family of a tool.

        Returns:
            ``mail``, ``calendar`` or ``files``.
        """
        return FAMILY.get(tool_name, "")

    def tools(self) -> list[McpTool]:
        """The tools of the configured families (writes only with ``writes: true``).

        Returns:
            Tools.
        """
        n = self.cfg.max_items
        limit = {"type": "integer", "minimum": 1, "maximum": n}
        fam = set(self.cfg.families)
        out: list[McpTool] = []
        if "mail" in fam:
            out += [
                tool(
                    "mail_search",
                    f"Search the user's Gmail with Gmail's search syntax (at most {n}).",
                    {"query": {"type": "string"}, "limit": limit},
                    ["query"],
                ),
                tool(
                    "mail_read",
                    "A Gmail message: sender, recipients, subject, date, text.",
                    {"id": {"type": "string"}},
                    ["id"],
                ),
            ]
            if self.cfg.writes:
                out.append(
                    tool(
                        "mail_draft",
                        "Write a Gmail draft (never sent: the user sends it). With "
                        "reply_to_id it is a reply in that message's thread.",
                        {
                            "to": {"type": "array", "items": {"type": "string"}},
                            "subject": {"type": "string"},
                            "body": {"type": "string"},
                            "reply_to_id": {"type": "string"},
                        },
                        ["body"],
                        writes=True,
                    )
                )
        if "calendar" in fam:
            out.append(
                tool(
                    "calendar_events",
                    "The user's calendar events between two times (RFC 3339).",
                    {"start": {"type": "string"}, "end": {"type": "string"}, "limit": limit},
                    ["start", "end"],
                )
            )
            if self.cfg.writes:
                out.append(
                    tool(
                        "calendar_create_event",
                        "Create an event in the user's primary calendar.",
                        {
                            "subject": {"type": "string"},
                            "start": {"type": "string", "description": "RFC 3339"},
                            "end": {"type": "string", "description": "RFC 3339"},
                            "timezone": {"type": "string"},
                            "attendees": {"type": "array", "items": {"type": "string"}},
                            "location": {"type": "string"},
                            "body": {"type": "string"},
                        },
                        ["subject", "start", "end"],
                        writes=True,
                    )
                )
        if "files" in fam:
            out += [
                tool(
                    "files_search",
                    f"Search the user's Google Drive by words in names and text (at most {n}).",
                    {"query": {"type": "string"}, "limit": limit},
                    ["query"],
                ),
                tool(
                    "files_read",
                    "The text of a file found with files_search.",
                    {"id": {"type": "string"}},
                    ["id"],
                ),
            ]
        return out

    def _limit(self, args: dict[str, Any]) -> int:
        return max(1, min(int(args.get("limit") or self.cfg.max_items), self.cfg.max_items))

    async def run_as(self, token: str, tool_name: str, args: dict[str, Any]) -> Any:
        """Run a tool as the person.

        Returns:
            Messages, events, files, or what was created.

        Raises:
            ValueError: Missing arguments, or a file that cannot be read as text.
        """
        if tool_name == "mail_search":
            q = str(args.get("query") or "").strip()
            if not q:
                raise ValueError("give some words to search")
            r = await self.request(
                token,
                "GET",
                f"{GMAIL}/messages",
                params={"q": q, "maxResults": str(self._limit(args))},
            )
            out = []
            for x in r.json().get("messages") or []:
                meta = (
                    await self.request(
                        token,
                        "GET",
                        f"{GMAIL}/messages/{quote(str(x.get('id')), safe='')}",
                        params=[
                            ("format", "metadata"),
                            ("metadataHeaders", "From"),
                            ("metadataHeaders", "Subject"),
                            ("metadataHeaders", "Date"),
                        ],
                    )
                ).json()
                p = meta.get("payload") or {}
                out.append({
                    "id": meta.get("id"),
                    "threadId": meta.get("threadId"),
                    "from": _header(p, "From"),
                    "subject": _header(p, "Subject"),
                    "date": _header(p, "Date"),
                    "preview": (meta.get("snippet") or "")[:300],
                })
            return {"messages": out}
        if tool_name == "mail_read":
            mid = quote(str(args.get("id") or ""), safe="")
            if not mid:
                raise ValueError("give the id of a message from mail_search")
            x = (
                await self.request(
                    token, "GET", f"{GMAIL}/messages/{mid}", params={"format": "full"}
                )
            ).json()
            p = x.get("payload") or {}
            return {
                "id": x.get("id"),
                "threadId": x.get("threadId"),
                "from": _header(p, "From"),
                "to": _header(p, "To"),
                "cc": _header(p, "Cc"),
                "subject": _header(p, "Subject"),
                "date": _header(p, "Date"),
                "text": _text_of(p)[: self.cfg.max_body_chars],
            }
        if tool_name == "mail_draft":
            msg = EmailMessage()
            thread = None
            subject = str(args.get("subject") or "")
            reply_to = str(args.get("reply_to_id") or "")
            if reply_to:
                orig = (
                    await self.request(
                        token,
                        "GET",
                        f"{GMAIL}/messages/{quote(reply_to, safe='')}",
                        params=[
                            ("format", "metadata"),
                            ("metadataHeaders", "From"),
                            ("metadataHeaders", "Subject"),
                            ("metadataHeaders", "Message-ID"),
                        ],
                    )
                ).json()
                p = orig.get("payload") or {}
                thread = orig.get("threadId")
                msg["To"] = _header(p, "From")
                subj = _header(p, "Subject")
                subject = subject or (subj if subj.lower().startswith("re:") else f"Re: {subj}")
                if mid := _header(p, "Message-ID"):
                    msg["In-Reply-To"] = mid
                    msg["References"] = mid
            elif args.get("to"):
                to = args["to"] if isinstance(args["to"], list) else [args["to"]]
                msg["To"] = ", ".join(str(a) for a in to)
            else:
                raise ValueError("give the recipients (to) or reply_to_id")
            msg["Subject"] = subject
            msg.set_content(str(args.get("body") or ""))
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            body: dict[str, Any] = {"message": {"raw": raw}}
            if thread:
                body["message"]["threadId"] = thread
            d = (await self.request(token, "POST", f"{GMAIL}/drafts", json=body)).json()
            return {"draft": d.get("id"), "subject": subject}
        if tool_name == "calendar_events":
            r = await self.request(
                token,
                "GET",
                f"{CALENDAR}/events",
                params={
                    "timeMin": str(args.get("start") or ""),
                    "timeMax": str(args.get("end") or ""),
                    "maxResults": str(self._limit(args)),
                    "singleEvents": "true",
                    "orderBy": "startTime",
                },
            )
            return {
                "events": [
                    {
                        "id": e.get("id"),
                        "subject": e.get("summary"),
                        "start": (e.get("start") or {}).get("dateTime")
                        or (e.get("start") or {}).get("date"),
                        "end": (e.get("end") or {}).get("dateTime")
                        or (e.get("end") or {}).get("date"),
                        "location": e.get("location"),
                        "organizer": (e.get("organizer") or {}).get("email"),
                    }
                    for e in r.json().get("items") or []
                ]
            }
        if tool_name == "calendar_create_event":
            tz = args.get("timezone")
            event: dict[str, Any] = {
                "summary": str(args.get("subject") or ""),
                "start": {
                    "dateTime": str(args.get("start") or ""),
                    **({"timeZone": tz} if tz else {}),
                },
                "end": {"dateTime": str(args.get("end") or ""), **({"timeZone": tz} if tz else {})},
                "attendees": [{"email": str(a)} for a in args.get("attendees") or []],
            }
            if args.get("location"):
                event["location"] = str(args["location"])
            if args.get("body"):
                event["description"] = str(args["body"])
            x = (await self.request(token, "POST", f"{CALENDAR}/events", json=event)).json()
            return {"event": x.get("id"), "subject": x.get("summary"), "link": x.get("htmlLink")}
        if tool_name == "files_search":
            words = str(args.get("query") or "").strip().replace("\\", "").replace("'", "\\'")
            if not words:
                raise ValueError("give some words to search")
            r = await self.request(
                token,
                "GET",
                f"{DRIVE}/files",
                params={
                    "q": f"fullText contains '{words}' and trashed = false",
                    "pageSize": str(self._limit(args)),
                    "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                },
            )
            return {
                "files": [
                    {
                        "id": f.get("id"),
                        "name": f.get("name"),
                        "mimeType": f.get("mimeType"),
                        "modified": f.get("modifiedTime"),
                        "link": f.get("webViewLink"),
                    }
                    for f in r.json().get("files") or []
                    if f.get("mimeType") != "application/vnd.google-apps.folder"
                ]
            }
        if tool_name == "files_read":
            fid = quote(str(args.get("id") or ""), safe="")
            if not fid:
                raise ValueError("give the id of a file from files_search")
            meta = (
                await self.request(
                    token,
                    "GET",
                    f"{DRIVE}/files/{fid}",
                    params={"fields": "id,name,mimeType,webViewLink", "supportsAllDrives": "true"},
                )
            ).json()
            mime = str(meta.get("mimeType") or "")
            if mime in EXPORTS:
                r = await self.request(
                    token, "GET", f"{DRIVE}/files/{fid}/export", params={"mimeType": EXPORTS[mime]}
                )
                text = r.content.decode("utf-8", "replace")
            else:
                r = await self.request(
                    token,
                    "GET",
                    f"{DRIVE}/files/{fid}",
                    params={"alt": "media", "supportsAllDrives": "true"},
                )
                try:
                    text = extract_text(str(meta.get("name") or ""), mime, r.content)
                except Unreadable as e:
                    raise ValueError(str(e)) from e
            return {
                "id": meta.get("id"),
                "name": meta.get("name"),
                "link": meta.get("webViewLink"),
                "text": text[: self.cfg.max_file_chars],
                "truncated": len(text) > self.cfg.max_file_chars,
            }
        raise ValueError(f"unknown tool {tool_name}")
