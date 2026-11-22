# Templates

Evokai has one template syntax: [Handlebars](https://handlebarsjs.com/guide/), rendered with
[pydantic-handlebars](https://pypi.org/project/pydantic-handlebars/) (the engine of pydantic-ai's `TemplateStr`). It is
used by:

- an agent's `instructions`, and the application prompt an agent references with `prompt:`;
- [prompt modules](prompt-modules.md), which add parameters and a data table;
- an agent's `confirm_labels`, the text of the card that asks a person to confirm a change.

Nothing is HTML-escaped: a prompt is not a page, so `{{x}}` and `{{{x}}}` print the same value as it is.

## Instructions and prompts

```yaml
instructions: |
  You are the assistant of {{app}}. Today is {{date}}.
  Reply with JSON such as {"ok": true}.
```

- `{{date}}` is today in the application's time zone (`defaults.timezone`), `{{app}}` the application's name.
- Single braces are ordinary text, so JSON examples stay as written.
- A backslash before double braces writes them as they are: `\{{name}}` prints `{{name}}`.
- `{{#if}}`, `{{#unless}}` and the other block helpers work as in Handlebars.

When the text is saved it is checked: a template that does not parse, or reads anything but `date` and `app`, is refused
with 400 `prompt_template` (a module is checked against its own parameters, 400 `module_template`).

## Confirm labels

```yaml
confirm_labels:
  return_close: "Close the return {{returnId}}"
  order_refund:
    - 'Refund {{count orderIds}} orders as {{map reason "reasons"}}'
    - 'Refund order {{orderId}} as {{map reason "reasons"}}'
    - "Refund order {{orderId}}"
confirm_values:
  reasons: {damaged: "Damaged in transit", wrong_item: "Wrong item sent"}
```

- `{{field}}` prints an argument of the tool call; a list prints its items joined with commas, and a dotted path
  (`{{customer.name}}`) reaches inside an object.
- `{{count field}}` is the length of a list (1 for a single value).
- `{{map field "table"}}` passes the value through a table of `confirm_values`, item by item for a list; a value the
  table does not have is printed as it is.
- A tool may have several templates: the **first one whose fields are all present and not empty** wins (an empty text
  or list counts as missing), so put the most specific one first. Without a complete template the card names the tool,
  in the application's language.

When the agent is saved each template is checked: it must parse, call only `count`, `map` and the block helpers, and
`map` must name a table of `confirm_values` in quotes; otherwise 400 `label_template`.

Workflow approvals still write their `label` with the older grammar (`{field}`, `{field|count}`, `{field|map:table}`,
[workflows](workflows.md#approvals)).

## Versions saved before Handlebars

Every agent version and prompt version records its syntax (`template_syntax`). The versions saved before Handlebars are
`legacy` and keep rendering exactly as they did, so a conversation pinned to one is unchanged:

- in legacy instructions `{date}` and `{app}` are replaced, `{{` and `}}` become single braces and every other brace
  stays as written;
- legacy labels use `{field}`, `{field|count}` and `{field|map:table}`.

The console marks them **Legacy syntax** («Sintassi vecchia») in the agent editor and on the Prompts page. They are
never converted automatically: what is saved now is Handlebars. A save whose text still has an old placeholder is
refused with 400 `legacy_syntax`, and the message names the placeholder and what to write instead, for example
`{date} is the old placeholder syntax, use {{date}} (Handlebars)`. A JSON example in the text is not an old placeholder.

What to write instead:

- `{date}` and `{app}` become `{{date}}` and `{{app}}`;
- `{{` and `}}` meant as literal braces become `{` and `}` (or `\{{` for double braces);
- in a label, `{field}` becomes `{{field}}`, `{field|count}` becomes `{{count field}}` and `{field|map:table}`
  becomes `{{map field "table"}}`.

Two saves keep a legacy agent as it is: restoring a stored version from its history, and saving it again with
`template_syntax: legacy` while its instructions, prompt reference and labels are unchanged (another model, other
settings), which is what the console and `POST /v1/admin/agents/{name}/switch-model` do. The application YAML is always
Handlebars: a file with old placeholders, or with `template_syntax: legacy`, stops the start-up with a message that
names the file and the agent or prompt.

`GET /v1/admin/agents` returns each agent's `template_syntax`, `GET /v1/admin/prompts` each version's
`templateSyntax`.
