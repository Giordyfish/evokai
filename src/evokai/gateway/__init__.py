"""Gateways for coding tools: Claude Code and Codex pass through Evokai with people's own seats.

A **gateway** is stored as an application row with ``kind = "gateway"`` (roles, the audit log,
security events, retention and data protection are keyed by the application id), but it is never
an application anywhere a person or an API client looks: it has no agents, knowledge, workflows or
channels, it is listed only under ``/v1/admin/gateways``, and no application key or session token
reaches it. Each person has a personal gateway key (``evk_gw_...``, an API key row of kind
``gateway``) sent in the ``x-evokai-key`` header; the key alone says who and which gateway.

The doors forward the provider's own API unchanged (``evokai.gateway.relay``) after the gateway's
controls (``evokai.gateway.checks``), and record one row per request
(``evokai.gateway.store``): tokens, timings and outcome, never content and never an amount.
See ``docs/gateway.md``.
"""
