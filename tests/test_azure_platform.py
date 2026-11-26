"""Azure Container Apps dynamic sessions for code_run, Azure Monitor, and their catalog facts.

No Azure account: the session pool is a mock transport shaped from the dynamic sessions REST
documentation (executions, DELETE session), the token a fake.
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from evokai.llm import providers as pc
from evokai.observability import telemetry
from evokai.protection import DataProtection, insights_region, processor_verdict
from evokai.sandbox import interpreter as it
from evokai.sandbox.tool import CodeService

POOL = (
    "https://swedencentral.dynamicsessions.io/subscriptions/0000/resourceGroups/rg/"
    "sessionPools/evokai-python"
)
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


async def _token() -> str:
    await asyncio.sleep(0)
    return "tok-sessions"


class Pool:
    """A fake session pool: three executions per call (setup, code, collect), then DELETE."""

    def __init__(self, status: str = "Success", fail: int | None = None, slow: bool = False):
        self.requests: list[httpx.Request] = []
        self.status = status
        self.fail = fail
        self.slow = slow

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        n = sum(1 for r in self.requests if r.method == "POST")
        if self.fail == n:
            return httpx.Response(500)
        if self.slow and n == 2:
            await asyncio.sleep(5)
        code = json.loads(request.content)["properties"]["code"]
        if n == 1:
            return httpx.Response(200, json={"properties": {"status": "Success", "stdout": ""}})
        if n == 2:
            return httpx.Response(
                200,
                json={
                    "properties": {
                        "status": self.status,
                        "stdout": "mean 4.5\n",
                        "stderr": "" if self.status == "Success" else "Traceback ...",
                        "executionResult": "42",
                        "executionTimeInMilliseconds": 12,
                    }
                },
            )
        assert "EVOKAI_OUTPUT_FILES:" in code
        files = [
            ["chart.png", len(PNG), base64.b64encode(PNG).decode()],
            ["table.csv", 8, base64.b64encode(b"a,b\n1,2\n").decode()],
            ["model.bin", 10, base64.b64encode(b"\x00" * 10).decode()],
            ["huge.png", 10**7, None],
        ]
        return httpx.Response(
            200,
            json={
                "properties": {
                    "status": "Success",
                    "stdout": "EVOKAI_OUTPUT_FILES:" + json.dumps(files),
                }
            },
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


async def test_a_call_runs_in_a_fresh_session_that_is_deleted() -> None:
    pool = Pool()
    sandbox = it.ContainerAppsInterpreter(POOL, http=pool.client(), token=_token)
    assert sandbox.region == "swedencentral" and sandbox.identifier == "evokai-python"
    out = await sandbox.run("print(4.5)", [("data.csv", "x\n1\n")], timeout=30)
    assert not out.error and not out.timed_out
    assert out.stdout == "mean 4.5\n42" and out.execution_ms == 12
    assert [f.name for f in out.files] == ["chart.png", "table.csv"]
    assert out.files[0].data == PNG and out.files[1].text == "a,b\n1,2\n"
    assert sorted(out.skipped) == ["huge.png", "model.bin"]
    setup, code, collect, delete = pool.requests
    ids = {r.url.params["identifier"] for r in pool.requests}
    assert ids == {out.session_id} and out.session_id.startswith("evokai-")
    for r in (setup, code, collect):
        assert r.url.path.endswith("/sessionPools/evokai-python/executions")
        assert r.url.params["api-version"] == "2025-10-02-preview"
        assert r.headers["authorization"] == "Bearer tok-sessions"
        body = json.loads(r.content)["properties"]
        assert body["codeInputType"] == "inline" and body["executionType"] == "synchronous"
    # The inputs are written by the setup execution, the code is sent as it is.
    setup_code = json.loads(setup.content)["properties"]["code"]
    assert "data.csv" in setup_code and "/mnt/data" in setup_code
    assert json.loads(code.content)["properties"]["code"] == "print(4.5)"
    assert delete.method == "DELETE" and delete.url.path.endswith("/evokai-python/session")
    assert delete.url.params["api-version"] == "2025-02-02-preview"


async def test_failures_and_timeouts_still_delete_the_session() -> None:
    failed = Pool(status="Failure")
    out = await it.ContainerAppsInterpreter(POOL, http=failed.client(), token=_token).run(
        "1/0", [], timeout=30
    )
    assert out.error and "Traceback" in out.stderr

    down = Pool(fail=2)
    out = await it.ContainerAppsInterpreter(POOL, http=down.client(), token=_token).run(
        "x", [], timeout=30
    )
    assert out.error and "HTTP 500" in out.stderr
    assert down.requests[-1].method == "DELETE"

    slow = Pool(slow=True)
    out = await it.ContainerAppsInterpreter(POOL, http=slow.client(), token=_token).run(
        "x", [], timeout=0.2
    )
    assert out.timed_out and out.error and slow.requests[-1].method == "DELETE"


def test_only_european_session_pools_and_the_code_service() -> None:
    assert it.azure_region_of(POOL) == "swedencentral"
    assert it.azure_region_of("https://example.org/pool") == ""
    with pytest.raises(ValueError, match="European"):
        it.ContainerAppsInterpreter(POOL.replace("swedencentral", "eastus"))

    def settings(**kw: Any) -> Any:
        base = {
            "code_enabled": True,
            "code_sandbox": "azure",
            "code_session_pool_endpoint": POOL,
            "code_region": None,
            "aws_region": "eu-north-1",
            "code_timeout_seconds": 60,
            "code_usd_per_second": 0.00003,
            "code_interpreter_id": "aws.codeinterpreter.v1",
        }
        return SimpleNamespace(**{**base, **kw})

    svc = CodeService(settings())
    assert svc.available and svc.region == "swedencentral"
    assert svc.processor == "azure-container-apps-sessions"
    assert svc.usage_model == "azure:container-apps-sessions"
    assert svc.status()["sandbox"] == "azure-sessions"
    missing = CodeService(settings(code_session_pool_endpoint=None))
    assert not missing.available and "SESSION_POOL_ENDPOINT" in (missing.problem or "")
    us = CodeService(settings(code_session_pool_endpoint=POOL.replace("swedencentral", "westus")))
    assert not us.available and "European" in (us.problem or "")
    aws = CodeService(settings(code_sandbox="agentcore"))
    assert aws.processor == "agentcore-code-interpreter"
    assert aws.usage_model == "agentcore:code-interpreter"


def _dp(**rule: Any) -> dict[str, Any]:
    return {"data_protection": DataProtection.model_validate(rule).stored()}


def test_the_session_pool_and_azure_monitor_are_judged_in_their_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cat = pc.ProviderCatalog.load()
    f = cat.processor_facts("azure-container-apps-sessions", region="swedencentral")
    assert f is not None and (f.geography, f.countries) == ("eu", ["SE"])
    assert f.entity.controlled_from == "US"
    eu = _dp(residency={"geographies": ["eu"]})
    assert processor_verdict(eu, "azure-container-apps-sessions", region="swedencentral").allowed
    assert not processor_verdict(
        _dp(jurisdiction=["eu"]), "azure-container-apps-sessions", region="swedencentral"
    ).allowed

    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    assert insights_region() == "unknown"
    assert not processor_verdict(eu, "azure-monitor", region=insights_region()).allowed
    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
        "IngestionEndpoint=https://westeurope-5.in.applicationinsights.azure.com/;"
        "LiveEndpoint=https://westeurope.livediagnostics.monitor.azure.com/",
    )
    assert insights_region() == "westeurope"
    assert processor_verdict(eu, "azure-monitor", region="westeurope").allowed


def test_the_azure_monitor_exporter_needs_its_connection_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL", "true")
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    assert telemetry.exporters("azure-monitor") is None
    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
        "IngestionEndpoint=https://swedencentral-1.in.applicationinsights.azure.com/",
    )
    made = telemetry.exporters("azure-monitor")
    assert made is not None
    spans, metrics, sampler = made
    assert type(spans).__name__ == "AzureMonitorTraceExporter"
    assert type(metrics).__name__ == "AzureMonitorMetricExporter"
    assert type(sampler).__name__ == "ApplicationInsightsSampler"
    spans.shutdown()
    metrics.shutdown()
    otlp = telemetry.exporters("otlp")
    assert otlp is not None and type(otlp[0]).__name__ == "OTLPSpanExporter" and otlp[2] is None
