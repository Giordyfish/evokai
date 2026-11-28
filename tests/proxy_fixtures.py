"""Sanitised shapes of the 2026-11-27 captures (plan, appendix A): the structure is real, every
value is a fake. No token, cookie, e-mail or conversation text of a real person is here."""

from __future__ import annotations

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
