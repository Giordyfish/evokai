"""What people read when the proxy refuses or rewrites: English and Italian.

The language is the gateway's (``defaults.locale``; another language falls back to English), or
``EVOKAI_PROXY_LOCALE`` when no gateway is known. A gateway may replace any text in its settings
(``gateway.proxy_messages``: key to text, same ``{placeholders}``). A reason never repeats the
value that matched.
"""

from __future__ import annotations

from typing import Any

from evokai.i18n import locale_of

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "refusal": (
            "⛔ Request blocked by the company policy: the content includes {reason}. "
            "Remove it and try again. If in doubt: {contact}."
        ),
        "refusal_other": (
            "⛔ Request blocked by the company policy: {reason}. If in doubt: {contact}."
        ),
        "instruction": (
            "Reply by repeating exactly the text between quotes, adding nothing and using no "
            'tools: "{refusal}"'
        ),
        "marker": "[Message removed by the company policy]",
        "tool_notice": (
            "[Output removed by the company policy. Reply by repeating exactly the text between "
            'quotes, adding nothing and using no tools: "{refusal}"]'
        ),
        "bridge": (
            "[Content blocked by the company policy: it includes {reason}. Tell the person the "
            "file was blocked and why.]"
        ),
        "format_unknown": (
            "⛔ Format not recognised by the company policy: the request was not sent. "
            "Update the app, or contact {contact}."
        ),
        "org_refused": (
            "⛔ Account outside the company: this app is signed in with an account that does "
            "not belong to the organisation. Sign in with your work account. "
            "If in doubt: {contact}."
        ),
        "unidentified": (
            "⛔ The company policy does not recognise this account yet: restart the app (or "
            "sign out and in again). If it goes on, contact {contact}."
        ),
        "too_large": (
            "⛔ Request too large for the company policy (over {mb} MB): send less at once."
        ),
        "contact": "IT",
        "reason.input.secrets": "a secret or credential",
        "reason.pii.model": "personal data",
        "reason.scope": "a topic the policy does not allow",
        "reason.input.size": "too much text at once",
        "reason.gateway.model": "the model {model} is not allowed",
        "reason.gateway.provider": "this provider is not enabled",
        "reason.gateway.protection": "the data protection profile does not allow this provider",
        "reason.abuse.rate": "too many requests in a minute, wait a moment",
        "reason.gateway.cap": "you have used your token cap",
    },
    "it": {
        "refusal": (
            "⛔ Richiesta bloccata dalla policy aziendale: il contenuto include {reason}. "
            "Toglila e riprova. Per dubbi: {contact}."
        ),
        "refusal_other": (
            "⛔ Richiesta bloccata dalla policy aziendale: {reason}. Per dubbi: {contact}."
        ),
        "instruction": (
            "Rispondi ripetendo esattamente il testo tra virgolette, senza aggiungere altro e "
            'senza usare strumenti: "{refusal}"'
        ),
        "marker": "[Messaggio rimosso dalla policy aziendale]",
        "tool_notice": (
            "[Output rimosso dalla policy aziendale. Rispondi ripetendo esattamente il testo tra "
            'virgolette, senza aggiungere altro e senza usare strumenti: "{refusal}"]'
        ),
        "bridge": (
            "[Contenuto bloccato dalla policy aziendale: include {reason}. Di' alla persona che il "
            "file è stato bloccato e perché.]"
        ),
        "format_unknown": (
            "⛔ Formato non riconosciuto dalla policy aziendale: la richiesta non è stata "
            "inviata. Aggiorna l'app o contatta {contact}."
        ),
        "org_refused": (
            "⛔ Account non aziendale: questa app usa un account che non appartiene "
            "all'organizzazione. Accedi con l'account di lavoro. Per dubbi: {contact}."
        ),
        "unidentified": (
            "⛔ La policy aziendale non riconosce ancora questo account: riavvia l'app (o esci "
            "e rientra). Se continua, contatta {contact}."
        ),
        "too_large": (
            "⛔ Richiesta troppo grande per la policy aziendale (oltre {mb} MB): inviane meno "
            "alla volta."
        ),
        "contact": "l'IT",
        "reason.input.secrets": "un segreto o una credenziale",
        "reason.pii.model": "dati personali",
        "reason.scope": "un argomento che la policy non consente",
        "reason.input.size": "troppo testo in una volta",
        "reason.gateway.model": "il modello {model} non è consentito",
        "reason.gateway.provider": "questo provider non è abilitato",
        "reason.gateway.protection": (
            "il profilo di protezione dei dati non consente questo provider"
        ),
        "reason.abuse.rate": "troppe richieste in un minuto, attendi un momento",
        "reason.gateway.cap": "hai esaurito il tuo tetto di token",
    },
}
CONTENT_CONTROLS = frozenset({"input.secrets", "pii.model", "scope", "input.size"})
"""Controls about what a request holds (the ``refusal`` text); the others use ``refusal_other``."""


class Texts:
    """The texts of one gateway (or of the deployment, without one)."""

    def __init__(self, gw: Any = None, *, locale: str = "en", contact: str = "") -> None:
        """Pick the language and the overrides.

        Args:
            gw: The gateway row, if known.
            locale: The language without a gateway.
            contact: ``EVOKAI_PROXY_CONTACT`` (a gateway's own ``contact`` setting wins).
        """
        lang = locale_of(gw) if gw is not None else locale
        lang = (lang or "en")[:2]
        self.lang = lang if lang in TEXTS else "en"
        settings = (getattr(gw, "gateway", None) or {}) if gw is not None else {}
        own = settings.get("proxy_messages")
        self._own: dict[str, str] = own if isinstance(own, dict) else {}
        self.contact = str(settings.get("contact") or contact or self.get("contact"))

    def get(self, key: str, **params: Any) -> str:
        """A text with its placeholders filled.

        Returns:
            The text.
        """
        text = self._own.get(key) or TEXTS[self.lang].get(key) or TEXTS["en"].get(key, key)
        for name, value in params.items():
            text = text.replace("{" + name + "}", str(value))
        return text

    def refusal(self, control: str, model: str = "") -> str:
        """The refusal of a control.

        Returns:
            The text.
        """
        reason = self.get(f"reason.{control}", model=model or "?")
        if control in CONTENT_CONTROLS:
            return self.get("refusal", reason=reason, contact=self.contact)
        return self.get("refusal_other", reason=reason, contact=self.contact)

    def instruction(self, refusal: str) -> str:
        """The prompt that makes the real model repeat a refusal (rewrite adapters).

        Returns:
            The text.
        """
        return self.get("instruction", refusal=refusal)
