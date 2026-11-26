# Running on Azure

Evokai runs on Azure with what it uses on AWS, and every Azure piece has a self-hosted alternative. Azure services are
reached without keys: a **Microsoft Entra ID** token of the app's **managed identity** (`azure.identity`'s
`DefaultAzureCredential`, code: `evokai.core.azure`) whenever no key is configured. The Azure pieces are an optional
extra, never part of the base install:

```bash
uv sync --extra azure        # azure-identity, the anthropic SDK (Claude in Foundry), the Azure Monitor exporter
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
| Vector search | pgvector on PostgreSQL, or the portable index | pgvector on Azure Database for PostgreSQL (allow the `vector` extension) | pgvector, or the portable index |
| Scanned documents (OCR) | none (Textract is not in `eu-north-1`) | Document Intelligence `prebuilt-layout` | docling-serve |
| Reranking | none | Cohere Rerank sold by Azure in Foundry | vLLM, Infinity (Cohere v2 rerank API) |
| Files (attachments, knowledge documents) | in the database | in the database | in the database |
| Injection and moderation classifiers | Bedrock Guardrails | Content Safety Prompt Shields and text analysis | Llama Prompt Guard 2 (in process), Llama Guard |
| Code execution | AgentCore Code Interpreter | Container Apps dynamic sessions | none, on purpose |
| Personal data | in process | in process | in process |
| Traces and metrics | OTLP (a collector) | Azure Monitor Application Insights | an OpenTelemetry Collector |
| Logs | CloudWatch (stdout) | Log Analytics (stdout) | stdout |
| Console sign-in | any OpenID Connect provider | Microsoft Entra ID | Keycloak, Authentik, any OpenID Connect provider |
| Secrets | Secrets Manager / SSM | Key Vault references | environment, or `EVOKAI_STATE_DIR` |
| Database | RDS for PostgreSQL | Azure Database for PostgreSQL | PostgreSQL |
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

## Knowledge bases

- **Vector search** needs no Azure AI Search: Azure Database for PostgreSQL flexible server runs pgvector once
  `vector` is in the server parameter `azure.extensions`; Evokai then uses it as on any Postgres.
- **Scanned PDFs**: `EVOKAI_DOCUMENT_READER=azure` with `EVOKAI_DOCUMENT_INTELLIGENCE_ENDPOINT` (keyless: the
  **Cognitive Services User** role on the resource), or `docling` for a docling-serve of your own
  ([formats](knowledge.md#formats)).
- **Reranking**: `EVOKAI_RERANK_MODEL` on a catalog endpoint ([reranking](knowledge.md#reranking)).
- **Files** never go to Blob Storage: attachments and knowledge documents are kept in the database, encrypted at rest
  with it, so there is nothing to configure.

Sources: [Analyze Document](https://learn.microsoft.com/en-us/rest/api/aiservices/document-models/analyze-document?view=rest-aiservices-v4.0%20(2024-11-30)),
[Document Intelligence data, privacy and security](https://learn.microsoft.com/en-us/azure/foundry/responsible-ai/document-intelligence/data-privacy-security),
[pgvector on Azure Database for PostgreSQL](https://learn.microsoft.com/en-us/azure/postgresql/extensions/how-to-use-pgvector),
[docling-serve usage](https://github.com/docling-project/docling-serve/blob/main/docs/usage.md).

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

## Code execution

`EVOKAI_CODE_SANDBOX=azure` with `EVOKAI_CODE_SESSION_POOL_ENDPOINT` runs `code_run` and `skill_run` in an Azure
Container Apps dynamic sessions pool instead of AgentCore: a new session per call, deleted afterwards, a European
region only, the **Azure ContainerApps Session Executor** role for the managed identity
([code execution](code.md#azure-container-apps-sessions)). There is no self-hosted sandbox on purpose: running
model-written code next to Evokai is not made safe by Evokai.

## Monitoring

Evokai logs JSON lines on stdout: Container Apps sends them to the environment's Log Analytics workspace with nothing
to configure. For traces and metrics:

- `EVOKAI_OTEL_ENABLED=true` and `EVOKAI_OTEL_EXPORTER=azure-monitor`: Microsoft's Azure Monitor OpenTelemetry
  exporter (in the `azure` extra) sends them to the Application Insights resource of
  `APPLICATIONINSIGHTS_CONNECTION_STRING`. For Entra ID instead of the instrumentation key, set
  `APPLICATIONINSIGHTS_AUTHENTICATION_STRING=Authorization=AAD` (system-assigned identity) or
  `Authorization=AAD;ClientId=<client id>` (user-assigned), give the identity **Monitoring Metrics Publisher** on the
  resource, and disable local authentication there. The data protection profile judges the processor
  `azure-monitor` in the region of the connection string's `IngestionEndpoint`.
- Self-hosted: the default OTLP exporter to an OpenTelemetry Collector of your own (`OTEL_EXPORTER_OTLP_ENDPOINT`).

What a trace carries is the same either way: subjects as keyed hashes, content only as `tracing_content` allows
([traces](configuration.md#traces-and-opentelemetry)).
Sources: [the exporter](https://learn.microsoft.com/en-us/python/api/overview/azure/monitor-opentelemetry-exporter-readme),
[Entra ID for Application Insights](https://learn.microsoft.com/en-us/azure/azure-monitor/app/azure-ad-authentication).

## Console sign-in with Microsoft Entra ID

The console's single sign-on is plain OpenID Connect ([console sign-in](configuration.md#single-sign-on)); Entra ID
needs no code of its own, only these settings:

- `issuer`: `https://login.microsoftonline.com/<tenant id>/v2.0` (a single tenant: the `common` and `organizations`
  endpoints publish an issuer with a `{tenantid}` placeholder, which never matches);
- register a **Web** platform redirect URI `{EVOKAI_PUBLIC_URL}/v1/console/sso/callback` on the app registration, and
  a client secret (`EVOKAI_SSO_CLIENT_SECRET`, from Key Vault);
- the e-mail comes from `email` when the app registration adds it as an optional claim, else from
  `preferred_username` (the UPN);
- roles: `role_claim: "groups"` with the group object ids (the app registration emits security groups; past 200 groups
  Entra sends an overage marker instead and no rule matches), or `role_claim: "roles"` with app roles assigned to
  users and groups, which avoids the overage;
- leave `require_mfa` off: Entra's ID tokens carry no `amr` claim, so every sign-in would be refused. Enforce MFA with
  a Conditional Access policy on the app instead.

Admin keys and the operator key keep working, so a wrong setting never locks the console.
Source: [ID token claims reference](https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference).
Any other OpenID Connect provider (Keycloak, Authentik, Okta...) is the self-hosted alternative.

## Not built, on purpose

- **Azure AI Search**: pgvector on Azure Database for PostgreSQL gives the same retrieval as on AWS; a search index
  outside the database would be a second store to keep consistent.
- **Blob Storage**: files live in the database ([attachments](attachments.md), [knowledge](knowledge.md)).
- **Language PII / Presidio**: the guard's personal data detection runs in process (checksummed identifiers, e-mail,
  phone, cards); named entities such as person names and addresses are not detected by either AWS or Azure today.
- **Entra ID for PostgreSQL and Redis**: Evokai connects to both with a password from Key Vault.

## Deploying on Azure Container Apps

The AWS deployment runs one Evokai task behind the application, Postgres for state, an optional Redis or Valkey and the
keys from a secret store ([deployment](deployment.md)). The same on Azure:

| AWS | Azure |
|---|---|
| ECS service, task role | Container App with a user-assigned managed identity |
| Secrets Manager / SSM parameters | Key Vault, referenced by Container Apps secrets |
| RDS for PostgreSQL | Azure Database for PostgreSQL flexible server |
| ElastiCache for Valkey | Azure Managed Redis (`rediss://`) |
| CloudWatch Logs | Log Analytics workspace of the Container Apps environment |
| Bedrock, Guardrails, AgentCore | Azure OpenAI / Foundry, Content Safety, Container Apps sessions |

### 1. Identity and roles

Create a user-assigned managed identity and give it, on each resource it uses:

| Resource | Role |
|---|---|
| Key Vault | Key Vault Secrets User |
| Azure OpenAI / Foundry resource (models, embeddings, Claude, rerank) | Cognitive Services OpenAI User (Azure OpenAI), Cognitive Services User (Claude, Foundry models) |
| Content Safety, Document Intelligence | Cognitive Services User |
| Container Apps session pool | Azure ContainerApps Session Executor |
| Application Insights | Monitoring Metrics Publisher |
| Container registry | AcrPull |

Set `AZURE_CLIENT_ID` to the identity's client id, so `DefaultAzureCredential` picks it.

### 2. Secrets in Key Vault

Store `EVOKAI_SIGNING_KEY`, `EVOKAI_ENCRYPTION_KEY`, `EVOKAI_OPERATOR_KEY` (`evokai keygen signing|encryption`, a
random operator key), the database URL with the runtime role's password and, if used, `EVOKAI_REDIS_URL` and
`EVOKAI_SSO_CLIENT_SECRET`. Reference them from the app and map them to environment variables:

```bash
ID=/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.ManagedIdentity/userAssignedIdentities/evokai
KV=https://<vault>.vault.azure.net/secrets
az containerapp create -g <rg> -n evokai --environment <env> \
  --image <registry>/evokai:<tag> --registry-server <registry> --registry-identity $ID \
  --user-assigned $ID --ingress internal --target-port 8787 \
  --secrets "signing=keyvaultref:$KV/evokai-signing,identityref:$ID" \
            "encryption=keyvaultref:$KV/evokai-encryption,identityref:$ID" \
            "operator=keyvaultref:$KV/evokai-operator,identityref:$ID" \
            "database=keyvaultref:$KV/evokai-database-url,identityref:$ID" \
  --env-vars EVOKAI_SIGNING_KEY=secretref:signing EVOKAI_ENCRYPTION_KEY=secretref:encryption \
             EVOKAI_OPERATOR_KEY=secretref:operator EVOKAI_DATABASE_URL=secretref:database \
             AZURE_CLIENT_ID=<identity client id> EVOKAI_BASE_PATH=/evokai \
             EVOKAI_MODEL_ALLOW='["azure:*","anthropic-foundry:*"]' \
             EVOKAI_PROVIDERS_FILE=/app/config/providers.yaml
```

A secret URI without a version follows the latest one: Container Apps picks it up within 30 minutes and restarts the
revisions that use it.
Source: [manage secrets](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets).

### 3. The database

Azure Database for PostgreSQL flexible server, in the same region:

- allow the `vector` extension (server parameter `azure.extensions`) for pgvector;
- run `evokai db-setup` once with the owner's URL, as a Container Apps job with the same image: it creates the runtime
  role (`EVOKAI_APP_DB_PASSWORD`), the schema and the migrations; the app then runs as that role. Later releases run
  `evokai migrate` the same way, or set `EVOKAI_AUTO_MIGRATE=true`;
- TLS: `?sslmode=verify-full` with `EVOKAI_DB_CA_FILE` pointing to a bundle of the two root certificates Microsoft
  lists today (DigiCert Global Root G2 and Microsoft RSA Root CA 2017, never an intermediate one,
  [TLS](https://learn.microsoft.com/en-us/azure/postgresql/security/security-tls)), mounted or baked into your own
  image layer.

### 4. Models, data protection and the rest

Declare the Azure resources in `EVOKAI_PROVIDERS_FILE` with their real region (`place`) and `declared_by: operator`,
as in [Models](#models), so the data protection profile sees where each one runs. Then, as needed:
`EVOKAI_EMBEDDING_ALLOW` and `EVOKAI_EMBEDDING_MODEL` (`azure:<deployment>`), `EVOKAI_AZURE_CONTENT_SAFETY_ENDPOINT`,
`EVOKAI_DOCUMENT_READER=azure` with its endpoint, `EVOKAI_RERANK_MODEL`, `EVOKAI_CODE_SANDBOX=azure` with the pool,
`EVOKAI_OTEL_ENABLED=true` with `EVOKAI_OTEL_EXPORTER=azure-monitor`, and `EVOKAI_REDIS_URL` when more than one
replica runs ([more than one instance](deployment.md#more-than-one-instance)).

### 5. Ingress and the console

Keep the ingress **internal** (the environment's virtual network): the application's backend mints session tokens with
its server key and its front end reaches Evokai through it, as on AWS. The console (`{EVOKAI_BASE_PATH}/admin/`) must
not be public: reach it through the virtual network (a jump host or a VPN), or turn it off with
`EVOKAI_ADMIN_UI_ENABLED=false`. The health probe is `{EVOKAI_BASE_PATH}/health` on port 8787.
