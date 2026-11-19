"""Ready connectors: configuration rules, each connector against a fake, and the agent's turn."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from evokai.connectors import build, describe, validate
from evokai.connectors.imap import ImapConfig, ImapConnector
from evokai.connectors.server import mcp_app
from evokai.connectors.text import extract_text
from evokai.core.errors import BadRequest
from evokai.mcp.client import McpClient
from tests.conftest import Env, Tool, types

CONSOLE = {"X-Evokai-Console": "1"}


# ── configuration ─────────────────────────────────────────────────────────────────────────────────


def test_secrets_in_clear_are_refused_and_kinds_are_known() -> None:
    with pytest.raises(BadRequest) as e:
        validate("imap", {"host": "h", "username": "u", "password": "hunter2", "password_env": "X"})
    assert "password_env" in e.value.message
    with pytest.raises(BadRequest):
        validate(
            "openapi", {"spec": {}, "operations": ["a"], "auth": {"secret": "s", "secret_env": "X"}}
        )
    with pytest.raises(BadRequest, match="Unknown connector"):
        validate("ftp", {})
    kinds = {d["kind"] for d in describe()}
    assert kinds == {
        "sql",
        "openapi",
        "google_drive",
        "microsoft_files",
        "imap",
        "microsoft_365",
        "google_workspace",
    }


# ── SQL ───────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def crm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    db = tmp_path / "crm.db"
    c = sqlite3.connect(db)
    c.executescript(
        """
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT, city TEXT);
        CREATE TABLE invoices (
          id INTEGER PRIMARY KEY, customer_id INTEGER, amount REAL, status TEXT
        );
        CREATE TABLE secrets (id INTEGER PRIMARY KEY, value TEXT);
        INSERT INTO customers VALUES (1, 'Northwind', 'a@n.example', 'Rome'),
          (2, 'Contoso', 'b@c.example', 'Milan'), (3, 'Fabrikam', 'c@f.example', 'Rome');
        INSERT INTO invoices VALUES (1, 1, 120.5, 'open'), (2, 1, 80, 'paid'), (3, 2, 300, 'open');
        INSERT INTO secrets VALUES (1, 'nope');
        """
    )
    c.commit()
    c.close()
    monkeypatch.setenv("CRM_DSN", f"sqlite+aiosqlite:///{db}")
    return str(db)


SQL = {
    "dsn_env": "CRM_DSN",
    "tables": ["customers", "invoices"],
    "hidden_columns": {"customers": ["email"]},
    "row_limit": 2,
    "queries": {
        "open_by_city": {
            "description": "Open invoices of the customers of a city.",
            "sql": "SELECT c.name, i.amount FROM invoices i "
            "JOIN customers c ON c.id = i.customer_id "
            "WHERE i.status = 'open' AND c.city = :city ORDER BY i.amount",
            "params": {"city": "string"},
        }
    },
}


@pytest.mark.usefixtures("crm")
async def test_sql_reads_allowed_tables_with_bound_values_and_limits() -> None:
    c = build("crm", "sql", SQL)
    try:
        names = [t.name for t in await c.list_tools()]
        assert names == [
            "list_tables",
            "describe_table",
            "query",
            "aggregate",
            "query_open_by_city",
        ]
        assert all(t.read_only for t in await c.list_tools())
        listed = json.loads((await c.call_tool("list_tables", {})).text)
        assert listed[0] == {"table": "customers", "columns": ["id", "name", "city"]}

        r = await c.call_tool(
            "query",
            {
                "table": "customers",
                "filters": [{"column": "city", "op": "eq", "value": "Rome"}],
                "order_by": [{"column": "name"}],
            },
        )
        assert json.loads(r.text) == {
            "rows": [
                {"id": 3, "name": "Fabrikam", "city": "Rome"},
                {"id": 1, "name": "Northwind", "city": "Rome"},
            ],
            "more": False,
        }
        # The row limit holds; values are bound, so SQL in a value is just a string.
        many = json.loads((await c.call_tool("query", {"table": "customers", "limit": 50})).text)
        assert len(many["rows"]) == 2 and many["more"] is True
        sneaky = await c.call_tool(
            "query",
            {
                "table": "customers",
                "filters": [{"column": "name", "op": "eq", "value": "x' OR '1'='1"}],
            },
        )
        assert json.loads(sneaky.text)["rows"] == []

        agg = json.loads(
            (
                await c.call_tool(
                    "aggregate",
                    {
                        "table": "invoices",
                        "group_by": ["status"],
                        "metrics": [{"fn": "sum", "column": "amount"}, {"fn": "count"}],
                    },
                )
            ).text
        )
        assert agg["rows"] == [
            {"status": "open", "sum_amount_0": 420.5, "count_rows_1": 2},
            {"status": "paid", "sum_amount_0": 80.0, "count_rows_1": 1},
        ]
        named = json.loads((await c.call_tool("query_open_by_city", {"city": "Rome"})).text)
        assert named["rows"] == [{"name": "Northwind", "amount": 120.5}]

        refused = [
            await c.call_tool("query", {"table": "secrets"}),
            await c.call_tool("query", {"table": "customers", "columns": ["email"]}),
            await c.call_tool("describe_table", {"table": "sqlite_master"}),
            await c.call_tool("insert_row", {"table": "customers", "values": {"name": "x"}}),
        ]
        assert all(not r.ok for r in refused), [r.text for r in refused]
    finally:
        await c.close()


@pytest.mark.usefixtures("crm")
async def test_sql_writes_only_when_enabled_and_never_from_a_named_query() -> None:
    with pytest.raises(BadRequest, match=r"only reads|SELECT"):
        validate(
            "sql", {**SQL, "queries": {"x": {"description": "d", "sql": "DELETE FROM customers"}}}
        )
    with pytest.raises(BadRequest, match="one statement"):
        validate(
            "sql",
            {
                **SQL,
                "queries": {"x": {"description": "d", "sql": "SELECT 1; DROP TABLE customers"}},
            },
        )
    c = build("crm", "sql", {**SQL, "writes": True, "write_tables": ["invoices"]})
    try:
        tools = {t.name: t for t in await c.list_tools()}
        assert tools["insert_row"].mutating and tools["update_rows"].mutating
        ok = await c.call_tool(
            "update_rows",
            {
                "table": "invoices",
                "values": {"status": "paid"},
                "filters": [{"column": "id", "op": "eq", "value": 1}],
            },
        )
        assert json.loads(ok.text) == {"updated": 1}
        no_filter = await c.call_tool(
            "update_rows", {"table": "invoices", "values": {"status": "paid"}}
        )
        assert not no_filter.ok
        other = await c.call_tool("insert_row", {"table": "customers", "values": {"name": "x"}})
        assert not other.ok
    finally:
        await c.close()


@pytest.mark.usefixtures("crm")
async def test_a_connector_is_an_mcp_server_of_the_application_under_its_grants(env: Env) -> None:
    put = await env.client.put(
        "/agent/v1/admin/mcp-servers/crm",
        json={"connector": "sql", "config": {**SQL, "writes": True, "write_tables": ["invoices"]}},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert put.status_code == 200, put.text
    secret = await env.client.put(
        "/agent/v1/admin/mcp-servers/bad",
        json={"connector": "sql", "config": {**SQL, "dsn": "postgres://u:p@h/db"}},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert secret.status_code == 400 and "dsn_env" in secret.json()["message"]
    agents = (
        await env.client.get(
            "/agent/v1/admin/agents", headers={"Authorization": f"Bearer {env.admin_key}"}
        )
    ).json()
    helper = {k: v for k, v in agents["items"][0].items() if k != "version"}
    helper["mcp_servers"] = ["app", "crm"]
    r = await env.client.put(
        "/agent/v1/admin/agents/helper",
        json=helper,
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200, r.text

    env.script.add(
        "function:primary",
        [
            Tool(
                "query",
                {
                    "table": "customers",
                    "filters": [{"column": "city", "op": "eq", "value": "Milan"}],
                },
            )
        ],
        ["Contoso is in Milan."],
    )
    tok = await env.token()
    events = await env.chat(tok["token"], text="Who is in Milan?")
    assert {"type": "tool", "name": "query", "status": "done"}.items() <= next(
        e for e in events if e["type"] == "tool" and e["status"] == "done"
    ).items()
    tool_result = [m for _, msgs in env.script.seen for m in msgs][-1]
    assert "Contoso" in str(tool_result)

    # A tool that changes data waits for the user's confirmation.
    env.script.add(
        "function:primary",
        [
            Tool(
                "insert_row",
                {"table": "invoices", "values": {"customer_id": 2, "amount": 5, "status": "open"}},
            )
        ],
    )
    events = await env.chat(tok["token"], text="Add an invoice")
    assert "confirm" in types(events)

    # Revoking a tool takes it away from the next turn.
    g = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "tool", "name": "crm/query", "value": False}]},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert g.status_code == 200, g.text
    env.script.tools_seen.clear()
    tok2 = await env.token(subject="u2")
    await env.chat(tok2["token"], text="hello")
    assert "query" not in env.script.tools_seen and "aggregate" in env.script.tools_seen

    listed = (
        await env.client.get(
            "/agent/v1/admin/mcp-servers", headers={"Authorization": f"Bearer {env.admin_key}"}
        )
    ).json()
    crm_row = next(x for x in listed["items"] if x["name"] == "crm")
    assert crm_row["connector"] == "sql" and crm_row["url"] == "connector://sql"
    test = await env.client.post(
        "/agent/v1/admin/mcp-servers/crm/test",
        json={},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert test.json()["ok"] is True and len(test.json()["tools"]) == 7
    exported = (
        await env.client.get(
            "/agent/v1/admin/export", headers={"Authorization": f"Bearer {env.admin_key}"}
        )
    ).json()
    assert "connector: sql" in exported["yaml"] and "dsn_env: CRM_DSN" in exported["yaml"]


# ── REST / OpenAPI ────────────────────────────────────────────────────────────────────────────────

SPEC = {
    "openapi": "3.0.0",
    "servers": [{"url": "https://api.shop.example/v1"}],
    "paths": {
        "/orders/{id}": {
            "get": {
                "operationId": "getOrder",
                "summary": "One order",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "X-Api-Key", "in": "header", "schema": {"type": "string"}},
                ],
            }
        },
        "/orders": {
            "post": {
                "operationId": "createOrder",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/New"}}
                    }
                },
            },
            "get": {
                "operationId": "listOrders",
                "parameters": [{"name": "status", "in": "query", "schema": {"type": "string"}}],
            },
        },
    },
    "components": {
        "schemas": {"New": {"type": "object", "properties": {"title": {"type": "string"}}}}
    },
}


async def test_openapi_operations_become_tools_with_auth_and_a_host_allow_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORDERS_KEY", "k-123")
    seen: list[httpx.Request] = []

    def api(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"id": "T-9", **json.loads(request.content)})
        return httpx.Response(200, json={"id": "T-1", "status": request.url.params.get("status")})

    http = httpx.AsyncClient(transport=httpx.MockTransport(api))
    cfg = {
        "spec": SPEC,
        "operations": ["getOrder", "createOrder", "listOrders"],
        "auth": {"header": "X-Api-Key", "prefix": "", "secret_env": "ORDERS_KEY"},
    }
    c = build("orders", "openapi", cfg, http)
    tools = {t.name: t for t in await c.list_tools()}
    assert set(tools) == {"getOrder", "listOrders"}  # POST needs writes: true
    assert "X-Api-Key" not in tools["getOrder"].input_schema["properties"]
    r = await c.call_tool("getOrder", {"id": "T 1/../2"})
    assert r.ok and json.loads(r.text)["body"]["id"] == "T-1"
    assert (
        seen[-1].url.host == "api.shop.example"
        and seen[-1].url.raw_path == b"/v1/orders/T%201%2F..%2F2"
    )
    assert seen[-1].headers["x-api-key"] == "k-123"
    assert (
        json.loads((await c.call_tool("listOrders", {"status": "open"})).text)["body"]["status"]
        == "open"
    )
    assert not (await c.call_tool("createOrder", {"body": {"title": "x"}})).ok

    w = build("orders", "openapi", {**cfg, "writes": True}, http)
    tools = {t.name: t for t in await w.list_tools()}
    assert (
        tools["createOrder"].mutating
        and tools["createOrder"].input_schema["properties"]["body"]["properties"]["title"]
    )
    assert (
        json.loads((await w.call_tool("createOrder", {"body": {"title": "x"}})).text)["body"]["id"]
        == "T-9"
    )

    elsewhere = build(
        "orders",
        "openapi",
        {**cfg, "base_url": "https://evil.example", "allowed_hosts": ["api.shop.example"]},
        http,
    )
    await elsewhere.list_tools()
    r = await elsewhere.call_tool("getOrder", {"id": "1"})
    assert not r.ok and "host not allowed" in r.text
    await http.aclose()


# ── documents ─────────────────────────────────────────────────────────────────────────────────────


def _docx(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "word/document.xml",
            f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
    return buf.getvalue()


def test_text_from_office_files_html_and_plain_text() -> None:
    assert extract_text("a.docx", "", _docx("Hello &amp; welcome")) == "Hello & welcome"
    assert (
        extract_text("a.html", "text/html", b"<p>One</p><script>x()</script><p>Two</p>")
        == "One\nTwo"
    )
    assert extract_text("a.csv", "text/csv", b"a,b") == "a,b"
    with pytest.raises(ValueError, match="cannot be read"):
        extract_text("a.pdf", "application/pdf", b"%PDF")


async def test_google_drive_reads_only_inside_the_shared_folders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for k, v in {"G_ID": "cid", "G_SECRET": "cs", "G_REFRESH": "rt"}.items():
        monkeypatch.setenv(k, v)
    files = {
        "doc1": {
            "id": "doc1",
            "name": "Policy",
            "mimeType": "application/vnd.google-apps.document",
            "parents": ["sub"],
        },
        "sub": {
            "id": "sub",
            "name": "Sub",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["shared"],
        },
        "out": {
            "id": "out",
            "name": "Payroll.txt",
            "mimeType": "text/plain",
            "parents": ["private"],
        },
        "private": {
            "id": "private",
            "name": "Private",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [],
        },
    }

    def drive(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            assert b"refresh_token=rt" in request.content
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer at"
        path = request.url.path.removeprefix("/drive/v3")
        if path == "/files":
            q = request.url.params["q"]
            if "mimeType = 'application/vnd.google-apps.folder'" in q:
                return httpx.Response(
                    200, json={"files": [{"id": "sub"}] if "'shared' in parents" in q else []}
                )
            assert "'shared' in parents" in q and "'sub' in parents" in q
            return httpx.Response(200, json={"files": [files["doc1"]]})
        if path.endswith("/export"):
            return httpx.Response(200, text="The policy text.")
        fid = path.split("/")[2]
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=b"secret salaries")
        return httpx.Response(200, json=files[fid]) if fid in files else httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(drive))
    c = build(
        "drive",
        "google_drive",
        {
            "folders": ["shared"],
            "client_id_env": "G_ID",
            "client_secret_env": "G_SECRET",
            "refresh_token_env": "G_REFRESH",
        },
        http,
    )
    found = json.loads((await c.call_tool("search_files", {"query": "policy"})).text)
    assert [f["id"] for f in found["files"]] == ["doc1"]
    read = json.loads((await c.call_tool("read_file", {"file_id": "doc1"})).text)
    assert read["text"] == "The policy text." and read["truncated"] is False
    refused = await c.call_tool("read_file", {"file_id": "out"})
    assert not refused.ok and "not in a folder shared" in refused.text
    assert all(t.read_only for t in await c.list_tools())
    await http.aclose()


async def test_sharepoint_reads_only_inside_the_shared_folders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MS_SECRET", "s3")
    items = {
        "i1": {
            "id": "i1",
            "name": "Rules.docx",
            "file": {"mimeType": ""},
            "parentReference": {"path": "/drive/root:/Policies/HR"},
        },
        "i2": {
            "id": "i2",
            "name": "Salaries.txt",
            "file": {"mimeType": "text/plain"},
            "parentReference": {"path": "/drive/root:/Finance"},
        },
    }

    def graph(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "login.microsoftonline.com/tenant-1/oauth2/v2.0/token" in url:
            assert (
                b"client_secret=s3" in request.content and b"client_credentials" in request.content
            )
            return httpx.Response(200, json={"access_token": "gt", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer gt"
        if "/search(" in url:
            return httpx.Response(200, json={"value": list(items.values())})
        if url.endswith("/content"):
            return httpx.Response(200, content=_docx("Leave rules"))
        iid = request.url.path.split("/items/")[1]
        return httpx.Response(200, json=items[iid])

    http = httpx.AsyncClient(transport=httpx.MockTransport(graph))
    c = build(
        "sp",
        "microsoft_files",
        {
            "tenant_id": "tenant-1",
            "client_id": "app-1",
            "client_secret_env": "MS_SECRET",
            "drive_id": "d1",
            "folders": ["Policies"],
        },
        http,
    )
    found = json.loads((await c.call_tool("search_files", {"query": "rules"})).text)
    assert [f["id"] for f in found["files"]] == ["i1"]
    read = json.loads((await c.call_tool("read_file", {"item_id": "i1"})).text)
    assert read["text"] == "Leave rules"
    assert not (await c.call_tool("read_file", {"item_id": "i2"})).ok
    assert not (await c.call_tool("list_folder", {"path": "/Finance"})).ok
    await http.aclose()


# ── IMAP ──────────────────────────────────────────────────────────────────────────────────────────

MAIL = (
    b"From: Ada <ada@example.com>\r\nTo: support@example.com\r\nSubject: Invoice question\r\n"
    b"Date: Mon, 2 Nov 2026 10:00:00 +0000\r\nContent-Type: text/plain\r\n\r\n"
    b"Why is October higher?\r\n"
)


class FakeImap:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"1"]

    def uid(self, command: str, *args: Any) -> tuple[str, Any]:
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b"41 42"]
        if "HEADER.FIELDS" in args[1]:
            return "OK", [(b"hdr", MAIL.split(b"\r\n\r\n")[0])]
        return "OK", [(b"msg", MAIL)]

    def logout(self) -> None:
        self.calls.append(("logout",))


async def test_imap_examines_folders_and_never_marks_messages_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAIL_PASSWORD", "pw")
    fake = FakeImap()
    cfg = ImapConfig.model_validate({
        "host": "imap.example",
        "username": "support",
        "password_env": "MAIL_PASSWORD",
        "folders": ["INBOX", "Clienti"],
    })
    c = ImapConnector("mail", cfg, factory=lambda _cfg: fake)
    found = json.loads(
        (
            await c.call_tool(
                "search_messages", {"folder": "INBOX", "text": 'x" OR ALL', "since": "2026-11-01"}
            )
        ).text
    )
    assert [m["uid"] for m in found["messages"]] == ["42", "41"] and found["messages"][0][
        "subject"
    ] == "Invoice question"
    assert ("select", '"INBOX"', True) in fake.calls
    search = next(x for x in fake.calls if x[:2] == ("uid", "SEARCH"))
    assert search[3:] == ("TEXT", '"x\\" OR ALL"', "SINCE", "01-Nov-2026")
    msg = json.loads((await c.call_tool("read_message", {"folder": "INBOX", "uid": "42"})).text)
    assert (
        msg["text"].strip() == "Why is October higher?" and msg["from"] == "Ada <ada@example.com>"
    )
    assert all("PEEK" in str(x) for x in fake.calls if x[:2] == ("uid", "FETCH"))
    assert not (await c.call_tool("read_message", {"folder": "Sent", "uid": "1"})).ok
    with pytest.raises(BadRequest):
        validate("imap", {"host": "h", "username": "u", "password_env": "X", "writes": True})


@pytest.mark.usefixtures("crm")
async def test_bootstrap_registers_connectors_from_the_config_file(
    env: Env, tmp_path: Path
) -> None:
    from evokai.agents.bootstrap import bootstrap

    (tmp_path / "c.yaml").write_text(
        "application: {slug: test, name: Test App}\n"
        "mcp_servers:\n  - name: crm\n    connector: sql\n"
        "    config: {dsn_env: CRM_DSN, tables: [customers]}\n",
        encoding="utf-8",
    )
    await bootstrap(env.svc.store, env.svc.keyring, tmp_path / "c.yaml", {})
    row = next(s for s in await env.svc.store.mcp_servers.all(env.app_id) if s.name == "crm")
    assert row.connector == "sql" and row.config == {"dsn_env": "CRM_DSN", "tables": ["customers"]}


@pytest.mark.usefixtures("crm")
async def test_a_connector_served_alone_speaks_mcp() -> None:
    connector = build("crm", "sql", SQL)
    app = mcp_app(connector, token="t0k")
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://c")
    client = McpClient(
        name="crm", url="http://c/mcp", http=http, headers={"Authorization": "Bearer t0k"}
    )
    tools = await client.list_tools()
    assert [t.name for t in tools][:2] == ["list_tables", "describe_table"]
    assert all(t.read_only for t in tools)
    r = await client.call_tool("query", {"table": "invoices", "limit": 1})
    assert r.ok and json.loads(r.text)["rows"][0]["id"] == 1
    denied = await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert denied.status_code == 401
    await http.aclose()
    await connector.close()
