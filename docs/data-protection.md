# Providers and data protection

Evokai runs agents on any model provider pydantic-ai supports. Which ones an application may use is a question of
law as much as of taste: where the data is processed, which company (and which country's authorities) controls the
service, what the provider keeps. This page describes the **provider catalog**, the facts Evokai knows about every
provider and every other external processor, with the official page each fact was read from.

## The provider catalog

`src/evokai/llm/providers.yaml` (code: `evokai.llm.providers`) is versioned with Evokai. Per provider it states:

| Fact | Field | Values |
|---|---|---|
| Who operates the service | `entity.name`, `entity.country`, `entity.eu_contracting_entity` | the company, its country, who a customer in the EU contracts with |
| Who controls it | `entity.controlled_from` | an ISO country code, or `EU` for a group owned across member states |
| Training on API data | `terms.training` | `false`, `true`, or unknown |
| What is kept | `terms.retention`, `terms.retention_days` | `none`, `abuse_monitoring`, `stored`; days |
| Zero data retention | `terms.zero_retention` | `default`, `on_request`, `no` |
| Prompt caching | `caching.style` | `explicit`, `automatic`, `implicit_and_explicit`, `none` |

and per **endpoint** (a Bedrock inference profile, an Azure deployment type, a Vertex location, a regional base URL):
the model names it serves (`models`, globs over `provider:model`), the models the provider lists there (`offered`),
where processing happens (`place`: a geography, ISO countries, provider regions, or the deployment's own AWS region)
and how Evokai connects (`api`, `base_url`, `api_key_env`, `api_version`, `location`, `project`, `region`).

**Every fact carries its sources and the date it was read** (`sources`, `checked`). A fact nobody could verify on an
official page is `null` and listed in the provider's `unknowns`; it is never guessed, and an unknown fact never
satisfies a data protection rule. Evokai's own list prices (`prices`) are in the same file; they never reach a
browser.

`processors` describes what Evokai calls besides chat models: guard classifiers (Bedrock Guardrails, Azure Prompt
Shields and Content Safety, Google Model Armor, OpenAI and Mistral moderation, the local Prompt Guard, a self-hosted
Llama Guard), the document readers of scanned PDFs (Azure Document Intelligence, a self-hosted docling-serve), the
AgentCore code sandbox, server speech, the OAuth connections and the observability exporter.

### Coverage

| Provider | Model prefix | Endpoints | Jurisdiction |
|---|---|---|---|
| Amazon Bedrock | `bedrock:` | `eu.`, `us.`, `apac.`, `global.` profiles; in-region ids in `EVOKAI_AWS_REGION` | US |
| Azure OpenAI / AI Foundry | `azure:` | Global, Data Zone EU, Data Zone US, Regional (declared per deployment) | US |
| Claude in Microsoft Foundry | `anthropic-foundry:` | Hosted on Azure (Global, Data Zone US), Hosted on Anthropic infrastructure (declared per deployment) | US |
| Google Vertex AI | `google-vertex:` | `eu` and `us` multi-regions, EU regional endpoints, global (declared per deployment) | US |
| Google Gemini API | `google-gla:` | global | US |
| OpenAI API | `openai:`, `openai-eu:` | global; European data residency (`eu.api.openai.com`, eligible projects) | US |
| Anthropic API | `anthropic:` | global (`inference_geo` offers `us` and `global` only) | US |
| Mistral AI | `mistral:`, `mistral-eu:`, `mistral-us:` | global (data hosted in the EEA by default), EU, US | EU (FR) |
| Cohere | `cohere:` | not documented | CA |
| Groq | `groq:` | global | US |
| DeepSeek | `deepseek:` | China | CN |
| OpenRouter | `openrouter:` | global; EU routing (`eu.openrouter.ai`, EU/EEA plus UK and CH) | US |
| Hugging Face | `huggingface:` | Inference Providers (third parties); dedicated endpoints by region | US |
| Scaleway Generative APIs | `scaleway:` | Paris | EU (FR) |
| OVHcloud AI Endpoints | `ovhcloud:` | "hosted and run in the European Union" | EU (FR) |
| IONOS AI Model Hub | `ionos:` | Germany (`de-txl`) | EU (DE) |
| STACKIT AI Model Serving | `stackit:` | `eu01` | EU (DE) |
| Aleph Alpha | none | no public API documented (sign-up only) | EU (DE), pending a combination with Cohere |
| Ollama, vLLM, LM Studio | `ollama:`, `vllm:`, `lmstudio:` | self-hosted: what the operator declares | declared |

`GET /v1/admin/providers` returns the catalog (without prices) with its coverage: how many facts it states and how
many it leaves unknown. The console's **Providers** page shows it with every source.

### A deployment's own entries

`EVOKAI_PROVIDERS_FILE` names a YAML file of the same shape, merged over the built-in catalog by `id`: a provider's
fields replace the built-in ones, an endpoint with a known `id` replaces it, a new endpoint is tried before the
built-in ones. An endpoint can copy the facts of a built-in one with `like` and state only what differs:

```yaml
providers:
  - id: azure
    title: Azure OpenAI
    endpoints:
      - id: ours-sweden
        like: azure-datazone-eu         # the EU Data Zone's facts and sources
        models: ["azure:*"]             # azure:<deployment name>
        api: azure
        base_url: https://ours.openai.azure.com
        api_version: "2024-10-21"
        api_key_env: AZURE_OPENAI_API_KEY
        place: {geography: eu, countries: [SE], regions: [swedencentral]}
        declared_by: operator
  - id: google-vertex
    title: Google Vertex AI
    endpoints:
      - id: ours-eu
        like: vertex-eu-multiregion
        models: ["google-vertex:*"]
        api: google-vertex              # needs pydantic-ai-slim[google]
        location: eu
        project: my-project
  - id: our-vllm                        # a new provider: also a model prefix (our-vllm:<model>)
    title: Our vLLM in Milan
    self_hosted: true                   # no per-token charge
    entity: {name: ACME S.p.A., country: IT, controlled_from: IT}
    terms: {training: false, retention: none, zero_retention: default}
    endpoints:
      - id: milan
        models: ["our-vllm:*"]
        api: openai-compatible
        base_url: http://vllm.internal:8000/v1
        api_key_env: OUR_VLLM_KEY
        place: {geography: eu, countries: [IT]}
        declared_by: operator
```

A contract that changes a fact (an approved zero data retention, an Azure modified abuse monitoring) is declared the
same way, on the endpoint (`terms: {retention: none}`, `declared_by: operator`). The model still has to be in
`EVOKAI_MODEL_ALLOW`.

### How models are built

`evokai.llm.catalog.ModelCatalog` builds a model from its endpoint's connection when it has one: `openai-compatible`
(Scaleway, IONOS, STACKIT, OpenAI's EU residency, self-hosted servers), `azure` (a dated resource URL with its
`api_version`, or the `/openai/v1` URL without), `anthropic-foundry` (Claude in Microsoft Foundry), `google-vertex`,
`mistral` (Mistral's EU and US endpoints), `anthropic`, `bedrock` (with the endpoint's `region`); otherwise Azure from
its standard variables, Bedrock in the deployment's region, or pydantic-ai's `infer_model` (OVHcloud, Ollama,
Groq...). An Azure endpoint without a key uses a Microsoft Entra ID token of the managed identity ([Azure](azure.md)).
The API key comes from the environment variable the endpoint names (`SCALEWAY_API_KEY`, `IONOS_API_KEY`, `STACKIT_API_KEY`, `MISTRAL_API_KEY`, `OPENAI_API_KEY`); a
missing key or base URL is a configuration error, never a silent fallback.

## The data protection profile

An application's `data_protection` (code: `evokai.protection`) says which external processors its data may reach.
Three independent dimensions, each optional; the ones that are set combine, and all must hold:

| Dimension | Values | Holds when |
|---|---|---|
| `residency` | `geographies` (`eu`, `us`, `apac`, `global`...) and/or `countries` (ISO codes) | the endpoint's geography is listed, or every country it may process in is |
| `jurisdiction` | `eu` and/or ISO country codes | the provider is controlled from the EU or EEA (`eu`) or from a listed country |
| `retention` | `any`, `no_training`, `zero_retention` | the API terms say inputs are not used for training (and, for `zero_retention`, nothing is kept past the request) |

Beside the three dimensions, `provider_tool_storage: true` lets [provider built-in tools](builtin-tools.md) that keep
data at the model's provider (`file_search` over the provider's vector stores, `memory`) run. It is off unless set,
and a profile with `retention: zero_retention` cannot set it.

**An unknown fact never satisfies a rule.** A model whose provider's jurisdiction the catalog cannot show is excluded
by a jurisdiction rule with the reason `unknown`; a self-hosted server satisfies the rules its operator declares in
`EVOKAI_PROVIDERS_FILE`. Something that runs inside Evokai (a test model, the local Prompt Guard) sends nothing out and
satisfies every rule.

Example: EU residency, EU jurisdiction and zero data retention.

```yaml
application:
  data_protection:
    residency: {geographies: [eu]}
    jurisdiction: [eu]
    retention: zero_retention
```

With the built-in catalog this allows Scaleway, IONOS, STACKIT, OVHcloud and Mistral's EU endpoint for residency and
jurisdiction, excludes Bedrock, Azure, Vertex, OpenAI and Anthropic (US-controlled), and then keeps only the providers
whose terms state that nothing is kept and nothing is trained on (Scaleway, for one; Mistral keeps 30 days for abuse
monitoring). A deployment whose contract changes a fact declares it on its endpoint (`terms: {retention: none}`,
`declared_by: operator`).

A profile that sets `sovereignty` is refused (at bootstrap and by `PATCH /v1/admin/application`): that dimension was
removed before release. A stored profile that still holds it is read without it.

### Where it applies

| Processor | Checked |
|---|---|
| Chat models | at agent save (400 `residency`), token mint for a requested model (403 `model_residency`), every call and fallback (a model outside is never tried) |
| Embeddings | when a knowledge base chooses its model, and for memories |
| Guard classifiers | before a text is sent: an excluded classifier is skipped and the turn records `classifier_unavailable` with the reason |
| Code sandbox (AgentCore) | `code_run` and `skill_run` are not offered when AgentCore in its region is excluded |
| Server speech (STT, TTS) | at the voice offer (403 `data_protection`) |
| Connections (Microsoft 365, Google Workspace, GitHub, Linear, Notion, Slack) | when a person starts one (403 `data_protection`) |
| Observability exporter | deployment-wide: reported, not enforced per application |
| Browser voices and recognition | the application's choice: declared in `browser` (`online_voices`, `online_recognition`, `vendors`) and reported |

The older `residency` declaration (`model_regions`, `models`) keeps working beside the profile; both must hold. Its
`model_regions` now reads geographies from the provider catalog, so an EU provider passes `model_regions: [eu]` too.

`GET /v1/admin/data-protection` returns the profile and every processor with its facts, whether it is allowed and
why not (`reasons`: dimension and `outside`, `unknown`, `training` or `retained`). The console shows it on the Privacy
page; the GDPR pack (section 2) states the profile and each processor's residency, jurisdiction and terms as known.

### The system policy's profile

The deployment's [system policy](security.md#the-system-policy) may set a profile too. It holds **as well as** each
application's: a processor must meet both, and a reason the system profile gives carries `source: system` in
`GET /v1/admin/data-protection` (which also returns the system profile as `systemProfile`). Unlike an application's,
an unset `provider_tool_storage` in the system profile constrains nothing; `provider_tool_storage: false` or
`retention: zero_retention` there keeps provider built-in tools from storing data for every application. The system
profile has no `browser` declaration: what a browser sends is each application's choice.

## Regulatory spaces

The profile is written with the European regulatory space in mind (the GDPR's transfers and processors, a provider
controlled from the EU or EEA, an EU contracting entity), but its dimensions are not EU-specific: `residency` and
`jurisdiction` take any geography or country, and `retention` is about API terms anywhere. Another regulatory space
adds what it needs the same way (a fact on catalog entries, stated only where an official source says so, and a rule
in the profile), without touching the other dimensions.
