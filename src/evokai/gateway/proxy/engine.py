"""The network engine: mitmproxy (MIT), used as a library, behind this one module.

mitmproxy does CONNECT, the per-host certificates, HTTP/1.1 and HTTP/2 on both sides, WebSocket
and streaming. This module turns its flows into neutral ``adapters.Request`` values for
``ProxyService`` and applies what the service decides; nothing else in Evokai imports mitmproxy,
so another engine can take its place behind the same adapters.

What it sets:

- ``connection_strategy = lazy``: nothing connects upstream before a request needs it (a refused
  host, the check host and a synthetic answer never reach a server);
- ``upstream_cert = False``: a leaf names only the host the PC asked for, never the upstream
  certificate's other names (they could fall outside the CA's Name Constraints);
- responses are streamed as they arrive (a buffered SSE stream hung Cowork in the probe), but the
  small answers that name an account, which are read whole;
- request bodies are read whole only on the paths an adapter inspects, up to
  ``EVOKAI_PROXY_MAX_BODY_MB`` (over it: refused); every other body streams through;
- the upstream's certificate is verified against the public roots (certifi).
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path
from typing import Any

from mitmproxy import certs, http, options
from mitmproxy.addons import core, disable_h2c, errorcheck, next_layer, proxyserver, tlsconfig
from mitmproxy.addons import dns_resolver as dns_addon
from mitmproxy.master import Master

from evokai.gateway.proxy.adapters import Answer, Request, Rewrite
from evokai.gateway.proxy.ca import Authority
from evokai.gateway.proxy.service import Pending, ProxyService
from evokai.gateway.relay import elapsed_ms

log = logging.getLogger("evokai.gateway.proxy")

BODY_SLACK = 4
"""mitmproxy's own body limit is this many times ours: a memory guard for bodies of unknown length
(ours refuses with a readable message first; a response over it is cut)."""
_CERT_OPTIONS = {"certs", "confdir", "key_size", "cert_passphrase", "emudhra", "emudhra_old"}


class _Certificates(tlsconfig.TlsConfig):
    """mitmproxy's TLS addon, with our intermediate instead of a CA in a config directory."""

    def __init__(self, store: certs.CertStore) -> None:
        super().__init__()
        self._store = store
        self.certstore = store

    def configure(self, updated: Any) -> None:
        super().configure(set(updated) - _CERT_OPTIONS)
        self.certstore = self._store

    def running(self) -> None:
        self.certstore = self._store


def certstore(authority: Authority, workdir: Path) -> certs.CertStore:
    """A certificate store that mints leaves with the intermediate (cached in memory).

    Returns:
        The store.
    """
    dh = workdir / "dhparam.pem"
    dh.write_bytes(certs.DEFAULT_DHPARAM)
    inter = certs.Cert(authority.cert)
    store = certs.CertStore(
        authority.key,
        inter,
        None,
        certs.dummy_crl(authority.key, authority.cert),
        certs.CertStore.load_dhparam(dh),
    )
    store.default_chain_certs = [certs.Cert(c) for c in authority.chain]
    return store


def _request(flow: http.HTTPFlow, body: bytes = b"") -> Request:
    r = flow.request
    path, _, query = r.path.partition("?")
    headers = [(k.decode("latin-1").lower(), v.decode("latin-1")) for k, v in r.headers.fields]
    peer = flow.client_conn.peername
    return Request(
        method=r.method,
        host=r.pretty_host.lower() if r.scheme == "http" else r.host.lower(),
        path=path,
        query=query,
        headers=headers,
        body=body,
        client=str(peer[0]),
    )


def _response(a: Answer) -> http.Response:
    return http.Response.make(a.status, a.body, a.headers)


def _refused(host: str) -> http.Response:
    return http.Response.make(
        403,
        f"Evokai gateway proxy: {host} is not one of the AI hosts this proxy serves.\n".encode(),
        {"content-type": "text/plain; charset=utf-8"},
    )


class EvokaiAddon:
    """The mitmproxy addon: the hooks call ``ProxyService`` and apply its decisions."""

    def __init__(
        self, proxy: ProxyService, overrides: dict[str, tuple[str, int]] | None = None
    ) -> None:
        """Wire the addon.

        Args:
            proxy: The decisions.
            overrides: Host to (address, port) to connect to instead (tests and the sizing run's
                local fake upstream; never set by configuration).
        """
        self.proxy = proxy
        self.overrides = overrides or {}

    # Connections

    def http_connect(self, flow: http.HTTPFlow) -> None:
        """A ``CONNECT``: only the catalogue's hosts and the check host."""
        host, port = flow.request.host, flow.request.port
        if self.proxy.connect(host, port) is None:
            log.info("proxy_connect_refused", extra={"port": port})
            flow.response = _refused(host)

    def tls_clienthello(self, data: Any) -> None:
        """Tunnelled hosts pass without opening TLS."""
        sni = data.client_hello.sni or ""
        if sni and self.proxy.connect(sni, 443) == "tunnel":
            data.ignore_connection = True

    def server_connect(self, data: Any) -> None:
        """Connect elsewhere for an overridden host (tests and the sizing run only)."""
        if not self.overrides or data.server.address is None:
            return
        target = self.overrides.get(str(data.server.address[0]).lower())
        if target is not None:
            data.server.address = target

    # Requests

    async def requestheaders(self, flow: http.HTTPFlow) -> None:
        """Route a request: the check host, a refused host, an inspected path, or a stream."""
        r = flow.request
        tls = bool(flow.client_conn.tls_established)
        host = r.host.lower() if tls else r.pretty_host.lower()
        role = self.proxy.connect(host, r.port if tls else 443)
        if role == "check":
            flow.response = _response(await self.proxy.check_page(_request(flow), tls=tls))
            return
        if role != "intercept" or not tls:
            flow.response = _refused(host)
            return
        path = r.path.partition("?")[0]
        log.debug("proxy_request", extra={"method": r.method, "host": host, "path": path})
        learn = self.proxy.learns(r.method, host, path)
        if learn:
            flow.metadata["evokai_learn"] = learn
        adapter = self.proxy.adapter(r.method, host, path)
        if adapter is None:
            r.stream = True
            return
        flow.metadata["evokai_adapter"] = adapter
        flow.metadata["evokai_t0"] = time.perf_counter()
        length = r.headers.get("content-length", "")
        if length.isdigit() and int(length) > self.proxy.max_body:
            flow.response = _response(self.proxy.too_large(adapter, _request(flow)))
            flow.metadata["evokai_done"] = True
            return
        r.stream = False

    async def request(self, flow: http.HTTPFlow) -> None:
        """Decide on a request an adapter inspects, now that its body is here."""
        adapter = flow.metadata.get("evokai_adapter")
        if adapter is None or flow.response is not None or flow.metadata.get("evokai_done"):
            return
        raw = flow.request.raw_content or b""
        if len(raw) > self.proxy.max_body:
            flow.response = _response(self.proxy.too_large(adapter, _request(flow)))
            return
        try:
            body = flow.request.get_content(strict=True) or b""
        except ValueError:
            # A content-encoding that cannot be read is never forwarded unread (fail closed).
            log.warning("proxy_format_unknown", extra={"adapter": adapter.name, "why": "encoding"})
            texts = self.proxy.texts(None)
            flow.response = _response(
                adapter.failed(_request(flow), texts.get("format_unknown", contact=texts.contact))
            )
            return
        if len(body) > self.proxy.max_body:
            flow.response = _response(self.proxy.too_large(adapter, _request(flow)))
            return
        try:
            decision = await self.proxy.decide(_request(flow, body), adapter)
        except Exception as e:  # fail closed: a bug or a database outage never lets it through
            log.exception("proxy_decision_failed", extra={"error_type": type(e).__name__})
            flow.response = _response(
                adapter.failed(
                    _request(flow),
                    "Evokai gateway proxy: the policy could not be checked. Try again.",
                )
            )
            return
        if isinstance(decision.verdict, Answer):
            flow.response = _response(decision.verdict)
        elif isinstance(decision.verdict, Rewrite):
            flow.request.content = decision.verdict.body
        if decision.pending is not None:
            flow.metadata["evokai_pending"] = decision.pending

    # Responses

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        """Stream every answer as it arrives (but the small ones that name an account)."""
        if flow.response is None:
            return
        if flow.metadata.get("evokai_learn") and flow.response.status_code == 200:
            flow.response.stream = False
            return
        pending: Pending | None = flow.metadata.get("evokai_pending")
        if pending is None:
            flow.response.stream = True
            return
        t0 = flow.metadata.get("evokai_t0", time.perf_counter())
        flow.metadata["evokai_first_byte"] = elapsed_ms(t0)
        usage = self.proxy.usage(
            pending,
            flow.response.headers.get("content-type", ""),
            flow.response.headers.get("content-encoding", ""),
        )
        if usage is None:
            flow.response.stream = True
            return
        flow.metadata["evokai_usage"] = usage

        def tap(chunk: bytes) -> bytes:
            if chunk:
                usage.feed(chunk)
            return chunk

        flow.response.stream = tap

    async def response(self, flow: http.HTTPFlow) -> None:
        """An answer ended: learn an account, record a model request."""
        if flow.response is None:
            return
        learn = flow.metadata.get("evokai_learn")
        if learn and flow.response.status_code == 200 and not flow.response.stream:
            try:
                body = flow.response.get_content(strict=False) or b""
            except ValueError:
                body = b""
            await self.proxy.learn(learn, _request(flow), 200, body)
        await self._finish(
            flow, "forwarded" if flow.response.status_code < 400 else "upstream_error"
        )

    async def error(self, flow: http.HTTPFlow) -> None:
        """A forwarded request failed (the upstream, or the PC went away)."""
        msg = flow.error.msg if flow.error else ""
        outcome = "cancelled" if "client disconnect" in msg.lower() else "failed"
        await self._finish(flow, outcome)

    async def _finish(self, flow: http.HTTPFlow, outcome: str) -> None:
        pending: Pending | None = flow.metadata.pop("evokai_pending", None)
        if pending is None:
            return
        usage = flow.metadata.get("evokai_usage")
        if usage is not None:
            usage.finish()
            if outcome in ("cancelled", "failed") and usage.complete:
                outcome = "forwarded"
        status = flow.response.status_code if flow.response is not None else 502
        self.proxy.done(pending, status, usage, outcome, flow.metadata.get("evokai_first_byte"))

    # WebSocket

    async def websocket_message(self, flow: http.HTTPFlow) -> None:
        """A message from the app on a WebSocket an adapter reads."""
        if flow.websocket is None:
            return
        msg = flow.websocket.messages[-1]
        if not msg.from_client:
            return
        adapter = self.proxy.frame_adapter(flow.request.host, flow.request.path.partition("?")[0])
        if adapter is None:
            return
        try:
            verdict = await self.proxy.frame(_request(flow), adapter, msg.content)
        except Exception as e:
            log.exception("proxy_decision_failed", extra={"error_type": type(e).__name__})
            msg.drop()
            return
        if verdict.drop:
            msg.drop()
        elif verdict.replace is not None:
            msg.content = verdict.replace


class Engine:
    """``evokai proxy``'s server: mitmproxy's master with the minimum of its addons and ours."""

    def __init__(
        self,
        proxy: ProxyService,
        authority: Authority,
        *,
        host: str,
        port: int,
        trusted_ca: str | None = None,
        overrides: dict[str, tuple[str, int]] | None = None,
    ) -> None:
        """Set up (nothing listens until ``run``).

        Args:
            proxy: The decisions.
            authority: The intermediate CA.
            host: The address to bind.
            port: The port (0: any free one, read ``port`` after ``started``).
            trusted_ca: A PEM file of the upstreams' CA instead of the public roots (tests and
                the sizing run).
            overrides: Host to (address, port) to connect to instead (tests and the sizing run).
        """
        self.proxy = proxy
        self.authority = authority
        self.host = host
        self.port = port
        self.trusted_ca = trusted_ca
        self.overrides = overrides
        self.master: Master | None = None
        self.started = asyncio.Event()
        self._tmp = tempfile.TemporaryDirectory(prefix="evokai-proxy-")

    async def run(self) -> None:
        """Serve until ``stop``."""
        workdir = Path(self._tmp.name)
        limit = self.proxy.max_body * BODY_SLACK
        opts = options.Options(
            listen_host=self.host,
            listen_port=self.port,
            mode=["regular"],
            confdir=str(workdir),
        )
        master = Master(opts, with_termlog=False)
        master.addons.add(
            core.Core(),
            proxyserver.Proxyserver(),
            dns_addon.DnsResolver(),
            next_layer.NextLayer(),
            _Certificates(certstore(self.authority, workdir)),
            disable_h2c.DisableH2C(),
            EvokaiAddon(self.proxy, self.overrides),
            errorcheck.ErrorCheck(),
        )
        update: dict[str, Any] = {
            "connection_strategy": "lazy",
            "upstream_cert": False,
            "body_size_limit": f"{limit}b",
            "http3": False,
        }
        if self.trusted_ca:
            update["ssl_verify_upstream_trusted_ca"] = self.trusted_ca
        opts.update(**update)
        self.master = master
        task = asyncio.create_task(master.run())
        try:
            await self._wait_listening(master, task)
            self.started.set()
            await task
        finally:
            self._tmp.cleanup()

    async def _wait_listening(self, master: Master, task: asyncio.Task[None]) -> None:
        ps = master.addons.get("proxyserver")
        for _ in range(500):
            if task.done():
                task.result()
                raise RuntimeError("the proxy stopped while starting")
            addrs = ps.listen_addrs()
            if addrs:
                self.port = int(addrs[0][1])
                return
            await asyncio.sleep(0.01)
        raise RuntimeError("the proxy did not start listening")

    def stop(self) -> None:
        """Stop serving."""
        if self.master is not None:
            self.master.shutdown()
