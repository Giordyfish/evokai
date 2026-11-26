# Features

Everything Evokai does today. The [README](../README.md) has the short list.

| | |
| --- | --- |
| 🧠 **Any model** | Bedrock, OpenAI, Azure, Mistral, Ollama and more through [pydantic-ai](https://ai.pydantic.dev), behind an allow-list, with an ordered **fallback chain** when a model has no capacity, and **prompt caching** per agent on Bedrock Anthropic models. |
| 🔧 **Your tools over MCP** | The app's MCP server, called **as the signed-in user** (`forward_user`) or with a service secret. Tools are filtered per user; a server that is down only loses its own tools. |
| 🧪 **Evals** | Test cases per agent (contains, patterns, JSON schemas, tools called, model-judged rubrics), run on demand and on every new version against the previous one, with their own budget. |
| 🔌 **Ready connectors** | SQL databases, REST APIs from their OpenAPI document, Google Drive, SharePoint and OneDrive, IMAP mailboxes: enabled by configuration, read-only unless you opt in, under the same grants. |
| ✋ **Confirm before change** | Tools that change data never run directly: the turn stops with a `confirm` event and resumes on the user's decision. Questions with options work the same way. |
| 📚 **Skills** | Folders with a `SKILL.md` and, beside it, references, data and scripts: only names and descriptions go in the prompt, the agent loads a skill when it needs it (or it is preloaded), reads its files one at a time and runs its scripts in the code sandbox when code execution is on. |
| 🎙️ **Voice** | Dictation, conversation and barge-in. Browser engines (on-device recognition, in-browser Piper or device voices) or server voice on [Pipecat](https://pipecat.ai) over WebRTC, with Silero VAD and Smart Turn. |
| 📊 **Rich answers** | Charts, schemas, navigation and questions as typed events, rendered by your own interface; opt in to show the model's reasoning and its tools' arguments as they are written, and generate [TypeScript types](streaming.md) of an agent's tools. |
| 💶 **Budgets** | Hard caps per application, defaults per user (month and day), enforced before every model call. People, in the app and in the console, only ever see **percentages** of the caps, never amounts. |
| 🗄️ **Your choice of storage** | Conversations stored in Evokai (any SQL database) or kept by the app and sent with each request. A time to live per application deletes them for good. |
| 🛡️ **Agent security layer** | Prompt-injection and jailbreak heuristics, untrusted tool output and e-mails marked as data, personal data (Italian formats included) masked or tokenised, secrets and unsafe links stopped in answers, a tool policy, abuse limits; each control off, monitored or enforced, per application and per agent, with security events. Mapped to the OWASP LLM Top 10. |
| 🇪🇺 **Privacy** | A person's data exported or erased on request, sign-out everywhere, retention per kind of data, and a data residency per application that keeps models in their geography. |
| 📜 **Compliance packs** | AI Act (classification, Annex IV-style documentation, changes), GDPR (record of processing, DPIA template, rights, DPA annex) and an ISO 27001 / SOC 2 mapping, generated from the live configuration in English and Italian, every statement with its source, snapshots and diffs. |
| 🧮 **Code execution** | Agents run Python for analysis and charts in Amazon Bedrock AgentCore Code Interpreter: one sandbox per call, no internet, EU region, output through the guard, off until granted. |
| 🔑 **Console access** | Single sign-on (OpenID Connect, PKCE, MFA from the provider) with viewer, operator and owner roles enforced on every route, and an audit log of every change. |
| 🔒 **Never stored at providers** | No provider-side threads, assistants or stored responses, ever. History is always sent from Evokai or the app. Enforced by a test. |
| 🔗 **Open standards** | [AG-UI](ag-ui.md) for CopilotKit frontends, an [OpenAI-compatible API](openai-compatible.md) for any OpenAI client or LangChain, and [A2A](a2a.md) agent cards for other agents, under the same keys, grants, guard and caps. |
| 🤖 **Back-office agents** | Agents that run server to server with a JSON output schema, validated, for automation such as e-mail triage. |
| 🪄 **Admin console** | Applications, agents with versions, per-application **capability grants**, MCP servers, skills, keys, limits, usage, a playground and health, at `{base}/admin/`. |
| 🔭 **Observable** | Structured logs with correlation ids and per-call provider metrics; OpenTelemetry traces, metrics and logs when you turn them on. |
