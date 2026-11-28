"""Sanitised shapes of the 2026-11-27 captures (plan, appendix A): the structure is real, every
value is a fake. No token, cookie, e-mail or conversation text of a real person is here.

Codex's are measured (2026-11-27 and 2026-11-28, Codex CLI 0.167.2). ChatGPT on the web and GitHub
Copilot are **unmeasured**: built from the public formats, marked so where they are defined."""

from __future__ import annotations

import base64
import gzip
import json
from typing import Any

ORG = "00000000-0000-4000-8000-0000000000aa"
OTHER_ORG = "00000000-0000-4000-8000-0000000000bb"
ACCOUNT = "00000000-0000-4000-8000-000000000001"
CONV = "00000000-0000-4000-8000-0000000000c1"
SESSION = "cse_00000000fake"
SESSION_KEY = "fake-session-cookie-0123456789abcdef"
EMAIL = "ada@example.com"
SECRET = "AKIA" + "IOSFODNN7EXAMPLE"
"""AWS's own documentation example key."""
CLI_UA = "claude-cli/2.1.325 (external, cli)"
DESKTOP_CODE_UA = "claude-cli/2.1.325 (external, claude-desktop, agent-sdk/0.3.325)"
ELECTRON_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Claude/5.11492.5 Electron/39.0.0"


def user_id(account: str = ACCOUNT) -> str:
    """``metadata.user_id`` as Claude Code sends it (a JSON string, 186 characters)."""
    return json.dumps({
        "device_id": "f" * 64,
        "account_uuid": account,
        "session_id": "00000000-0000-4000-8000-0000000000d1",
    })


def messages_body(text: str, *, stream: bool = True, account: str = ACCOUNT, **more: Any) -> dict:
    """A Claude Code ``/v1/messages`` body: history, a tool result, the newest user turn."""
    return {
        "model": "claude-sonnet-4-5",
        "max_tokens": 32000,
        "stream": stream,
        "system": [{"type": "text", "text": "You are Claude Code, a fake system prompt."}],
        "metadata": {"user_id": user_id(account)},
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "earlier turn"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "earlier answer"}]},
            {"role": "user", "content": [{"type": "text", "text": text}]},
        ],
        **more,
    }


def oauth_profile(email: str = EMAIL, account: str = ACCOUNT, org: str = ORG) -> dict:
    """``GET api.anthropic.com/api/oauth/profile``."""
    return {
        "account": {
            "uuid": account,
            "full_name": "Ada Example",
            "display_name": "Ada",
            "email": email,
            "has_claude_max": False,
            "has_claude_pro": False,
        },
        "organization": {
            "uuid": org,
            "name": "Example Org",
            "organization_type": "claude_team",
            "billing_type": "stripe_subscription",
        },
    }


def token_answer(email: str = EMAIL, account: str = ACCOUNT, org: str = ORG) -> dict:
    """Claude Code's OAuth token answer (``POST platform.claude.com/v1/oauth/token``)."""
    return {
        "token_type": "Bearer",
        "access_token": "fake-access",
        "refresh_token": "fake-refresh",
        "expires_in": 28800,
        "scope": "user:inference user:profile",
        "account": {"uuid": account, "email_address": email},
        "organization": {"uuid": org, "name": "Example Org"},
    }


def claude_account(email: str = EMAIL, account: str = ACCOUNT, org: str = ORG) -> dict:
    """``GET claude.ai/api/account`` (key names as the probe read them)."""
    return {
        "uuid": account,
        "email_address": email,
        "full_name": "Ada Example",
        "memberships": [{"organization": {"uuid": org, "name": "Example Org"}, "role": "user"}],
    }


def chat_body(prompt: str, **more: Any) -> dict:
    """A claude.ai chat completion request."""
    return {
        "prompt": prompt,
        "timezone": "Europe/Rome",
        "locale": "en-US",
        "model": "claude-opus-5-5",
        "effort": "high",
        "thinking_mode": "auto",
        "turn_message_uuids": {
            "human_message_uuid": "00000000-0000-4000-8000-0000000000e1",
            "assistant_message_uuid": "00000000-0000-4000-8000-0000000000e2",
        },
        "attachments": [],
        "files": [],
        "sync_sources": [],
        "completion_request_id": "00000000-0000-4000-8000-0000000000e3",
        "rendering_mode": "messages",
        "create_conversation_params": {
            "name": "",
            "model": "claude-opus-5-5",
            "include_conversation_preferences": True,
            "is_temporary": False,
        },
        "tools": [{"type": "fake_tool", "name": f"tool_{i}"} for i in range(3)],
        **more,
    }


def gz(data: Any) -> bytes:
    """A gzip JSON body, as Desktop sends it."""
    return gzip.compress(json.dumps(data).encode())


CHAT_SSE = (
    b'event: ping\ndata: {"type":"ping"}\n\n',
    b'event: conversation_ready\ndata: {"type":"conversation_ready","is_temporary":false}\n\n',
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"chatcompl_fake",'
    b'"type":"message","role":"assistant","model":"claude-opus-5-5","content":[]}}\n\n',
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"Ciao! Come pos"}}\n\n',
    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n',
    b'event: message_limit\ndata: {"type":"message_limit",'
    b'"message_limit":{"type":"within_limit"}}\n\n',
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
)


def cowork_body(text: str, *, with_reminder: bool = False) -> dict:
    """A Cowork events request: an initialize control request and the person's message."""
    events: list[dict] = [
        {
            "payload": {
                "type": "control_request",
                "request_id": "sdk-mcp-init-fake",
                "request": {"subtype": "initialize"},
                "uuid": "00000000-0000-4000-8000-0000000000f1",
            }
        }
    ]
    if with_reminder:
        events.append({
            "payload": {
                "type": "user",
                "client_platform": "desktop_app",
                "message": {
                    "role": "user",
                    "content": "<system-reminder>App context, nothing secret.</system-reminder>",
                },
                "parent_tool_use_id": None,
                "uuid": "00000000-0000-4000-8000-0000000000f2",
            }
        })
    events.append({
        "payload": {
            "type": "user",
            "client_platform": "desktop_app",
            "message": {"role": "user", "content": text},
            "parent_tool_use_id": None,
            "uuid": "00000000-0000-4000-8000-0000000000f3",
        }
    })
    return {"events": events}


def bridge_result(text: str, rpc_id: int = 7) -> bytes:
    """A JSON-RPC result the app sends on the bridge (a file Cowork read on the PC)."""
    return json.dumps({
        "result": {"content": [{"type": "text", "text": text}]},
        "jsonrpc": "2.0",
        "id": rpc_id,
    }).encode()


ANTHROPIC_SSE = (
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_fake",'
    b'"model":"claude-sonnet-4-5","usage":{"input_tokens":12,"cache_read_input_tokens":100,'
    b'"cache_creation_input_tokens":30,"output_tokens":1}}}\n\n',
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"ok"}}\n\n',
    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":7}}\n\n',
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
)


# OpenAI: Codex with a ChatGPT login (measured)

CHATGPT_ACCOUNT = "00000000-0000-4000-8000-0000000000ca"
OTHER_CHATGPT_ACCOUNT = "00000000-0000-4000-8000-0000000000cb"
CHATGPT_USER = "user-fakeChatgptUser0001__" + CHATGPT_ACCOUNT
CODEX_UA = "codex_exec/0.167.2 (Linux; x86_64) fake"


def chatgpt_jwt(
    email: str = EMAIL, account: str = CHATGPT_ACCOUNT, user: str = CHATGPT_USER
) -> str:
    """A ChatGPT login's access token: the claims' structure is real, the signature is not."""

    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    claims = {
        "aud": ["https://api.openai.com/v1"],
        "client_id": "app_fake",
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account,
            "chatgpt_account_user_id": user,
            "chatgpt_plan_type": "team",
            "chatgpt_user_id": "user-fakeChatgptUser0001",
            "user_id": "user-fakeChatgptUser0001",
        },
        "https://api.openai.com/profile": {"email": email, "email_verified": True},
        "iss": "https://auth.openai.com",
        "sub": "fake|0001",
        "exp": 4102444800,
    }
    return f"{part({'alg': 'RS256', 'typ': 'JWT'})}.{part(claims)}.ZmFrZS1zaWduYXR1cmU"


def codex_warmup() -> dict:
    """The first ``response.create`` of a run: no input, ``generate: false``."""
    return {
        "type": "response.create",
        "model": "gpt-fake-codex",
        "generate": False,
        "input": [],
        "include": ["reasoning.encrypted_content"],
        "parallel_tool_calls": True,
        "prompt_cache_key": "00000000-0000-4000-8000-0000000000c9",
        "reasoning": {"effort": "high"},
        "store": False,
        "stream": True,
        "text": {"verbosity": "low"},
        "tool_choice": "auto",
        "client_metadata": {"fake": "1"},
    }


def codex_create(text: str | None = None, *, tool_output: str | None = None) -> dict:
    """A ``response.create`` with the person's prompt (the app's context first), or a tool output.

    As measured: developer messages, the environment context as a user message, then the prompt;
    after a tool call, only the ``custom_tool_call_output`` with ``previous_response_id``.
    """
    frame = {
        **{k: v for k, v in codex_warmup().items() if k != "generate"},
        "access_programs": [],
        "previous_response_id": "resp_fake_warmup",
    }
    if tool_output is not None:
        frame["input"] = [
            {"type": "custom_tool_call_output", "call_id": "call_fake", "output": tool_output}
        ]
        return frame
    context = "<environment_context>fake</environment_context>"
    frame["input"] = [
        {
            "type": "message",
            "role": "developer",
            "content": [{"type": "input_text", "text": "Fake developer instructions."}],
        },
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": context}]},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
    ]
    return frame


def responses_events() -> list[dict]:
    """The server's events of one answer, as the Codex WebSocket sends them (one per message)."""
    usage = {
        "input_tokens": 20314,
        "input_tokens_details": {"cached_tokens": 12288},
        "output_tokens": 5,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 20319,
    }
    item = {"id": "msg_fake", "type": "message", "role": "assistant"}
    part = {"type": "output_text", "text": "ok", "annotations": []}
    at = {"item_id": "msg_fake", "output_index": 0, "content_index": 0}
    response = {"id": "resp_fake", "object": "response", "status": "in_progress", "output": []}
    done = {**item, "content": [part]}
    return [
        {"type": "codex.rate_limits", "rate_limits": {"primary": {"used_percent": 1.0}}},
        {"type": "codex.response.metadata", "metadata": {"fake": True}},
        {"type": "response.created", "response": response},
        {"type": "response.in_progress", "response": response},
        {"type": "response.output_item.added", "output_index": 0, "item": {**item, "content": []}},
        {"type": "response.content_part.added", **at, "part": {**part, "text": ""}},
        {"type": "response.output_text.delta", **at, "delta": "ok"},
        {"type": "response.output_text.done", **at, "text": "ok"},
        {"type": "response.content_part.done", **at, "part": part},
        {"type": "response.output_item.done", "output_index": 0, "item": done},
        {"type": "responsesapi.websocket_timing", "timing": {"fake": 1}},
        {
            "type": "response.completed",
            "response": {**response, "status": "completed", "output": [done], "usage": usage},
        },
    ]


def responses_sse() -> bytes:
    """The same answer over HTTP (Server-Sent Events, no content type: as ChatGPT streams it)."""
    return b"".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in responses_events()
    )


def responses_body(text: str) -> dict:
    """A Responses request over HTTP (Codex without WebSocket)."""
    return {k: v for k, v in codex_create(text).items() if k != "type"}


# ChatGPT on the web and the desktop app (unmeasured: built from the public format knowledge)


def chatgpt_conversation(text: str, **more: Any) -> dict:
    """``POST chatgpt.com/backend-api/conversation`` (unmeasured)."""
    return {
        "action": "next",
        "messages": [
            {
                "id": "00000000-0000-4000-8000-0000000000d2",
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": [text]},
                "metadata": {"attachments": [{"id": "file-fake", "name": "n.txt"}]},
            }
        ],
        "conversation_id": "00000000-0000-4000-8000-0000000000d3",
        "parent_message_id": "00000000-0000-4000-8000-0000000000d4",
        "model": "auto",
        "timezone": "Europe/Rome",
        **more,
    }


# GitHub Copilot (unmeasured: built from the public format knowledge)

COPILOT_TID = "fakeTrackingId0001"
GITHUB_LOGIN = "ada-example"
GITHUB_TOKEN = "fake-github-oauth-token-0001"
COPILOT_UA = "GitHubCopilotChat/0.0.0-fake"


def copilot_token(tid: str = COPILOT_TID) -> str:
    """A Copilot token's shape (``tid=...;exp=...;...:<mac>``), fake values."""
    return (
        f"tid={tid};exp=4102444800;sku=copilot_for_business_seat;"
        "proxy-ep=proxy.business.githubcopilot.com;8kp=1:fakemac"
    )


def copilot_token_answer(tid: str = COPILOT_TID) -> dict:
    """``GET api.github.com/copilot_internal/v2/token`` (unmeasured)."""
    return {
        "token": copilot_token(tid),
        "expires_at": 4102444800,
        "refresh_in": 1500,
        "endpoints": {"api": "https://api.business.githubcopilot.com"},
    }


def github_user(email: str | None = None, login: str = GITHUB_LOGIN) -> dict:
    """``GET api.github.com/user`` (public format; the e-mail is null unless public)."""
    return {"login": login, "id": 1, "type": "User", "name": "Ada Example", "email": email}


def copilot_user(
    email: str | None = EMAIL, login: str = GITHUB_LOGIN, orgs: tuple[str, ...] = ("example-org",)
) -> dict:
    """``GET api.github.com/copilot_internal/user`` (unmeasured)."""
    return {
        "login": login,
        "email": email,
        "access_type_sku": "copilot_for_business_seat",
        "analytics_tracking_id": COPILOT_TID,
        "chat_enabled": True,
        "organization_login_list": list(orgs),
        "organization_list": [{"login": o, "name": o} for o in orgs],
    }


def copilot_chat(text: str, *, stream: bool = True) -> dict:
    """``POST api.*.githubcopilot.com/chat/completions`` (unmeasured): history, a tool call, its
    result, the newest user turn."""
    call = {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": "{}"}}
    return {
        "model": "gpt-fake",
        "stream": stream,
        "messages": [
            {"role": "system", "content": "Fake Copilot instructions."},
            {"role": "user", "content": "earlier turn"},
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": "call_1", "content": "tool result"},
            {"role": "user", "content": [{"type": "text", "text": text}]},
        ],
    }


COPILOT_CHAT_SSE = (
    b'data: {"id":"c1","object":"chat.completion.chunk","model":"gpt-fake",'
    b'"choices":[{"index":0,"delta":{"role":"assistant","content":"ok"}}]}\n\n',
    b'data: {"id":"c1","object":"chat.completion.chunk","model":"gpt-fake",'
    b'"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
    b'"usage":{"prompt_tokens":321,"completion_tokens":4,"total_tokens":325,'
    b'"prompt_tokens_details":{"cached_tokens":64}}}\n\n',
    b"data: [DONE]\n\n",
)
"""Copilot's chat answer (unmeasured): OpenAI chat completion chunks, usage in the last one."""


def copilot_completion(prompt: str, suffix: str = "", *, stream: bool = True) -> dict:
    """An inline suggestion request (unmeasured)."""
    return {
        "prompt": "# Path: app/settings.py\n" + prompt,
        "suffix": suffix,
        "max_tokens": 500,
        "temperature": 0,
        "top_p": 1,
        "n": 1,
        "stop": ["\n\n"],
        "stream": stream,
        "extra": {"language": "python"},
    }
