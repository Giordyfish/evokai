# Client tools

An agent can call tools that your application runs itself, in the browser or on a device: turn on a kiosk light,
open a dialog of your own, read a value only the page knows, print a receipt. Evokai sends the call to the client as
an event, and for a tool that waits it holds the turn until the client answers.

Evokai's own client tools (navigation, charts, diagrams, questions, confirmations, memory) keep working as before;
this page is about the tools you declare.

A chart's values are numbers the model writes. An agent whose figures must be exactly the data's sets
`client_tools.chart_from_tools: true`: every value of a chart (a KPI's delta aside) must be a number one of the
application's tools returned in the same turn, rounded at most as the chart writes it. Otherwise the chart is not shown
and the model is told to read the data again. Chart units are `eur`, `pct`, `count` (the default) and `ms`, a duration
the UI writes in milliseconds below a second and in seconds above (850 ms, 4.3 s).

## Declaring a tool

A tool has a name, a description for the model, a JSON Schema of its arguments and a `wait` flag:

| Field | Rule |
|---|---|
| `name` | `^[a-z][a-z0-9_]{1,47}$`, not the name of a built-in client tool (`navigate`, `chart`, `diagram`, `ask`, `confirm`, `memory`) nor `session`. The model sees it as `client_<name>`, a namespace of its own beside MCP tools. |
| `description` | What the tool does, for the model (at most 1000 characters). |
| `parameters` | The JSON Schema of the arguments, an object schema (`type: object`), checked as a schema (at most 16 KB). |
| `wait` | `false` (the default): Evokai sends the event and tells the model at once that the call was received. `true`: the turn waits for the client's result, up to `EVOKAI_CLIENT_TOOL_TIMEOUT_SECONDS` (120 s by default); then the model reads that no answer came. |

At most 32 tools per session, the agent's and the session's together. Tools can be declared in three places:

- **in the agent definition**, for every session of the agent (the console's agent editor has an editor for them):

  ```yaml
  client_tools:
    custom:
      - name: kiosk_light
        description: Turn the kiosk light on or off.
        parameters:
          type: object
          properties: {on: {type: boolean}}
          required: [on]
  ```

- **per session**, when your backend mints the session token (`clientTools`, kept with the conversation because it
  is too big for the token; a later mint that sends the list replaces it, `[]` clears it):

  ```json
  {"subject": "u-42", "agent": "assistant",
   "clientTools": [{"name": "open_cart", "description": "Open the shopping cart.", "wait": true,
                    "parameters": {"type": "object", "properties": {}}}]}
  ```

- **per server run** (`clientTools` in `POST /v1/agents/{name}/run`). Nobody can answer in the middle of a run, so a
  run accepts only tools that do not wait (400 `client_tool_wait` otherwise); the calls come back in the response's
  `clientToolCalls` (`callId`, `name`, `args`), for your backend to carry out.

## Grants

Client tools are the grant kind `client`. A tool an agent declares is on by default; a tool a session token or a
server run declares needs the grant `client:session`, which is off by default (403 `client_not_granted`). A name or a
pattern (`client:kiosk_*`) turns tools off (or on) for the application: an exact name wins over a pattern. The
console shows them on the Permissions page under "Your client tools".

## The event and the answer

When the model calls a tool, the session's stream carries a `client_tool` event:

```json
{"type": "client_tool", "callId": "toolu_01", "name": "open_cart", "args": {}, "wait": true}
```

For a tool that waits, answer it with `POST /v1/session/tool-results` and the session token:

```json
{"callId": "toolu_01", "result": {"opened": true}}
```

or, when the client could not do it, `{"callId": "toolu_01", "error": "The cart is empty."}`. A call id belongs to
its session and turn and answers once (404 `unknown_call` afterwards, from another session, or after the timeout). The
wait lives in the task that runs the turn, like a turn's cancellation: keep a browser session on one task (sticky
sessions, see [deployment](deployment.md)).

The call is kept with the answer as it was sent (name, `callId`, `args`), and for a tool that waits with how it ended:
`"status": "done"` when the client answered, `"error"` when it sent an error or never answered. A reloaded conversation
shows its card in the same place ([what a reload shows](architecture.md#what-a-reload-shows)); the result the client
sent is not kept with it.

With the TypeScript client, `handleTool` does all of it:

```ts
session.handleTool('open_cart', async () => {
  await cart.open()
  return { opened: true } // posted as the result; a thrown error is posted as the error
})
```

`<evokai-chat>` takes the same handlers as a property: `chat.tools = { open_cart: () => cart.open() }`. With the
Python client: `session.on_tool("open_cart", fn)`, or `session.tool_result(call_id, result)` yourself.

## Drawing tools with your own UI

A page can draw any tool's activity itself, not only its client tools: `session.renderTool(name, render)` in the
TypeScript client calls `render({name, status, callId?, args?, result?, error?})` when the tool starts and ends, and
`<evokai-chat>` takes `toolRenderers` (`{name: (part) => HTMLElement | null}`) to show that element in the chat instead
of the default line (`null` keeps it; `ui_chart` and `ui_diagram` replace the built-in visuals). A part carries only
what the session's events already carry: a server or MCP tool's name and status (never its output, and its
arguments only for an agent with [`show_tool_args`](streaming.md)), a client tool's arguments and the result your
own handler returned, a chart or a diagram. Nothing more leaves the service. `evokai types` gives the
[TypeScript types](streaming.md#typescript-types-of-an-agents-tools) of each tool's arguments and results.

## Security

- **Arguments** come from the model: the guard checks them against the tool's schema (`tools.args`), with the tool
  policy, egress, loop and rate controls, before the event leaves. A blocked call never reaches the client.
- **Results** come from the browser, so the person controls them. They are capped at 32 KB (413 `too_large`),
  scanned for instructions (`untrusted.injection`) and marked as data for the model, like a tool's output: they never
  become instructions, and their [markup](markup.md) is made inert.
- Every call and every answer is in the turn's trace (`execute_tool client_<name>`, then
  `client_tool_result client_<name>` with how long the client took and the outcome).
- A step that also asks for a confirmation does not wait for its client calls: the model reads that they did not
  run, and can call them again after the confirmation.

## Not carried

Custom client tools are offered only where a client receives their events: the session's own stream
(`/v1/session/chat`, `/v1/session/regenerate`) and server runs (`/v1/agents/{name}/run`, tools that do not wait).
The [OpenAI-compatible API](openai-compatible.md), [AG-UI](ag-ui.md), A2A, workflows and the messaging channels do
not offer them. A turn asked for as JSON (`Accept: application/json`) sees its events only at the end, so it offers
only the tools that do not wait.
