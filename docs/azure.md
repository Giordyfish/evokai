# Running on Azure

Evokai runs on Azure with what it uses on AWS, and every Azure piece has a self-hosted alternative. Azure services are
reached without keys: a **Microsoft Entra ID** token of the app's **managed identity** (`azure.identity`'s
`DefaultAzureCredential`, code: `evokai.core.azure`) whenever no key is configured. The Azure pieces are an optional
extra, never part of the base install:

```bash
uv sync --extra azure        # azure-identity and the anthropic SDK (Claude in Microsoft Foundry)
```

The image installs it in both flavours.

!!! warning "Not verified against a live Azure account"
    Every Azure piece on this page is built on Microsoft's official SDKs and REST references (cited below) and
    tested against recorded or faked HTTP shaped from those references. None has been run against a live Azure
    account yet: treat each as unverified until the owner records a real run.

## What runs where

| Piece | On AWS | On Azure | Self-hosted |
|---|---|---|---|
| Chat models | Amazon Bedrock | Azure OpenAI, the Foundry models sold by Azure (`azure:`), Claude in Microsoft Foundry (`anthropic-foundry:`) | Ollama, vLLM, LM Studio, any OpenAI-compatible server |
| Embeddings | Titan Text Embeddings v2 on Bedrock | Azure OpenAI embeddings (`azure:`) | any OpenAI-compatible server (Ollama, vLLM, Text Embeddings Inference) |
| Injection and moderation classifiers | Bedrock Guardrails | Content Safety Prompt Shields and text analysis | Llama Prompt Guard 2 (in process), Llama Guard |
| Credentials | the task role (the default AWS chain) | the managed identity (`DefaultAzureCredential`) | keys in the environment |

## Models

### Azure OpenAI and the Foundry models sold by Azure

An endpoint of the [provider catalog](data-protection.md#a-deployments-own-entries) with `api: azure` connects
`azure:<deployment>`. Two kinds of base URL:

- a **v1** base URL, `https://<resource>.openai.azure.com/openai/v1` or
  `https://<resource>.services.ai.azure.com/openai/v1`, with no `api_version`. A Foundry resource serves on it the
  models Azure sells besides OpenAI's (DeepSeek, Llama, Mistral, Grok...), each under its deployment name;
- a **dated** resource URL, `https://<resource>.openai.azure.com`, with its `api_version` (`2024-10-21`).

The key comes from the variable `api_key_env` names, else `AZURE_OPENAI_API_KEY`; with neither, Evokai sends an Entra
ID token for `https://cognitiveservices.azure.com/.default`. The identity needs the **Cognitive Services OpenAI User**
role on the resource.

```yaml
providers:
  - id: azure
    title: Azure OpenAI
    endpoints:
      - id: ours-sweden
        like: azure-datazone-eu          # the EU Data Zone's facts and sources
        models: ["azure:*"]
        api: azure
        base_url: https://ours.openai.azure.com/openai/v1
        place: {geography: eu, countries: [SE], regions: [swedencentral]}
        declared_by: operator
```

Without a declared endpoint, `azure:<deployment>` uses `AZURE_OPENAI_ENDPOINT` (and `OPENAI_API_VERSION` for a dated
one), with the key or Entra ID the same way.

Sources: [Azure OpenAI API versions](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/api-version-lifecycle),
[managed identity for Azure OpenAI](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/managed-identity).

### Claude in Microsoft Foundry

`anthropic-foundry:<deployment>` calls the Messages API of a Foundry resource,
`https://<resource>.services.ai.azure.com/anthropic`, through the anthropic SDK's `AsyncAnthropicFoundry`: the
resource key (`api_key_env`, sent as `x-api-key`) or an Entra ID token for `https://ai.azure.com/.default` (the
**Cognitive Services User** role). It is a Claude model like any other: Anthropic's prompt caching, context windows
and suitability profiles apply.

```yaml
providers:
  - id: anthropic-foundry
    title: Claude in our Foundry resource
    endpoints:
      - id: ours
        like: foundry-claude-azure-global      # or foundry-claude-azure-datazone-us
        models: ["anthropic-foundry:*"]
        api: anthropic-foundry
        base_url: https://ours.services.ai.azure.com/anthropic
        declared_by: operator
```

Without a declared endpoint, `ANTHROPIC_FOUNDRY_BASE_URL` or `ANTHROPIC_FOUNDRY_RESOURCE` and, optionally,
`ANTHROPIC_FOUNDRY_API_KEY`.

What the catalog says about it (read on Microsoft's pages on 26 November 2026): Anthropic, PBC is the seller, the
operator and an independent data processor on both hosting options, under Anthropic's Commercial Terms and Data
Processing Addendum. **Hosted on Azure** keeps prompts and outputs on Azure (Global Standard, or the US Data Zone);
**Hosted on Anthropic infrastructure** may process them outside Azure and outside the chosen geography. There is no
EU data zone for Claude, so a profile that keeps data in the EU, or under EU control, excludes it.

Sources: [deploy and use Claude in Foundry](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-claude),
[Claude models in Foundry](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/claude-models),
[hosting options](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/claude-models-hosting-comparison),
[data, privacy and security](https://learn.microsoft.com/en-us/azure/foundry/responsible-ai/claude-models/data-privacy).

## Embeddings

`azure:<embedding deployment>` (for example `text-embedding-3-large`) on an `api: azure` endpoint, keyless the same
way; self-hosted servers through `api: openai-compatible` ([knowledge bases](knowledge.md#embeddings)).

## Guard classifiers

`azure_prompt_shields` and `azure_content_safety` take `EVOKAI_AZURE_CONTENT_SAFETY_ENDPOINT`; without
`EVOKAI_AZURE_CONTENT_SAFETY_KEY` they send an Entra ID token (the **Cognitive Services User** role on the Content
Safety resource), fetched once at start ([security](security.md#injection-classifiers)).
Source: [authentication to Azure AI services](https://learn.microsoft.com/en-us/azure/ai-services/authentication).

## The managed identity

`DefaultAzureCredential` tries, in order, environment credentials, workload identity, the managed identity, then
developer sign-ins (Azure CLI...). In Azure Container Apps give the app a managed identity; for a user-assigned one set
`AZURE_CLIENT_ID` to its client id. One credential serves the process and refreshes its tokens.
Source: [DefaultAzureCredential](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.aio.defaultazurecredential).
