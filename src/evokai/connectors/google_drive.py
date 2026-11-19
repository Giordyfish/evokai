"""Google Drive: search and read files inside the folders you allow.

Credentials (from the environment): an OAuth client and a refresh token of the account that owns or
shares the folders (``client_id_env``, ``client_secret_env``, ``refresh_token_env``), or a service
account key (``service_account_env``, the JSON key) with the folders shared to it. Only the
``drive.readonly`` scope is asked. Every file is checked against ``folders`` (and, with
``recursive``, their sub-folders) before it is read: a file elsewhere is refused even when the
credential could read it. Google Docs, Sheets and Slides are exported as text; other files are read
when they are text or Office documents.
"""

from __future__ import annotations

import json
import time
from typing import Any, ClassVar, cast

import httpx
import jwt
from pydantic import Field, model_validator

from evokai.connectors.base import Connector, ConnectorConfig, secret, tool
from evokai.connectors.text import Unreadable, extract_text
from evokai.mcp.client import McpTool

API = "https://www.googleapis.com/drive/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # ruff: ignore[hardcoded-password-string] (an endpoint, not a secret)
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
FOLDER = "application/vnd.google-apps.folder"
EXPORTS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
FIELDS = "id,name,mimeType,modifiedTime,size,parents,webViewLink"


class DriveConfig(ConnectorConfig):
    """``connector: google_drive``."""

    folders: list[str] = Field(min_length=1)
    """Folder ids the agent may search and read."""
    recursive: bool = True
    """Their sub-folders too."""
    client_id_env: str | None = None
    client_secret_env: str | None = None
    refresh_token_env: str | None = None
    service_account_env: str | None = None
    max_file_chars: int = Field(default=20_000, ge=1_000, le=200_000)
    timeout_seconds: float = Field(default=20, gt=0, le=120)

    @model_validator(mode="after")
    def _credentials(self) -> DriveConfig:
        oauth = self.client_id_env and self.client_secret_env and self.refresh_token_env
        if not oauth and not self.service_account_env:
            raise ValueError(
                "give client_id_env, client_secret_env and refresh_token_env, "
                "or service_account_env"
            )
        return self


def _q(text: str) -> str:
    """A string literal of a Drive query.

    Returns:
        The quoted text.
    """
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


class GoogleDriveConnector(Connector):
    """Files in Google Drive folders, read-only."""

    kind: ClassVar[str] = "google_drive"
    title: ClassVar[str] = "Google Drive (read-only)"
    Config: ClassVar[type[ConnectorConfig]] = DriveConfig

    def __init__(
        self, name: str, config: ConnectorConfig, http: httpx.AsyncClient | None = None
    ) -> None:
        """Bind to the configuration."""
        super().__init__(name, config)
        self.cfg = cast(DriveConfig, config)
        self.http = http or httpx.AsyncClient()
        self._token: tuple[str, float] | None = None
        self._inside: dict[str, bool] = dict.fromkeys(self.cfg.folders, True)
        self._tree: list[str] | None = None

    async def token(self) -> str:
        """An access token, refreshed a minute before it expires.

        Returns:
            The token.

        Raises:
            PermissionError: Google refused the credential.
        """
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        if self.cfg.service_account_env:
            key = json.loads(secret(self.cfg.service_account_env, f"Drive connector {self.name}"))
            now = int(time.time())
            assertion = jwt.encode(
                {
                    "iss": key["client_email"],
                    "scope": SCOPE,
                    "aud": TOKEN_URL,
                    "iat": now,
                    "exp": now + 3600,
                },
                key["private_key"],
                algorithm="RS256",
            )
            data = {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            }
        else:
            data = {
                "grant_type": "refresh_token",
                "client_id": secret(self.cfg.client_id_env, f"Drive connector {self.name}"),
                "client_secret": secret(self.cfg.client_secret_env, f"Drive connector {self.name}"),
                "refresh_token": secret(self.cfg.refresh_token_env, f"Drive connector {self.name}"),
            }
        r = await self.http.post(TOKEN_URL, data=data, timeout=self.cfg.timeout_seconds)
        if r.status_code >= 400:
            raise PermissionError(f"Google refused the credential ({r.status_code})")
        body = r.json()
        self._token = (str(body["access_token"]), time.time() + float(body.get("expires_in", 3600)))
        return self._token[0]

    async def get(self, path: str, **params: Any) -> httpx.Response:
        """A Drive API GET.

        Returns:
            The response.

        Raises:
            LookupError: No such file.
            PermissionError: Drive refused the request.
        """
        r = await self.http.get(
            f"{API}{path}",
            params=params,
            headers={"Authorization": f"Bearer {await self.token()}"},
            timeout=self.cfg.timeout_seconds,
        )
        if r.status_code == 404:
            raise LookupError("no such file")
        if r.status_code >= 400:
            raise PermissionError(f"Drive answered {r.status_code}")
        return r

    async def meta(self, file_id: str) -> dict[str, Any]:
        """A file's metadata.

        Returns:
            id, name, mimeType, parents...
        """
        return (await self.get(f"/files/{file_id}", fields=FIELDS, supportsAllDrives="true")).json()

    async def inside(self, file_id: str, parents: list[str] | None = None, depth: int = 0) -> bool:
        """Whether a file is in an allowed folder (walking up its parents, cached).

        Returns:
            True when allowed.
        """
        if file_id in self._inside:
            return self._inside[file_id]
        if parents is None:
            parents = (await self.meta(file_id)).get("parents") or []
        direct = any(p in self.cfg.folders for p in parents)
        ok = direct or (
            self.cfg.recursive
            and depth < 12
            and any([await self.inside(p, None, depth + 1) for p in parents])
        )
        self._inside[file_id] = ok
        return ok

    async def tree(self) -> list[str]:
        """The allowed folders and (recursive) their sub-folders, up to 500.

        Returns:
            Folder ids.
        """
        if self._tree is not None:
            return self._tree
        found = list(self.cfg.folders)
        queue = list(self.cfg.folders) if self.cfg.recursive else []
        while queue and len(found) < 500:
            batch, queue = queue[:20], queue[20:]
            q = (
                f"mimeType = {_q(FOLDER)} and trashed = false and ("
                + " or ".join(f"{_q(f)} in parents" for f in batch)
                + ")"
            )
            r = await self.get(
                "/files",
                q=q,
                fields="files(id)",
                pageSize=200,
                supportsAllDrives="true",
                includeItemsFromAllDrives="true",
            )
            for f in r.json().get("files", []):
                if f["id"] not in found:
                    found.append(f["id"])
                    queue.append(f["id"])
                    self._inside[f["id"]] = True
        self._tree = found
        return found

    async def list_documents(self, limit: int = 2_000) -> list[dict[str, Any]]:
        """Every file of the allowed folders, for a knowledge base's sync.

        Returns:
            id, name, mime, modified, url of each file (folders left out).
        """
        out: list[dict[str, Any]] = []
        for folder in await self.tree():
            token: str | None = None
            while len(out) < limit:
                params: dict[str, Any] = {
                    "q": f"{_q(folder)} in parents and trashed = false",
                    "fields": f"nextPageToken,files({FIELDS})",
                    "pageSize": 200,
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                }
                if token:
                    params["pageToken"] = token
                body = (await self.get("/files", **params)).json()
                out += [
                    {
                        "id": f["id"],
                        "name": f.get("name") or f["id"],
                        "mime": f.get("mimeType") or "",
                        "modified": f.get("modifiedTime"),
                        "url": f.get("webViewLink"),
                    }
                    for f in body.get("files", [])
                    if f.get("mimeType") != FOLDER
                ]
                token = body.get("nextPageToken")
                if not token:
                    break
        return out[:limit]

    async def download(self, file_id: str) -> tuple[str, str, bytes]:
        """A file's content (Google Docs, Sheets and Slides exported as text).

        Returns:
            Name, type and bytes.

        Raises:
            PermissionError: The file is outside the allowed folders.
        """
        meta = await self.meta(file_id)
        if not await self.inside(file_id, meta.get("parents") or []):
            raise PermissionError("this file is not in a folder shared with the assistant")
        name, mime = str(meta.get("name") or file_id), str(meta.get("mimeType") or "")
        if mime in EXPORTS:
            r = await self.get(f"/files/{file_id}/export", mimeType=EXPORTS[mime])
            ext = ".csv" if EXPORTS[mime] == "text/csv" else ".txt"
            return name + ext, EXPORTS[mime], r.content
        r = await self.get(f"/files/{file_id}", alt="media", supportsAllDrives="true")
        return name, mime, r.content

    def tools(self) -> list[McpTool]:
        """Search, list a folder, read a file.

        Returns:
            Tools.
        """
        return [
            tool(
                "search_files",
                "Search the shared folders by words in the files' names and text.",
                {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                ["query"],
            ),
            tool(
                "list_folder",
                "The files of a shared folder (default: the first one).",
                {"folder_id": {"type": "string"}},
            ),
            tool(
                "read_file",
                "The text of a file found with search_files or list_folder.",
                {"file_id": {"type": "string"}},
                ["file_id"],
            ),
        ]

    @staticmethod
    def _item(f: dict[str, Any]) -> dict[str, Any]:
        return {
            k: f.get(k)
            for k in ("id", "name", "mimeType", "modifiedTime", "webViewLink")
            if f.get(k)
        }

    async def run(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Run a tool.

        Returns:
            Files, or a file's text.

        Raises:
            PermissionError: A folder or file outside the allowed folders.
            ValueError: An empty search, or a file that cannot be read as text.
        """
        if tool_name == "search_files":
            text = str(args.get("query") or "").strip()
            if not text:
                raise ValueError("give some words to search")
            limit = min(int(args.get("limit") or 20), 50)
            folders = await self.tree()
            out: list[dict[str, Any]] = []
            for i in range(0, len(folders), 20):
                parents = " or ".join(f"{_q(f)} in parents" for f in folders[i : i + 20])
                words = f"(name contains {_q(text)} or fullText contains {_q(text)})"
                q = f"{words} and trashed = false and ({parents})"
                r = await self.get(
                    "/files",
                    q=q,
                    fields=f"files({FIELDS})",
                    pageSize=limit,
                    supportsAllDrives="true",
                    includeItemsFromAllDrives="true",
                )
                out += [
                    self._item(f) for f in r.json().get("files", []) if f.get("mimeType") != FOLDER
                ]
                if len(out) >= limit:
                    break
            return {"files": out[:limit]}
        if tool_name == "list_folder":
            folder = str(args.get("folder_id") or self.cfg.folders[0])
            if not await self.inside(folder):
                raise PermissionError("this folder is not shared with the assistant")
            r = await self.get(
                "/files",
                q=f"{_q(folder)} in parents and trashed = false",
                fields=f"files({FIELDS})",
                pageSize=100,
                supportsAllDrives="true",
                includeItemsFromAllDrives="true",
            )
            return {"files": [self._item(f) for f in r.json().get("files", [])]}
        file_id = str(args.get("file_id") or "")
        meta = await self.meta(file_id)
        if not await self.inside(file_id, meta.get("parents") or []):
            raise PermissionError("this file is not in a folder shared with the assistant")
        mime = str(meta.get("mimeType") or "")
        if mime in EXPORTS:
            r = await self.get(f"/files/{file_id}/export", mimeType=EXPORTS[mime])
            text = r.text
        else:
            r = await self.get(f"/files/{file_id}", alt="media", supportsAllDrives="true")
            try:
                text = extract_text(str(meta.get("name") or ""), mime, r.content)
            except Unreadable as e:
                raise ValueError(str(e)) from e
        cut = len(text) > self.cfg.max_file_chars
        return {**self._item(meta), "text": text[: self.cfg.max_file_chars], "truncated": cut}
