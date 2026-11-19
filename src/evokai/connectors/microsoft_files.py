"""Microsoft SharePoint and OneDrive: search and read files inside the folders you allow.

An app registration with Microsoft Graph application permissions (``Files.Read.All`` or
``Sites.Selected``) signs in with the client credentials flow: ``tenant_id``, ``client_id`` and the
secret from the environment (``client_secret_env``). The drive is a SharePoint document library or a
OneDrive (``drive_id``, or ``site_id`` for the site's default library). ``folders`` are paths in
that drive; a file outside them is refused even when the app could read it. Word, Excel and
PowerPoint files are read as text, as are text files.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar, cast
from urllib.parse import quote

import httpx
from pydantic import Field, field_validator, model_validator

from evokai.connectors.base import Connector, ConnectorConfig, secret, tool
from evokai.connectors.text import Unreadable, extract_text
from evokai.mcp.client import McpTool

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
SELECT = "id,name,file,folder,size,lastModifiedDateTime,webUrl,parentReference"


class MicrosoftConfig(ConnectorConfig):
    """``connector: microsoft_files``."""

    tenant_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    client_secret_env: str = Field(min_length=1)
    drive_id: str | None = None
    site_id: str | None = None
    folders: list[str] = Field(min_length=1)
    """Folder paths in the drive, e.g. ``/Policies`` or ``/Shared/Contracts``."""
    max_file_chars: int = Field(default=20_000, ge=1_000, le=200_000)
    timeout_seconds: float = Field(default=20, gt=0, le=120)

    @field_validator("folders")
    @classmethod
    def _paths(cls, v: list[str]) -> list[str]:
        return ["/" + f.strip().strip("/") for f in v]

    @model_validator(mode="after")
    def _drive(self) -> MicrosoftConfig:
        if not (self.drive_id or self.site_id):
            raise ValueError("give drive_id or site_id")
        return self


class MicrosoftFilesConnector(Connector):
    """Files in a SharePoint library or a OneDrive, read-only."""

    kind: ClassVar[str] = "microsoft_files"
    title: ClassVar[str] = "SharePoint and OneDrive (read-only)"
    Config: ClassVar[type[ConnectorConfig]] = MicrosoftConfig

    def __init__(
        self, name: str, config: ConnectorConfig, http: httpx.AsyncClient | None = None
    ) -> None:
        """Bind to the configuration."""
        super().__init__(name, config)
        self.cfg = cast(MicrosoftConfig, config)
        self.http = http or httpx.AsyncClient()
        self._token: tuple[str, float] | None = None

    @property
    def drive(self) -> str:
        """The drive's path in Graph."""
        return (
            f"/drives/{self.cfg.drive_id}"
            if self.cfg.drive_id
            else f"/sites/{self.cfg.site_id}/drive"
        )

    async def token(self) -> str:
        """An app token (client credentials), refreshed a minute before it expires.

        Returns:
            The token.

        Raises:
            PermissionError: Microsoft refused the credential.
        """
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        r = await self.http.post(
            f"{LOGIN}/{quote(self.cfg.tenant_id, safe='')}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.cfg.client_id,
                "client_secret": secret(
                    self.cfg.client_secret_env, f"Microsoft connector {self.name}"
                ),
                "scope": "https://graph.microsoft.com/.default",
            },
            timeout=self.cfg.timeout_seconds,
        )
        if r.status_code >= 400:
            raise PermissionError(f"Microsoft refused the credential ({r.status_code})")
        body = r.json()
        self._token = (str(body["access_token"]), time.time() + float(body.get("expires_in", 3600)))
        return self._token[0]

    async def get(self, path: str, **params: Any) -> httpx.Response:
        """A Graph GET (redirects to the file content are followed).

        Returns:
            The response.

        Raises:
            LookupError: No such file or folder.
            PermissionError: Graph refused the request.
        """
        r = await self.http.get(
            f"{GRAPH}{path}",
            params=params,
            headers={"Authorization": f"Bearer {await self.token()}"},
            timeout=self.cfg.timeout_seconds,
            follow_redirects=True,
        )
        if r.status_code == 404:
            raise LookupError("no such file or folder")
        if r.status_code >= 400:
            raise PermissionError(f"Graph answered {r.status_code}")
        return r

    def folder_of(self, item: dict[str, Any]) -> str:
        """The drive path of an item's folder (``/Policies``).

        Returns:
            The path.
        """
        path = str((item.get("parentReference") or {}).get("path") or "")
        _, _, inner = path.partition("root:")
        return "/" + inner.strip("/")

    def inside(self, item: dict[str, Any]) -> bool:
        """Whether an item is in an allowed folder (or is one).

        Returns:
            True when allowed.
        """
        folder = self.folder_of(item)
        own = folder.rstrip("/") + "/" + str(item.get("name") or "") if item.get("folder") else None
        return any(
            p == f or p.startswith(f.rstrip("/") + "/")
            for f in self.cfg.folders
            for p in (folder, own)
            if p
        )

    @staticmethod
    def _item(i: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": i.get("id"),
            "name": i.get("name"),
            "folder": bool(i.get("folder")),
            "modified": i.get("lastModifiedDateTime"),
            "webUrl": i.get("webUrl"),
        }

    async def list_documents(self, limit: int = 2_000) -> list[dict[str, Any]]:
        """Every file of the allowed folders and their sub-folders, for a knowledge base's sync.

        Returns:
            id, name, mime, modified, url of each file.
        """
        out: list[dict[str, Any]] = []
        queue = [f.rstrip("/") or "/" for f in self.cfg.folders]
        seen: set[str] = set()
        while queue and len(out) < limit and len(seen) < 500:
            path = queue.pop(0)
            if path in seen:
                continue
            seen.add(path)
            url: str | None = f"{self.drive}/root:{quote(path, safe='/')}:/children"
            params: dict[str, Any] = {"$select": SELECT, "$top": "200"}
            while url and len(out) < limit:
                body = (await self.get(url, **params)).json()
                for i in body.get("value", []):
                    if i.get("folder"):
                        queue.append(f"{path.rstrip('/')}/{i.get('name')}")
                    elif self.inside(i):
                        out.append({
                            "id": i.get("id"),
                            "name": i.get("name") or i.get("id"),
                            "mime": (i.get("file") or {}).get("mimeType") or "",
                            "modified": i.get("lastModifiedDateTime"),
                            "url": i.get("webUrl"),
                        })
                link = body.get("@odata.nextLink")
                url = link.removeprefix(GRAPH) if link else None
                params = {}
        return out[:limit]

    async def download(self, item_id: str) -> tuple[str, str, bytes]:
        """A file's content.

        Returns:
            Name, type and bytes.

        Raises:
            PermissionError: The file is outside the allowed folders.
        """
        meta = (
            await self.get(f"{self.drive}/items/{quote(item_id, safe='')}", **{"$select": SELECT})
        ).json()
        if not self.inside(meta) or meta.get("folder"):
            raise PermissionError("this file is not in a folder shared with the assistant")
        r = await self.get(f"{self.drive}/items/{quote(item_id, safe='')}/content")
        mime = str((meta.get("file") or {}).get("mimeType") or "")
        return str(meta.get("name") or item_id), mime, r.content

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
                "The files of a shared folder: one of "
                + ", ".join(self.cfg.folders)
                + " or a folder inside them.",
                {"path": {"type": "string"}},
            ),
            tool(
                "read_file",
                "The text of a file found with search_files or list_folder.",
                {"item_id": {"type": "string"}},
                ["item_id"],
            ),
        ]

    async def run(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Run a tool.

        Returns:
            Files, or a file's text.

        Raises:
            PermissionError: A folder or file outside the allowed folders.
            ValueError: An empty search, or a file that cannot be read as text.
        """
        if tool_name == "search_files":
            text = str(args.get("query") or "").strip().replace("'", "''")
            if not text:
                raise ValueError("give some words to search")
            limit = min(int(args.get("limit") or 20), 50)
            out: list[dict[str, Any]] = []
            for folder in self.cfg.folders:
                path = quote(folder, safe="/")
                r = await self.get(
                    f"{self.drive}/root:{path}:/search(q='{quote(text, safe='')}')",
                    **{"$select": SELECT, "$top": str(limit)},
                )
                out += [
                    self._item(i)
                    for i in r.json().get("value", [])
                    if not i.get("folder") and self.inside(i)
                ]
            return {"files": out[:limit]}
        if tool_name == "list_folder":
            path = "/" + str(args.get("path") or self.cfg.folders[0]).strip().strip("/")
            if not any(path == f or path.startswith(f.rstrip("/") + "/") for f in self.cfg.folders):
                raise PermissionError("this folder is not shared with the assistant")
            r = await self.get(
                f"{self.drive}/root:{quote(path, safe='/')}:/children",
                **{"$select": SELECT, "$top": "200"},
            )
            return {"path": path, "files": [self._item(i) for i in r.json().get("value", [])]}
        item_id = str(args.get("item_id") or "")
        meta = (
            await self.get(f"{self.drive}/items/{quote(item_id, safe='')}", **{"$select": SELECT})
        ).json()
        if not self.inside(meta) or meta.get("folder"):
            raise PermissionError("this file is not in a folder shared with the assistant")
        r = await self.get(f"{self.drive}/items/{quote(item_id, safe='')}/content")
        try:
            text = extract_text(
                str(meta.get("name") or ""),
                str((meta.get("file") or {}).get("mimeType") or ""),
                r.content,
            )
        except Unreadable as e:
            raise ValueError(str(e)) from e
        cut = len(text) > self.cfg.max_file_chars
        return {**self._item(meta), "text": text[: self.cfg.max_file_chars], "truncated": cut}
