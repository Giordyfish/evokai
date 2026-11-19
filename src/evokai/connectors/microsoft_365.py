"""Microsoft 365 as the connected person: Outlook mail, calendar, OneDrive and SharePoint files.

Through Microsoft Graph with the person's own delegated token (``evokai.connections``), so the
agent sees exactly what the person can see. Families:

- ``mail``: ``mail_search``, ``mail_read``; with ``writes``, ``mail_draft`` (a draft in the
  person's Drafts folder, never sent: the person sends it from Outlook);
- ``calendar``: ``calendar_events``; with ``writes``, ``calendar_create_event``;
- ``files``: ``files_search``, ``files_read`` (OneDrive and the SharePoint libraries the person
  can reach; Word, Excel, PowerPoint, text and HTML files are read as text).

Mutating tools are annotated destructive: the turn always stops for the person's confirmation.
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import quote

from pydantic import Field, field_validator

from evokai.connections.providers import GRAPH, MICROSOFT_365, Provider
from evokai.connectors.base import ConnectorConfig, tool
from evokai.connectors.delegated import DelegatedConfig, DelegatedConnector
from evokai.connectors.text import Unreadable, extract_text, html_text
from evokai.mcp.client import McpTool

FAMILY = {
    "mail_search": "mail",
    "mail_read": "mail",
    "mail_draft": "mail",
    "calendar_events": "calendar",
    "calendar_create_event": "calendar",
    "files_search": "files",
    "files_read": "files",
}
WRITES = {"mail_draft", "calendar_create_event"}
FILE_SELECT = "id,name,file,folder,lastModifiedDateTime,webUrl,parentReference,size"


class Microsoft365Config(DelegatedConfig):
    """``connector: microsoft_365``."""

    tenant: str = Field(default="organizations", min_length=1, max_length=120)
    """The Entra tenant: its id or domain for a single-tenant app registration, ``organizations``
    for any work account."""

    @field_validator("tenant")
    @classmethod
    def _tenant(cls, v: str) -> str:
        if not all(c.isalnum() or c in "-." for c in v):
            raise ValueError("a tenant id, a domain, or organizations")
        return v


def _addresses(values: Any) -> list[dict[str, Any]]:
    items = values if isinstance(values, list) else [values] if values else []
    return [{"emailAddress": {"address": str(a).strip()}} for a in items if str(a).strip()]


def _who(v: Any) -> str:
    a = (v or {}).get("emailAddress") or {}
    name, addr = a.get("name") or "", a.get("address") or ""
    return f"{name} <{addr}>" if name and addr else addr or name


class Microsoft365Connector(DelegatedConnector):
    """Microsoft Graph as the connected person."""

    kind: ClassVar[str] = "microsoft_365"
    title: ClassVar[str] = "Microsoft 365 (as the connected person)"
    provider: ClassVar[Provider] = MICROSOFT_365
    Config: ClassVar[type[ConnectorConfig]] = Microsoft365Config

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
        out: list[McpTool] = []
        fam = set(self.cfg.families)
        if "mail" in fam:
            out += [
                tool(
                    "mail_search",
                    f"Search the user's Outlook mail, newest first (at most {n}).",
                    {"query": {"type": "string", "description": "Words to find."}, "limit": limit},
                    ["query"],
                ),
                tool(
                    "mail_read",
                    "A message of the user's mailbox: sender, recipients, subject, date, text.",
                    {"id": {"type": "string"}},
                    ["id"],
                ),
            ]
            if self.cfg.writes:
                out.append(
                    tool(
                        "mail_draft",
                        "Write a draft in the user's Drafts folder (never sent: the user sends "
                        "it). With reply_to_id it is a reply to that message.",
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
                    "The user's calendar events between two times (ISO 8601).",
                    {
                        "start": {"type": "string", "description": "ISO 8601"},
                        "end": {"type": "string", "description": "ISO 8601"},
                        "limit": limit,
                    },
                    ["start", "end"],
                )
            )
            if self.cfg.writes:
                out.append(
                    tool(
                        "calendar_create_event",
                        "Create an event in the user's calendar (attendees get an invitation).",
                        {
                            "subject": {"type": "string"},
                            "start": {"type": "string", "description": "ISO 8601"},
                            "end": {"type": "string", "description": "ISO 8601"},
                            "timezone": {"type": "string", "description": "e.g. Europe/Rome"},
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
                    f"Search the user's OneDrive and SharePoint files (at most {n}).",
                    {"query": {"type": "string"}, "limit": limit},
                    ["query"],
                ),
                tool(
                    "files_read",
                    "The text of a file found with files_search.",
                    {"id": {"type": "string"}, "drive_id": {"type": "string"}},
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
            q = str(args.get("query") or "").replace('"', " ").strip()
            if not q:
                raise ValueError("give some words to search")
            r = await self.request(
                token,
                "GET",
                f"{GRAPH}/me/messages",
                params={
                    "$search": f'"{q}"',
                    "$top": str(self._limit(args)),
                    "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead",
                },
            )
            return {
                "messages": [
                    {
                        "id": x.get("id"),
                        "from": _who(x.get("from")),
                        "subject": x.get("subject"),
                        "date": x.get("receivedDateTime"),
                        "preview": (x.get("bodyPreview") or "")[:300],
                    }
                    for x in r.json().get("value", [])
                ]
            }
        if tool_name == "mail_read":
            mid = quote(str(args.get("id") or ""), safe="")
            if not mid:
                raise ValueError("give the id of a message from mail_search")
            r = await self.request(
                token,
                "GET",
                f"{GRAPH}/me/messages/{mid}",
                params={
                    "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,"
                    "hasAttachments,conversationId"
                },
                headers={"Prefer": 'outlook.body-content-type="text"'},
            )
            x = r.json()
            body = x.get("body") or {}
            text = str(body.get("content") or "")
            if str(body.get("contentType", "")).lower() == "html":
                text = html_text(text)
            return {
                "id": x.get("id"),
                "from": _who(x.get("from")),
                "to": [_who(a) for a in x.get("toRecipients") or []],
                "cc": [_who(a) for a in x.get("ccRecipients") or []],
                "subject": x.get("subject"),
                "date": x.get("receivedDateTime"),
                "text": text[: self.cfg.max_body_chars],
                "hasAttachments": bool(x.get("hasAttachments")),
            }
        if tool_name == "mail_draft":
            body = str(args.get("body") or "")
            reply_to = str(args.get("reply_to_id") or "")
            if reply_to:
                r = await self.request(
                    token,
                    "POST",
                    f"{GRAPH}/me/messages/{quote(reply_to, safe='')}/createReply",
                    json={"comment": body},
                )
            else:
                if not args.get("to"):
                    raise ValueError("give the recipients (to) or reply_to_id")
                r = await self.request(
                    token,
                    "POST",
                    f"{GRAPH}/me/messages",
                    json={
                        "subject": str(args.get("subject") or ""),
                        "body": {"contentType": "Text", "content": body},
                        "toRecipients": _addresses(args.get("to")),
                    },
                )
            x = r.json()
            return {"draft": x.get("id"), "subject": x.get("subject"), "webLink": x.get("webLink")}
        if tool_name == "calendar_events":
            r = await self.request(
                token,
                "GET",
                f"{GRAPH}/me/calendarView",
                params={
                    "startDateTime": str(args.get("start") or ""),
                    "endDateTime": str(args.get("end") or ""),
                    "$top": str(self._limit(args)),
                    "$orderby": "start/dateTime",
                    "$select": "id,subject,start,end,location,organizer,isOnlineMeeting",
                },
            )
            return {
                "events": [
                    {
                        "id": e.get("id"),
                        "subject": e.get("subject"),
                        "start": (e.get("start") or {}).get("dateTime"),
                        "end": (e.get("end") or {}).get("dateTime"),
                        "timezone": (e.get("start") or {}).get("timeZone"),
                        "location": (e.get("location") or {}).get("displayName"),
                        "organizer": _who(e.get("organizer")),
                    }
                    for e in r.json().get("value", [])
                ]
            }
        if tool_name == "calendar_create_event":
            tz = str(args.get("timezone") or "UTC")
            event: dict[str, Any] = {
                "subject": str(args.get("subject") or ""),
                "start": {"dateTime": str(args.get("start") or ""), "timeZone": tz},
                "end": {"dateTime": str(args.get("end") or ""), "timeZone": tz},
                "attendees": [{**a, "type": "required"} for a in _addresses(args.get("attendees"))],
            }
            if args.get("location"):
                event["location"] = {"displayName": str(args["location"])}
            if args.get("body"):
                event["body"] = {"contentType": "Text", "content": str(args["body"])}
            r = await self.request(token, "POST", f"{GRAPH}/me/events", json=event)
            x = r.json()
            return {"event": x.get("id"), "subject": x.get("subject"), "webLink": x.get("webLink")}
        if tool_name == "files_search":
            q = str(args.get("query") or "").strip().replace("'", "''")
            if not q:
                raise ValueError("give some words to search")
            r = await self.request(
                token,
                "GET",
                f"{GRAPH}/me/drive/root/search(q='{quote(q, safe='')}')",
                params={"$top": str(self._limit(args)), "$select": FILE_SELECT},
            )
            return {
                "files": [
                    {
                        "id": i.get("id"),
                        "driveId": (i.get("parentReference") or {}).get("driveId"),
                        "name": i.get("name"),
                        "modified": i.get("lastModifiedDateTime"),
                        "webUrl": i.get("webUrl"),
                    }
                    for i in r.json().get("value", [])
                    if not i.get("folder")
                ]
            }
        if tool_name == "files_read":
            item = quote(str(args.get("id") or ""), safe="")
            if not item:
                raise ValueError("give the id of a file from files_search")
            drive = (
                f"{GRAPH}/drives/{quote(str(args['drive_id']), safe='')}"
                if args.get("drive_id")
                else f"{GRAPH}/me/drive"
            )
            meta = (
                await self.request(
                    token, "GET", f"{drive}/items/{item}", params={"$select": FILE_SELECT}
                )
            ).json()
            if meta.get("folder"):
                raise ValueError("this is a folder, not a file")
            content = await self.request(
                token, "GET", f"{drive}/items/{item}/content", follow_redirects=True
            )
            try:
                text = extract_text(
                    str(meta.get("name") or ""),
                    str((meta.get("file") or {}).get("mimeType") or ""),
                    content.content,
                )
            except Unreadable as e:
                raise ValueError(str(e)) from e
            return {
                "id": meta.get("id"),
                "name": meta.get("name"),
                "webUrl": meta.get("webUrl"),
                "text": text[: self.cfg.max_file_chars],
                "truncated": len(text) > self.cfg.max_file_chars,
            }
        raise ValueError(f"unknown tool {tool_name}")
