"""What each channel accepts.

Secrets are never stored: keys ending in ``_env`` name environment variables of the service, and a
configuration with a secret in clear is refused.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from evokai.connectors.base import plaintext_secrets
from evokai.core.errors import BadRequest


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _no_plaintext_secrets(cls, data: Any) -> Any:
        if bad := plaintext_secrets(data):
            raise ValueError(
                "secrets come from the environment: use "
                + ", ".join(f"{k}_env" for k in bad)
                + " with the name of an environment variable"
            )
        return data


class Mapping(_Strict):
    """How a person of the channel becomes the application's subject."""

    strategy: Literal["hashed", "id", "webhook"] = "hashed"
    """``hashed``: a keyed hash of the channel's id of the person (an e-mail address, an Entra
    object id, a Slack user id), stable and opaque; ``id``: the id itself with the channel as
    prefix (``teams:<object id>``); ``webhook``: the application answers who it is."""
    webhook_url: str | None = Field(default=None, max_length=500)
    """``webhook``: a signed POST (``X-Evokai-Signature``) with the channel's identity, answered
    with ``{"subject": "..."}``, or ``{"subject": null}`` for someone the application does not
    know."""
    webhook_secret_env: str | None = None
    """The environment variable with the webhook's signing secret."""
    cache_seconds: int = Field(default=300, ge=0, le=86_400)
    """How long a webhook's answer is kept."""

    @model_validator(mode="after")
    def _webhook(self) -> Mapping:
        if self.strategy == "webhook" and not (self.webhook_url and self.webhook_secret_env):
            raise ValueError("the webhook strategy needs webhook_url and webhook_secret_env")
        if self.webhook_url and not self.webhook_url.startswith(("https://", "http://")):
            raise ValueError("webhook_url is an http(s) address")
        return self


class Rate(_Strict):
    """Deliveries one person may make (the application's guard and caps apply too)."""

    per_user_per_hour: int = Field(default=60, ge=1, le=10_000)
    per_channel_per_minute: int = Field(default=120, ge=1, le=100_000)


class ImapInbound(_Strict):
    """Where the mailbox is read (IMAP, polled)."""

    host: str = Field(min_length=1)
    port: int = 993
    ssl: bool = True
    username: str | None = None
    username_env: str | None = None
    password_env: str = Field(min_length=1)
    folder: str = "INBOX"
    poll_seconds: int = Field(default=60, ge=15, le=3_600)
    timeout_seconds: float = Field(default=20, gt=0, le=120)

    @model_validator(mode="after")
    def _user(self) -> ImapInbound:
        if not (self.username or self.username_env):
            raise ValueError("give username or username_env")
        return self


class SmtpOutbound(_Strict):
    """Where replies are sent (SMTP)."""

    host: str = Field(min_length=1)
    port: int = 587
    starttls: bool = True
    ssl: bool = False
    username: str | None = None
    username_env: str | None = None
    password_env: str | None = None
    timeout_seconds: float = Field(default=20, gt=0, le=120)


class GraphOutbound(_Strict):
    """Where replies are sent or drafted (Microsoft Graph).

    An app registration with ``Mail.Send`` and ``Mail.ReadWrite`` application permissions on the
    mailbox.
    """

    tenant_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    client_secret_env: str = Field(min_length=1)
    mailbox: str | None = None
    """The mailbox (its address or id); the channel's address by default."""


class Attachments(_Strict):
    """What happens to a message's attachments."""

    mode: Literal["input", "knowledge", "ignore"] = "input"
    """``input``: their text goes to the run as attachments (marked as data); ``knowledge``: also
    added to a knowledge base; ``ignore``: only their names are given."""
    knowledge_base: str | None = None
    max_count: int = Field(default=5, ge=0, le=20)
    max_bytes: int = Field(default=5_000_000, ge=1_000, le=25_000_000)
    """Larger attachments are skipped (their names are still given)."""
    max_chars: int = Field(default=20_000, ge=500, le=200_000)

    @model_validator(mode="after")
    def _kb(self) -> Attachments:
        if self.mode == "knowledge" and not self.knowledge_base:
            raise ValueError("the knowledge mode needs knowledge_base")
        return self


class EmailConfig(_Strict):
    """``kind: email``: an inbound mailbox per agent."""

    address: str = Field(min_length=3, max_length=320)
    """The agent's address: replies come from it, and mail from it is never answered."""
    aliases: list[str] = Field(default_factory=list, max_length=20)
    """Other addresses of the same mailbox (never answered either)."""
    display_name: str | None = Field(default=None, max_length=120)
    inbound: Literal["imap", "webhook"] = "webhook"
    """``imap``: Evokai polls the mailbox; ``webhook``: an inbound mail provider posts each
    message (raw RFC 822) to the channel's endpoint, signed."""
    imap: ImapInbound | None = None
    webhook_secret_env: str | None = None
    """``webhook``: the environment variable with the signing secret of deliveries."""
    reply: Literal["send", "draft", "none"] = "draft"
    """``send``: the answer is sent; ``draft``: it is saved as a draft for a person to review and
    send; ``none``: the run happens (for its effects and the conversation) without a reply."""
    transport: Literal["smtp", "graph"] = "smtp"
    smtp: SmtpOutbound | None = None
    graph: GraphOutbound | None = None
    drafts_folder: str = "Drafts"
    """With SMTP, drafts are appended to this IMAP folder (``imap`` is then needed)."""
    allowed_senders: list[str] = Field(default_factory=list, max_length=200)
    """Addresses or ``@domain`` entries allowed to write (empty: anyone)."""
    max_body_chars: int = Field(default=20_000, ge=500, le=200_000)
    max_replies_per_sender_per_hour: int = Field(default=10, ge=1, le=1_000)
    """Loop protection: at most this many replies to one address an hour."""
    attachments: Attachments = Field(default_factory=Attachments)
    mapping: Mapping = Field(default_factory=Mapping)
    rate: Rate = Field(default_factory=Rate)

    @model_validator(mode="after")
    def _complete(self) -> EmailConfig:
        if self.inbound == "imap" and self.imap is None:
            raise ValueError("inbound imap needs the imap section")
        if self.inbound == "webhook" and not self.webhook_secret_env:
            raise ValueError("inbound webhook needs webhook_secret_env")
        if self.reply != "none":
            if self.transport == "smtp" and self.smtp is None:
                raise ValueError("transport smtp needs the smtp section")
            if self.transport == "graph" and self.graph is None:
                raise ValueError("transport graph needs the graph section")
            if self.reply == "draft" and self.transport == "smtp" and self.imap is None:
                raise ValueError("drafts with SMTP are saved over IMAP: add the imap section")
        return self


class TeamsConfig(_Strict):
    """``kind: teams``: a Microsoft Teams bot (an Azure Bot resource with the Teams channel)."""

    app_id: str = Field(min_length=1, max_length=120)
    """The bot's Microsoft App ID (the Entra app registration's client id)."""
    app_password_env: str = Field(min_length=1)
    """The environment variable with the app registration's client secret."""
    tenant_id: str | None = Field(default=None, max_length=120)
    """The tenant of a single-tenant bot (its tokens are asked there); None: multi-tenant."""
    allowed_tenants: list[str] = Field(default_factory=list, max_length=50)
    """Tenants whose people may write (empty: the bot's own tenant only when ``tenant_id`` is set,
    else any)."""
    service_url_hosts: list[str] = Field(
        default_factory=lambda: ["smba.trafficmanager.net", "*.botframework.com"], max_length=20
    )
    """Hosts replies may be posted to (the activity's serviceUrl must be one of them)."""
    mapping: Mapping = Field(default_factory=lambda: Mapping(strategy="id"))
    rate: Rate = Field(default_factory=Rate)


class SlackConfig(_Strict):
    """``kind: slack``: a Slack app (Events API, interactivity, a bot token)."""

    signing_secret_env: str = Field(min_length=1)
    bot_token_env: str = Field(min_length=1)
    team_id: str | None = Field(default=None, max_length=40)
    """The workspace allowed to write (None: any workspace the app is installed in)."""
    api_url: str = Field(default="https://slack.com/api", max_length=200)
    """Slack's Web API (changed only for tests)."""
    mapping: Mapping = Field(default_factory=lambda: Mapping(strategy="id"))
    rate: Rate = Field(default_factory=Rate)


CONFIGS: dict[str, type[_Strict]] = {
    "email": EmailConfig,
    "teams": TeamsConfig,
    "slack": SlackConfig,
}


def validate(kind: str, config: dict[str, Any] | None) -> Any:
    """A channel's configuration, checked.

    Returns:
        The configuration.

    Raises:
        BadRequest: An unknown kind, a secret in clear or an invalid configuration.
    """
    cls = CONFIGS.get(kind)
    if cls is None:
        raise BadRequest(f"Unknown channel {kind!r}: email, teams or slack.", code="channel")
    try:
        return cls.model_validate(config or {})
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(map(str, x['loc'])) or kind}: {x['msg']}" for x in e.errors()[:6]
        )
        raise BadRequest(
            f"The {kind} channel's configuration is not valid: {problems}.", code="channel"
        ) from e


def env_names(config: Any) -> list[str]:
    """The environment variables a configuration names (for the console's status).

    Returns:
        Their names.
    """
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if str(k).endswith("_env") and isinstance(x, str) and x:
                    out.append(x)
                else:
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(config.model_dump() if isinstance(config, BaseModel) else config)
    return list(dict.fromkeys(out))
