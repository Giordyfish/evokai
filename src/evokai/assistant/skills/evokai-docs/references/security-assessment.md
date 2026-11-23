# Security assessment of the browser session API

Scope: the API a browser uses with a session token, `/v1/session/*` (session info, conversation, replay, chat over
SSE or JSON, cancel, feedback, kiosk exit, bug reports, server voice offer), and what it relies on: token minting
(`POST /v1/session-tokens`), the authenticator, origin checks, the middleware stack and the error handlers. Reviewed
on 2026-11-15 by reading the code (`api/session_routes.py`, `api/voice_routes.py`, `api/deps.py`,
`identity/auth.py`, `core/security.py`, `sessions/*`, `main.py`) and testing each point; the fixes are in the same
change as this document and covered by `tests/test_hardening.py`, `tests/test_privacy.py` and `tests/test_guard_*`.

Severity: High (exploitable to reach another user's data or the service), Medium (weakens a control or enables
abuse), Low (defence in depth, hygiene).

## Findings

| ID | Area | Finding | Severity | Status |
|---|---|---|---|---|
| S1 | Headers | API responses carried no security headers: no `nosniff`, no anti-framing, no `Referrer-Policy`, no HSTS; the console had a CSP but no HSTS. | Low | **Fixed**: `api.hardening` adds `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy` (`no-referrer` for the API, `same-origin` for the console), `Cross-Origin-Opener-Policy`, a CSP `default-src 'none'; frame-ancestors 'none'` on API responses, HSTS when the request came over HTTPS (or `X-Forwarded-Proto: https`). |
| S2 | Caching | Responses holding a session token (`POST /v1/session-tokens`), a sign-in code or conversations had no `Cache-Control`, so an intermediary or the browser could keep them. | Medium | **Fixed**: `Cache-Control: no-store` on every API response that sets none (SSE already had it). |
| S3 | Cancel | `POST /v1/session/turns/{id}/cancel` checked only the subject: a token of one conversation (or of another application with the same subject id) could cancel another conversation's turn if it knew the turn id (a random UUID, so not guessable, but disclosed in `X-Turn-Id` and events). | Low | **Fixed**: a token cancels turns of its own conversation only. |
| S4 | Revocation | A session token could not be revoked before it expired (up to 15 minutes): no sign-out everywhere, and an erased person's tokens kept working. | Medium | **Fixed**: `POST /v1/privacy/revoke-sessions` and every erase revoke the subject's tokens minted until then (a keyed hash of the subject, a millisecond issue time in tokens, checked on every request with a 5 s cache). |
| S5 | Rate limits | No limit on requests per token: a stolen or scripted token could hammer replay, conversation reads and bug reports; turns had only the spending caps. | Medium | **Fixed**: 240 requests per token per minute on the whole session API (429 `rate_limited`), 20 bug reports per user per hour, and the security layer's per-user turn rate, concurrency and conversation length (monitor by default, see [security](security.md)). |
| S6 | Body size | No request body limit: a large JSON body (an external history, a page text, a bug report context) was read whole into memory before validation. | Medium | **Fixed**: 413 above 2 MB on the session API and 8 MB elsewhere, by `Content-Length` and by counting streamed bodies. |
| S7 | CORS | No CORS handling: cross-origin browsers could not call the API at all (safe, but preflights got 405), so embedding Evokai on another origin needed a same-origin proxy. | Low | **Fixed**: preflights and responses of `/v1/session/*` allow the origins the applications declare, with `Authorization` and no credentials; the per-application origin check still runs on every request; other paths never answer CORS. |
| S8 | Origin check | Requests without `Origin` are accepted (non-browser clients); the token is a bearer secret. A token stolen from a browser can be replayed from a script until it expires. | Low | Accepted, mitigated: tokens live 5 minutes by default (at most 15), are bound to one subject, agent and conversation, cannot be minted by a browser, and can now be revoked (S4) and rate-limited (S5). Binding tokens to a client key (DPoP) is a possible next step. |
| S9 | Tokens | JWT validation: HS256 only (no `none`, no algorithm confusion), issuer checked, `exp`, `iat`, `jti`, `sub`, `aud` required; console cookies use another issuer so neither passes for the other. `aud` is the application id and is read rather than verified (the issuer and the key are what matter). | Info | No change. |
| S10 | Token in URLs | Tokens are only sent in `Authorization`; SSE runs over `fetch` with a POST, the WebRTC offer too. No token appears in a URL, a log line or a `Referer`. | Info | No change. |
| S11 | SSE | The stream sends a comment every 10 s, stops at `done`, cancels the turn when the client goes away and has `X-Accel-Buffering: no`. Events carry `turnId` and `seq`; replay returns only the token's conversation (at most 2,000 events per call). | Info | No change. |
| S12 | Error leakage | Errors are `{error, message}` with stable codes; unhandled exceptions answer `internal` without detail; validation errors list field paths only; model and tool failures reach the stream as generic messages. Log lines carry error classes, and now have personal data and secrets masked (`pii.logs`). | Info | No change. |
| S13 | Kiosk exit | The exit code is hashed with scrypt and limited to 5 attempts per session. | Info | No change. |
| S14 | Data in bug reports | Bug reports store free text and a client context (up to 20 kB) with no masking. | Low | Partly addressed: erased with the subject, rate-limited (S5); masking free text is out of scope (no reliable detector for names). |
| S15 | Console login throttling | Failed console logins are counted per client address; behind a proxy that address is the proxy's unless uvicorn trusts its `X-Forwarded-For` (`FORWARDED_ALLOW_IPS`), so one attacker can lock everyone out for 5 minutes. | Low | Open, documented: the console is internal (SSM port-forward); set `FORWARDED_ALLOW_IPS` to the load balancer's addresses if it is ever published. |
| S16 | Voice catalogue | `PUT /v1/admin/voices/{provider}/{id}` changes the deployment-wide voice catalogue with any application's admin key. | Low | Narrowed: console logins need the owner role for it (console roles); bearer admin keys keep their behaviour for compatibility. |

## Residual risks and recommendations

- Prompt injection is mitigated, not solved: keep tools least-privilege (the user's own credential, read-only keys
  for server runs) and confirmations on every change; move `input.injection` and `untrusted.injection` to enforce
  once the security events show no false positives.
- Per-instance counters (S5 and the security layer's limits) multiply with the number of tasks; a shared store
  (Redis, the database) would make them global.
- The deployment's proxy should also enforce TLS and its own rate limits; Evokai's are a second line.
- Keep the console off the public internet even with SSO and roles.
