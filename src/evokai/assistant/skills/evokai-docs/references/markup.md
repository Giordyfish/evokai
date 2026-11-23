# Inline markup

An agent can write two kinds of markup inside its answers, when your application declares them:

- **Cues** (`{wave}`, `{point:left}`) tell your page that something should happen at that point of the answer: a
  gesture of an animated character you draw, a highlight on the screen, a sound, a change of slide. They are never read
  aloud and never shown as subtitles; each one reaches you as a **mark** on the spoken segment, with the offset where it
  sat, so it can fire exactly while the voice says the words around it.
- **Speech tags** (`[laughs]`, `[whispers]`) make an expressive voice laugh, sigh or whisper. They reach the speech
  engines that perform them and are taken out everywhere else (subtitles, the other engines, the browser's voices).

Typical uses are subtitles and karaoke, expressive voices, cues for the user interface, kiosks and voice assistants.

## Declaring it

The application declares markup in its YAML (or with `PATCH /v1/admin/application`, `markup`; or on the console's
**Limits** page, under **Markup**):

```yaml
application:
  markup:
    cue:
      open: "{"          # the default delimiters
      close: "}"
      names: [wave, nod, point, smile]   # optional: other names are dropped
    speech:
      open: "["
      close: "]"
      names: [laughs, sighs, whispers]   # optional: other tags are removed
```

An agent may declare its own `markup:` in its definition; each kind it sets (`cue`, `speech`) replaces the
application's, and `enabled: false` turns a kind off for that agent. Tell the agent in its instructions which cues and
tags it may use and what they mean: Evokai never adds them to the prompt.

| Field | Default | Meaning |
|---|---|---|
| `cue.open`, `cue.close` | `{`, `}` | 1 to 3 ASCII punctuation characters each |
| `cue.names` | every name | the names delivered; a cue with another name is dropped and counted in the turn's trace (`markupDropped` on its speech span) |
| `cue.names_from` | none | `{module, param}`: a parameter of a session's [prompt module](prompt-modules.md) that lists the names (a multiple choice or a short text); with `names` too, it narrows them; when the session does not give it, `names` applies, and without `names` no cue is delivered |
| `speech.open`, `speech.close` | `[`, `]` | as for cues; they must differ from the cues' opening delimiter |
| `speech.names` | every tag | the tags kept for the voice |
| `enabled` | `true` | per kind |

A cue is `open name close` or `open name:argument close`: the name starts with an ASCII letter and has up to 40 ASCII
letters, digits, `_` or `-`; the argument has up to 80 characters. A speech tag is 2 to 40 letters, digits, spaces,
hyphens and apostrophes, starting with a letter; with square brackets, a Markdown link (`[text](url)`), a citation
(`[1]`) and a task list box (`[x]`) are never read as tags, but a reference-style link (`[text][ref]`) is, so avoid
those when speech tags use square brackets. An opening delimiter that cannot start a token (`[0, 1)`, `{ }` in code)
never holds the voice back.

## What the events carry

The model's own text is never changed: `text` events and the stored conversation keep the markup as written, so the
model sees its cues again on the next turn and a client can show them however it likes (`GET /v1/session` returns the
delimiters as `markup`, and the TypeScript client's `stripMarkup` takes them out of what a chat shows).

Each spoken segment (`say`, voice mode) is cleaned and carries the markup apart:

```json
{"type": "say", "index": 0, "text": "Hi! It is 1.000 points.", "spoken": "Hi! It is one thousand points.",
 "tagged": "Hi! [laughs] It is one thousand points.",
 "marks": [{"name": "wave", "at": 2, "spokenAt": 2}]}
```

- `text`: the subtitle, without any markup;
- `spoken`: what the voice says (numbers in words), present when it differs from `text`;
- `tagged`: `spoken` with its speech tags, always written `[words]` whatever the delimiters, present when there are
  tags; a server speech engine that performs tags speaks this form;
- `marks`: the cues, `name`, `arg` when given, `at` the character offset in `text`, `spokenAt` the offset in `spoken`
  when that differs.

A segment made only of cues hands them to the next segment (offset 0). Cues after the last sentence come in a final
`say` with an empty `text`: clients skip speaking it and only fire its marks. [Server speech](voice.md) turns
the offsets into times (`mark` events).

## People cannot write cues

Markup in a person's message is made inert before the model sees it (and before it is stored): the first character of
each token becomes its fullwidth form (`{wave}` becomes `｛wave}`), so the model never takes it for one of its own cues.
Text that only looks like markup by accident, such as JSON, is left alone. The conversation an application keeps
itself (external storage) is sent by the application and not rewritten.

## Guard

The `output.system_prompt` control reads the answer without its markup, so cues written between the words of the system
prompt cannot hide a leak.
