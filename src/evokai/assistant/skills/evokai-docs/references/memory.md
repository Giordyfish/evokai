# Long-term memory

What an agent remembers about a person across conversations, with the person's consent, and how the person sees,
changes and deletes it. Code: `src/evokai/memory/` (`service`, `consolidate`, `recall`, `store`).

## What is remembered

| Kind | What | Written by | Recalled |
|---|---|---|---|
| `preference` | how the person likes answers («prefers rounded amounts») | the agent, `memory_remember` (`kind: preference`) | always (up to 12, newest first) |
| `semantic` | facts about the person's work context («looks after the poetry shelf») | the agent, `memory_remember` (`kind: semantic`) | when relevant to the message |
| `episodic` | a one or two sentence summary of a finished conversation | Evokai, for agents with `memory: {episodes: true}` | by the same agent, when relevant |

Never customer data: the tool's description says so, and the guard's `memory.write` control (monitor by default,
`enforce` to refuse) keeps instructions, secrets and identifiers (tax codes, IBANs, e-mails, phones...) out of every
write, the agent's, the person's and the episodes'. An episode is also masked before it is stored.

Memories exist only with Evokai storage: an application that keeps its conversations (`external`) gets no memory
tools and no recall.

## Consent

Per application, `memory_mode`:

| Mode | Agents remember and recall |
|---|---|
| `off` | never (no tools, no recall; the chat panel hides its memory view) |
| `ask` | only for people who said yes: asked once, by the application at token mint (`memoryConsent`) or by the chat panel |
| `on` (default) | for everyone, except people who said no |

The person's answer is stored per subject (`evokai_memory_consents`: granted, source `token`, `person` or `server`,
time); the latest answer counts. A **no, or withdrawing a yes, deletes everything remembered** about the person.

- At token mint: `POST /v1/session-tokens {..., memoryConsent: true|false}`; the answer carries `memory: {mode,
  consent, active, ask}`.
- From the chat panel: `POST /v1/session/memory-consent {granted}` (the `<evokai-chat>` panel asks once when `ask`).
- From the application's backend: `POST /v1/memories/consent {subject, granted}` (audited).

## Writing: consolidation

Every write is compared with the person's active memories (preferences and facts together, an agent's episodes
apart), deterministically (`evokai.memory.consolidate`), by meaning (cosine of the embeddings) and by words (overlap of
the content words, lightly stemmed):

- **duplicate** (the same thing again, or one adding detail to the other): nothing new is stored; the existing memory
  keeps the more informative wording and its expiry is renewed;
- **supersede** (the same topic with a changed detail, a negation that flips, a number that changed): the new memory
  is stored and the old one gets `superseded_by`; it is never recalled and is purged 30 days later. The agent can
  also say it explicitly (`memory_remember {fact, replaces: <id>}`);
- **new**: anything else.

## Recall

Once per turn, before the first model call (`evokai.memory.recall`):

- every preference;
- the semantic facts and the agent's episodes ranked by `relevance x (0.35 + 0.65 x decay)`: relevance is the cosine
  with the person's message (Titan v2 embeddings; their words without them), decay halves every 120 days for facts
  and 30 days for episodes since the memory was written or confirmed; below a relevance floor a memory is left out;
- the agent's `memory.recall` best ones (6 by default); near-duplicates are skipped;
- when there are no more than that many, all of them are recalled and no query embedding is made (no cost).

Recalled memories reach the model **spotlighted as untrusted data** (`<<untrusted:... source=memory>>`, after the
prompt cache point), with their ids so the agent can forget or replace one. Each recall updates `last_used_at` and
`uses`. The turn's trace has a `memory recall` span (how many, which kinds; never the text).

## Embeddings

The knowledge path: `EVOKAI_MEMORY_EMBEDDING_MODEL` (default `bedrock:amazon.titan-embed-text-v2:0`, 512 dimensions,
`EVOKAI_MEMORY_EMBEDDING_DIMENSIONS`) within `EVOKAI_EMBEDDING_ALLOW` and the application's data residency, in the
deployment's region. Every call is a usage record of kind `embed` tagged `evokai:memory`, counted in the caps.
Without an allowed model, or when Bedrock fails, memories are compared and recalled by their words.

## Episodes

An agent opts in with `memory: {episodes: true}`. A conversation with no new message for
`EVOKAI_MEMORY_EPISODE_IDLE_MINUTES` (30) is finished: the housekeeping job summarises it (at most 20 per pass,
conversations of the last 7 days, once each) with `EVOKAI_MEMORY_SUMMARY_MODEL` (default: the agent's model, within
the allow-lists and the residency), when the person's memory is active, the conversation has at least two messages of
the person and the caps allow it. The summary call is a usage record (kind `llm`, tag `evokai:memory`) of the person.

When the person [rewrites their last question](architecture.md#rewriting-the-last-question), memory keeps nothing of
the replaced turn: the conversation's episode is deleted (it is written again once the conversation is finished), and
so are the memories its agents wrote since the replaced question (an older memory one of them had superseded comes
back). A memory the agent only merged with an existing near-duplicate keeps the merged text.

## The person's view

The session API, with the session token (the chat panel's «What {title} remembers» / «Cosa ricordo di te»):

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/session/memories` | `memory` (mode, consent, active, ask) and `items` (id, text, kind, agent, source, dates, uses) |
| PATCH | `/v1/session/memories/{id}` | rewrite one (`memory.write` applies: 400 `memory_refused`); its source becomes `person` |
| DELETE | `/v1/session/memories/{id}`, `/v1/session/memories` | delete one, or everything |
| POST | `/v1/session/memory-consent` | `{granted}`; a no deletes everything |

The application's backend: `GET /v1/memories?subject=`, `DELETE /v1/memories?subject=&id=`,
`POST /v1/memories/consent`.

## The harness `memory` capability

The pydantic-ai-harness `memory` capability ([capabilities](capabilities.md#stores)) is a separate store, not
Evokai's memories: its tools (`write_memory`, `read_memory`, `search_memory`, `delete_memory`) read and edit whole
files (a `MEMORY.md` and topic files) with versions and compare-and-set, while Evokai's memories are single facts with
a kind, an embedding, consolidation (duplicate, supersede) and ranked recall. Mapping files onto facts would break
either the tools' versions or the consolidation, so each keeps its own model and both keep the same rules:

- only while the person's memory is active (Evokai storage, `memory_mode`, the person's consent);
- every write passes `memory.write` (no instructions, secrets or identifiers);
- `memory_ttl_days` expires a file after it was written; a withdrawn consent, the person's «forget everything» and
  an erasure delete the files too; the export lists them (`capabilityRecords`).

The files are kept per person and agent in `evokai_capability_records` (capability `memory`).

## Retention and privacy

`memory_ttl_days` (application) expires a memory that many days after it was written or confirmed (none by default);
the hourly purge deletes expired memories and those superseded more than 30 days ago. `POST /v1/privacy/export`
includes every memory (superseded ones too, with `supersededBy`) and the consent record; `/erase` deletes both. See
[privacy](privacy.md).

## Settings

| Where | Setting | Default |
|---|---|---|
| application (`PATCH /v1/admin/application`, YAML `memory: {mode, ttl_days}`, console Privacy page) | `memory_mode`, `memory_ttl_days` | `on`, kept |
| agent | `memory: {episodes, recall}` | no episodes, 6 |
| grants | client tool `memory` | granted |
| environment | `EVOKAI_MEMORY_EMBEDDING_MODEL`, `EVOKAI_MEMORY_EMBEDDING_DIMENSIONS`, `EVOKAI_MEMORY_SUMMARY_MODEL`, `EVOKAI_MEMORY_EPISODE_IDLE_MINUTES` | Titan v2, 512, the agent's model, 30 |
