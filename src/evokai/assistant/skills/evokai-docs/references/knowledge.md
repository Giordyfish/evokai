# Knowledge bases

An application's agents can answer from its own documents (regulations, manuals, policies, contracts) and cite them.
Evokai keeps the documents, cuts them into chunks that follow their structure, embeds them with a model that runs in
the deployment's region, and searches them by meaning and by words together. The agent gets a `knowledge_search`
tool (and, if asked, a search before every answer); the passages reach the model as untrusted data, spotlighted per
document; the answer cites them as `[1]`, `[2]` and the client receives a `citation` event for each number, with the
document's title, its page and section, a snippet and a link.

Code: `src/evokai/knowledge/` (`extract` formats, `chunking`, `embeddings`, `store` the indexes, `search` fusion and
access, `fetch` addresses, `service` ingestion and citations, `tool` the agent's tool), `src/evokai/api/knowledge_routes.py`.

## Model

| What | Table | Fields |
|---|---|---|
| Knowledge base | `evokai_knowledge_bases` | per application: `name`, `title`, `description` (the agent reads it), `embedding_model`, `dimensions`, `language` (word search), `chunking`, `retrieval`, `access` (groups), `link_template`, `sources` (addresses and connectors to sync), `retention_days` |
| Document | `evokai_knowledge_documents` | `source` (file name, address or `<connector>:<file id>`, unique in the base), `source_kind` (`upload`, `url`, `connector`), `title`, `mime`, `checksum` (SHA-256), `size`, `status` (`queued`, `ingesting`, `ready`, `failed` with a `reason`), `version`, `metadata` (the application's own fields, for filters), `access` (groups), `chunks`, `pages`, `tokens`, `modified` (a connector's change marker) |
| Chunk | `evokai_knowledge_chunks` | `text`, `kind` (`text` or `table`), `page` and `page_end`, `section` and `headings` (the heading path), `char_start` and `char_end` (in the document's text), `tokens`, `dims`, the embedding |
| File | `evokai_knowledge_files` | the original file (re-ingestion and signed links) |

On Postgres with the `vector` extension, migration 0016 adds to the chunks an `embedding_v` column (`vector`) with an
HNSW index per size (256, 512 and 1024 dimensions, cosine distance) and a `tsv` column (`tsvector` in the base's
language) with a GIN index (`evokai.knowledge.pgvector`). `evokai db-setup` installs the extension as the owner (on
Amazon RDS the owner has `rds_superuser`) and grants the runtime role `USAGE` on its schema. Anywhere else (SQLite in
tests, Postgres without pgvector, other databases) the same interface uses a portable index: embeddings as float32
bytes, cosine similarity by brute force and BM25 over the chunks, in Python. It is right for tests and small bases.

Documents and chunks belong to the application, never to a subject: erasing a person (`POST /v1/privacy/erase`) does
not touch them. Deleting a document deletes its chunks and its file; deleting a base deletes everything in it.

## Ingestion

```
upload / address / connector ─▶ document (queued) ─▶ worker: read ─▶ extract ─▶ chunk ─▶ embed ─▶ swap chunks (ready)
                                                                  └──────── any failure ────────▶ failed + reason
```

- **Uploads**: `POST /v1/admin/knowledge/{name}/documents?filename=…` with the file as the body (its type in
  `Content-Type`; `title`, `access` and `metadata` as query parameters), or the console. At most
  `EVOKAI_KNOWLEDGE_MAX_FILE_MB` (20) per file.
- **Addresses**: `POST /v1/admin/knowledge/{name}/urls` `{url, title?, access?, metadata?}`, or the base's `sources`
  synced on demand. Only `http(s)` addresses on a domain in `EVOKAI_KNOWLEDGE_FETCH_ALLOW` (a domain allows its
  subdomains; empty: off) are fetched. The host must resolve only to public addresses (loopback, private, link-local,
  shared, multicast and reserved addresses are refused, the cloud metadata service among them) and the request goes to
  the checked address itself with the host's TLS name, so a second DNS answer cannot redirect it. Redirects are followed
  by hand (three at most, each checked), the body is read up to the size cap, no credentials or cookies are sent.
- **Connectors**: a base's `sources` can name a Google Drive or SharePoint connector of the application
  (`{kind: connector, server: <its MCP server name>, access?}`). A sync lists every file in the connector's folders:
  new files and files whose modified time changed are queued, files gone from the folders are deleted, the others are
  left alone without downloading them.
- **Background work**: documents are ingested in the service's own process, `EVOKAI_KNOWLEDGE_WORKERS` (2) at a time.
  The status is durable: a process that stops leaves documents queued or ingesting, and the next one picks them up at
  start, from the kept file. New chunks replace the old ones in one transaction, so a document stays searchable with
  its previous version until the new one is ready.
- **Dedupe**: an upload with the same name and content is `unchanged` and not ingested again; the same content under
  another name is refused (409 `duplicate`, with the other document's id). Changing a base's embedding model, size,
  chunking or language ingests every document again.

### Formats

| Format | Read with | Structure kept |
|---|---|---|
| PDF | `pypdf`, the text layer | pages; headings recognised in the text (`TITOLO I`, `Capo II`, `Art. 3`, `1.2 Title`, short lines in capitals) |
| Word (`.docx`) | the XML of the file | `Heading 1..9` / `Titolo 1..9` styles, tables, rendered page breaks |
| PowerPoint (`.pptx`) | the XML of the file | one page per slide, the slide's title as heading, tables |
| HTML | the standard library parser | `h1`-`h6`, paragraphs, lists, tables; scripts, styles and navigation dropped |
| Markdown | Evokai | `#` headings, pipe tables, fenced code |
| Text | Evokai | headings recognised as in PDFs |
| CSV | the standard library | one table, its header repeated in every chunk |

There is no OCR: a scanned PDF without a text layer fails with the reason «the PDF has no text layer (a scan?): text
recognition (OCR) is not available». Amazon Textract is not available in `eu-north-1`; a local OCR engine (for example
Tesseract in the image) could be added later as one more extractor, without changing the rest of the pipeline.

### Chunking

Chunks follow the document: a heading closes the chunk before it and opens a section, and every chunk carries its
heading path (`Titolo II Standard specifici > Art. 2 Tempo di risposta`). Paragraphs are added up to `max_chars`
(1,500, about 350 tokens); a longer paragraph is cut between sentences. A table up to `table_max_chars` (3,000) is a
chunk of its own, kept whole; a longer one is cut between rows with its header repeated. Consecutive chunks of a
section overlap by `overlap_chars` (200, cut at a word). The text that is embedded starts with the document's title and
the heading path, so a chunk is found by what its section is about, not only by its own words.

## Embeddings

Amazon Titan Text Embeddings v2 (`bedrock:amazon.titan-embed-text-v2:0`) on Bedrock in the deployment's region
(`EVOKAI_AWS_REGION`, default `eu-north-1`), 256, 512 or 1024 dimensions, normalised. A base's model must match
`EVOKAI_EMBEDDING_ALLOW` and the application's data residency (`evokai.privacy.residency_allows`): an in-region
Bedrock id runs in the deployment's region, so an application whose models must stay in the EU can use it when the
deployment is in the EU, and a residency outside it refuses it (400 `embedding_model`). `test:hash` is a local model
for tests (hashed words, free).

Every embedding call is a usage record of kind `embed` with its input tokens and cost: ingestion under the subject
`system:knowledge` (tags `knowledge:<base>`), a search under the subject that asked (with its agent, conversation and
turn), the console's test search under `console:knowledge`. The application's caps count them; ingestion stops a
document with the reason «the application's spending cap is reached» when the cap would be passed. People see only
shares, never amounts.

## Retrieval

1. The documents the caller may read: the base's `access` and the document's `access` each match when empty or when
   they share a group with the caller; `filters` on the documents' `metadata` narrow them (a value, a list of values,
   or `{gte, lte}`).
2. Two searches over those documents' chunks: by meaning (cosine similarity of the question's embedding) and by words
   (Postgres full text in the base's language, or BM25), `candidates` (40) each.
3. Reciprocal rank fusion: a chunk scores the sum of `1 / (60 + rank)` over the lists it is in. A chunk found only by
   meaning must reach `min_score` (cosine similarity; 0 keeps them all).
4. Optional maximal marginal relevance (`mmr`, `mmr_lambda` 0.7): chunks are picked one by one, trading relevance for
   difference from the ones already picked, so near-duplicates give way; the picked ones are shown by relevance.
5. The best `top_k` (5); the agent can set its own `top_k`.

Settings per base (`retrieval`): `top_k`, `min_score`, `hybrid` (the word search on), `mmr`, `mmr_lambda`,
`candidates`. Each result carries a relevance from 0 to 1 (its fused score as a share of the best possible), its
cosine similarity and its rank in each search.

## Access

Documents and bases carry access groups. The application vouches for its users' groups:

- **Session tokens**: `POST /v1/session-tokens` takes `groups` (up to 50, the user's groups or roles, as the
  application knows them); they are signed into the token (`grp`) and every search of the session uses them.
- **Server runs**: `POST /v1/agents/{name}/run` takes `groups` for the subject of the run.
- **Console**: the test search sees every document, or searches as a session with the groups it is given.

A document without access groups is found by every session of the application (when its base allows them). Only
chunks of documents the caller may read are ever searched, so a restricted document never reaches the model, the
citations or the usage of a caller outside its groups.

## Agents

```yaml
agents:
  - name: esperto-norme
    knowledge:
      bases: [normativa]   # the application's bases this agent searches
      auto: false          # true: search with the user's message before the first model step
      top_k: 5             # optional, else each base's own setting
```

- The agent gets `knowledge_search(query, kb?, filters?)`, described with its bases' names and descriptions, and its
  system prompt tells it to cite every passage it uses as `[n]` and never to invent a number.
- With `auto: true` the message is searched before the first model step of each turn, kept in the conversation as a
  call to the same tool, so the model always reads the relevant chunks.
- Passages are numbered across a turn: the same chunk found twice keeps its number.
- **Untrusted content**: each passage travels in its own block and reaches the model spotlighted under the source
  `knowledge:<base>/<document id>`; it is scanned by `untrusted.injection` (rules and the policy's classifier) and the
  personal data rules (`pii.model`) apply, like any tool output ([security](security.md)).
- **Grants**: the `knowledge` grant turns the capability (`*`) or one base off per application. Without it the tool
  is not offered and nothing is searched; an agent definition that names a base that is not granted is refused
  (400 `not_granted`).
- Server runs get the tool too and return the citations they found in `citations`.
- Every search is a `retrieval` span of the turn's or run's trace (the bases, the number of results and documents;
  the query and the passages' sources only as the application's trace content allows).

### Citations

```json
{"type": "citation", "index": 1, "kb": "normativa", "documentId": "99d6…",
 "title": "Testo integrato della qualità commerciale (TIQV), sintesi",
 "location": {"page": 2, "section": "Titolo II Standard specifici > Art. 2 Tempo di risposta", "charStart": 770, "charEnd": 1130},
 "snippet": "Il venditore risponde in modo motivato a ogni reclamo scritto entro 30 giorni solari…",
 "url": "https://…", "turnId": "…", "seq": 7}
```

`url` is, in order: the base's `link_template` filled with `{documentId}`, `{source}`, `{title}`, `{page}`,
`{section}`, `{kb}` and `{meta.<field>}` (URL-encoded); the address of a document fetched by address; the connector's
own link of a connector file; else a signed link to the file Evokai keeps
(`{EVOKAI_PUBLIC_URL}/v1/knowledge/files/{id}?e=…&s=…`, an HMAC of the deployment's signing key, valid
`EVOKAI_KNOWLEDGE_LINK_TTL` seconds, 600 by default; PDFs open at the page). The event arrives before the text that
cites it and is stored with the conversation; a replayed signed link may have expired. Speech never reads the markers
aloud.

The TypeScript client keeps citations with their answer (`ChatMessage.citations`, also when a stored conversation is
reloaded); `<evokai-chat>` draws the markers as superscript links and lists the cited sources under the answer (parts
`citation`, `sources`, `source`).

## Console

The **Knowledge** page of an application (Italian: «Conoscenza»): its bases with their documents and chunks; per base
the settings (embedding model and size, word search language, chunking, retrieval, access groups, link template,
retention, sources), uploads with progress (several files, drag and drop), fetching an address, the documents with
their status and reason, re-ingesting, deleting, re-syncing the sources, and a test search that shows the ranked chunks
with their relevance, similarity, ranks and how an answer would cite them. The agent editor has a **Knowledge** section
(the bases, the search before answering, chunks per search); Permissions has the knowledge grants; the playground draws
the citations of an answer.

## Admin API

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/admin/knowledge` | the bases with their counts, and the embedding models, languages, domains and connectors available |
| GET, PUT, DELETE | `/v1/admin/knowledge/{name}` | a base with its documents; create or change it; delete it with everything in it |
| POST | `/v1/admin/knowledge/{name}/documents?filename=` | upload a document (the body is the file) |
| POST | `/v1/admin/knowledge/{name}/urls` | fetch a document by address |
| POST | `/v1/admin/knowledge/{name}/sync` | re-sync the sources: `{queued, unchanged, removed, failed}` |
| GET, PATCH, DELETE | `/v1/admin/knowledge/{name}/documents/{id}` | a document with its chunks; its title, access or metadata; delete it |
| POST | `/v1/admin/knowledge/{name}/documents/{id}/reingest` | ingest it again |
| POST | `/v1/admin/knowledge/{name}/search` | a test search `{query, groups?, filters?, topK?}` (a viewer may run it) |
| GET | `/v1/knowledge/files/{id}?e=&s=` | a document's file through a citation's signed link |

Every change is in the audit log. In an application YAML, `knowledge_bases:` declares bases (`name`, `title`,
`description`, `embedding_model`, `dimensions`, `language`, `chunking`, `retrieval`, `access`, `link_template`,
`sources`, `retention_days`); documents are added through the API or the console.

## Retention

A base's `retention_days` deletes documents not updated for that many days (with their chunks and files), in the hourly
purge and in `evokai purge`. Without it documents are kept until deleted.
