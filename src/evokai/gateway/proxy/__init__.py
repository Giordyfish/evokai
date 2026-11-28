"""The gateway proxy: the gateway's rules for apps that cannot be pointed at a base URL.

``evokai proxy`` is a TLS-inspecting forward proxy (its own process, same image and database as
the API) for Claude Desktop (Chat, Cowork, Code), Claude Code and, later, Codex and Copilot. PCs
reach it through a PAC file (``/proxy.pac`` on the API) that names only the AI hosts of the
catalogue (``catalogue``); it refuses every other host, so it is never an open proxy. It mints a
certificate per host from an intermediate CA whose Name Constraints keep it to those hosts
(``ca``), and it learns who each app session belongs to from answers that pass anyway
(``identity``), without ever calling a provider with a person's credentials.

Each surface has an adapter (``adapters``): it finds the text that is new in a request, the
model, and how to refuse there. The checks are the gateway's (``evokai.gateway.service``), and so
are the records and the security events: one usage row per model request, never content. The
network engine is mitmproxy, used as a library behind ``engine``: everything else here is free
of it, so it can be replaced. See ``docs/gateway-proxy.md``.
"""
