# Attachments

A person can attach files to a chat message: a photo of a damaged parcel, a PDF invoice, a CSV export, a Word
document. The model reads them natively when it can (images, PDF), and otherwise reads the text Evokai extracts from
them. Attachments are off until the application is granted them and the agent turns them on.

## Turning them on

Two switches, both off by default:

- **the grant** `attachments:*` (the console's Permissions page, "Attachments"; in the application YAML,
  `grants: {attachments: {"*": true}}`);
- **the agent's `attachments`**, in the agent editor ("Attachments", under Tools) or in the definition:

  ```yaml
  attachments:
    enabled: true
    types: [png, jpeg, webp, gif, pdf, docx, txt, md, csv]   # default: every kind below
    max_file_mb: 10            # largest file (1 to 50)
    max_files: 5               # files in one message (1 to 10)
    max_conversation_mb: 50    # everything kept for one conversation (1 to 1000, Evokai storage)
  ```

An agent with `attachments.enabled` cannot be saved without the grant (400 `not_granted`), and revoking the grant
turns attachments off for every agent of the application at once.

`GET /v1/session` says what the session takes, so a client knows whether to offer a paper clip:

```json
"attachments": {"kinds": ["png", "pdf"], "mediaTypes": ["image/png", "application/pdf"],
                "maxFileBytes": 10000000, "maxFiles": 5}
```

It is `null` when nothing can be attached (no grant, the agent's switch off, or no kind the models can read).

## Kinds

| Kind | Read by the model |
|---|---|
| `png`, `jpeg`, `webp`, `gif` | as the image itself (at most 3.75 MB, the smallest limit among the providers) |
| `pdf` | as the file itself when every model of the agent's chain reads PDF, the file is at most 4.5 MB and the message has no more than 5 such files; otherwise its text |
| `docx`, `pptx` | their text (paragraphs, headings, tables, slides) |
| `txt`, `md`, `csv` | their text |

The kind is read from the file's bytes. The name and the type the browser declares are never trusted (the name only
tells a Markdown or CSV text from plain text). SVG, HTML and XML are always refused, whatever their name, and so is
a text with markup a browser would run anywhere in its first 8 KB, and anything else Evokai does not read (400
`attachment_type`). A Word or PowerPoint file larger than 30 MB once unpacked is not read. A document's text is cut at 50,000 characters, with a note
for the model.

Before an image is stored or sent, its metadata goes: EXIF (with the GPS position and the camera), XMP, IPTC and
comments. The pixels are not decoded or re-encoded, so nothing else changes; an EXIF orientation is lost with the rest.

## Sending files

Files travel inline, in the body of `POST /v1/session/chat`, as base64:

```json
{"text": "Is this invoice right?",
 "attachments": [{"name": "invoice.pdf", "mediaType": "application/pdf", "data": "JVBERi0xLjcK..."}]}
```

This is the shape of a server run's attachments (`name`, `mediaType`) with the bytes instead of the text. Inline
files keep one request one turn: nothing is uploaded before the turn decides, there are no upload ids to steal or
orphaned uploads to clean up, and external storage needs nothing more. The body limit of `POST /v1/session/chat`
follows `EVOKAI_ATTACHMENTS_REQUEST_MB` (50 MB of files by default, plus a third for base64) only when the session
token's application is granted `attachments` and its agent has them on: this is checked from the token before the
body is read. Any other chat message, and the other session routes (except a recording to transcribe, see
[voice](voice.md)), keep the 2 MB limit or less (413 `too_large`, from
`Content-Length` or while a chunked body is read).

A message with attachments still needs its text (the question about them). A decision (`confirm`) carries none.

The clients:

```ts
await session.chat({ text: 'Is this invoice right?', files: [file] })   // File or Blob
```

```python
session.chat("Is this invoice right?", attachments=["invoice.pdf", ("note.txt", b"..."), Attachment("a.png", data)])
```

`<evokai-chat>` shows a paper clip when the session takes files, accepts files dropped on the composer or pasted, and
shows each file as a preview that can be removed before sending (see the UI components' README).

## Refusals

Checked when the turn opens, before anything is kept or a conversation starts:

| Status | Code | When |
|---|---|---|
| 403 | `attachments_not_granted` | the grant is off |
| 400 | `attachments_off` | the agent takes no attachments |
| 400 | `too_many_attachments` | more than `max_files` |
| 413 | `attachment_too_large` | a file over `max_file_mb`, or an image over 3.75 MB |
| 400 | `attachment_type` | a kind that is refused or that the agent does not take |
| 400 | `attachment_damaged` | an image whose structure cannot be read |
| 400 | `attachment_invalid` | not base64, or attachments with a decision |
| 413 | `attachments_full` | the conversation would keep more than `max_conversation_mb` |
| 400 | `attachment_unsupported` | an image, and a model of the chain does not read images |
| 400 | `attachment_unreadable` | a document without text (a scan) that the models cannot read as a file either |

Whether a model reads images or PDF comes from its pydantic-ai profile (`supports_image_input`,
`supports_document_input`), and the whole chain counts: a fallback model that cannot read an image would fail the turn.

## The guard

Anything read from a file is untrusted data, like the output of an MCP tool:

- a file's name is the person's input, like their message: it passes the same input checks (`input.injection`,
  `input.moderation`, `scope` and the others: a name they refuse refuses the turn), and on its way to the model its
  identifiers are protected (`pii.model`) and its markup made inert ([markup](markup.md)). The person still sees the
  name they gave;

- a document's text is scanned for hidden instructions (`untrusted.injection`: in enforce mode the lines that read as
  instructions are removed) and reaches the model marked as data (`untrusted.spotlight`, source
  `attachment:<name>`), with its markup made inert;
- a PDF the models would read as a file is scanned the same way through its text; when the scan removes anything,
  or when `pii.model` is enforced and the text holds identifiers, the model gets the cleaned and protected text
  instead of the file;
- the personal data controls apply as to the person's own words: `pii.model` protects identifiers in the text the
  model reads, `pii.history` masks them in what is stored;
- an image is sent with a line telling the model it is data, never instructions. Evokai has no text recognition: what
  is written inside an image is not scanned. An application that needs every word scanned leaves images out of
  `types`.

## History, storage and privacy

The question keeps its attachments (name, type, size, SHA-256 hash). `GET /v1/session/conversation` lists them on
each user message (`attachments: [{id, name, mediaType, size}]`), never their content. An image is shown through
`GET /v1/session/attachments/{id}` with the session token: only images, only of the session's own conversation (any
other id is 404), as they are kept, without metadata. Documents are never served back. The TypeScript client reads
an image with `session.attachment(id)`.

With **Evokai storage** the files are kept with the conversation (`evokai_attachments`) and follow it: its time to
live, deletion, the GDPR export (`POST /v1/privacy/export` lists them with their bytes in base64) and erasure. A later
turn sends the images and PDF files of the conversation again (at most 10 images and 5 PDF files, the latest first;
older ones become a note that they are no longer available), and the extracted text of documents is part of the
question. When an older question's documents no longer fit the history window (`EVOKAI_HISTORY_CHARS`), the question
stays and its documents become a note. Revoking the grant stops earlier files from being sent again. Rewriting the
last question, or regenerating its answer, keeps its attachments; a rewrite may add new ones (`max_files` counts both),
and a rewrite whose new question is refused deletes them with the question it replaced.

With **external storage** Evokai keeps no file beyond the turn: the new messages it returns carry each attachment's
metadata and, for a document, the text the model read; the application stores them like any message. In a later turn
the model reads that an earlier image or PDF is no longer available. `max_conversation_mb` does not apply, since
nothing is kept.

Traces keep each attachment's name, type, size, hash and whether the model read it as a file, never the content.

## Not yet

- The OpenAI-compatible API (`/v1/openai/chat/completions`) still takes text only: `image_url` and file content parts
  are refused with `400 unsupported_content`. Server runs take text attachments as before.
- No text recognition (OCR) in scans or images, and no resizing: a thumbnail is the stored image drawn small.
