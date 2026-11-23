# Provider built-in tools

Some model providers run tools themselves: web search, web fetch, code execution, image generation, file search over
their own vector stores, MCP servers they call, and a memory. Evokai offers them through pydantic-ai's native tools,
with its grants, data protection, guard and traces around them.

## Turning one on

An agent lists them in `builtin_tools`, each with pydantic-ai's settings of that tool (never a secret: an MCP
server's `authorization_token` or `headers` are refused):

```yaml
builtin_tools:
  - kind: web_search
    config: {max_uses: 3}
  - kind: code_execution
```

| `kind` | What the provider does | Keeps data at the provider |
|---|---|---|
| `web_search` | searches the web | no |
| `web_fetch` | reads a web page | no |
| `code_execution` | runs code in its own sandbox | no |
| `image_generation` | draws an image | no |
| `file_search` | searches the files of its vector stores (`file_store_ids`) | **yes** |
| `mcp_server` | calls an MCP server (`id`, `url`, `allowed_tools`, required) | no |
| `memory` | keeps notes across requests | **yes** |

Each tool is the grant `builtin:<kind>`, off by default: the console's Permissions page ("Provider built-in tools") or
the YAML's `grants: {builtin_tools: {web_search: true}}`. An agent that lists a tool its application is not granted
cannot be saved (400 `not_granted`), and the console's agent editor shows the tools under the agent's tools. The model
must be one whose provider offers the tool: pydantic-ai refuses the call otherwise.

## Data protection

Evokai never lets a provider keep a conversation. `file_search` with the provider's vector stores and `memory` keep
data at the provider, so they are refused unless the application's [data protection](data-protection.md) profile sets
`provider_tool_storage: true` (an agent that lists one cannot be saved: 400 `provider_storage`; a profile with
`retention: zero_retention` cannot set it). The console's Protection page lists every agent's built-in tools and
whether each may run.

## Security policy

The provider runs these tools, so no call can wait for a confirmation. The tool policy and the ask rules apply under
the names `builtin:<kind>` and `<kind>`: a tool denied, outside `tools.allow`, confirmed (`tools.confirm`) or matched by
an [ask rule](security.md#ask-rules) is not offered, never run unconfirmed.

An `mcp_server` must list the tools the provider may call, in `allowed_tools` (`[crm_find_customer]`); one without
it cannot be saved, and one stored before is left out. **Every listed tool runs without a confirmation**, in every
channel: list only tools the model may call on its own. A listed tool the policy denies or confirms, or an ask rule
matches, is taken off the list. Tools that must wait for the person belong in Evokai's own `mcp_servers`.

## Guard, events and traces

- **Egress:** while `tools.egress` is enforced with an allow-list, `web_search` and `web_fetch` are limited to it: an
  agent's own `allowed_domains` can narrow it, never widen it (a tool left with no domain is not offered, and an empty
  allow-list allows none). An `mcp_server` outside an allow-list is left out, in monitor too. Without an allow-list
  they reach any address, like every other tool: the Protection page says so.
- **Regenerating:** an answer that called `mcp_server`, `memory` or `code_execution` (they can change data) is not
  regenerable.
- **Output:** what the tools return reaches the person only through the model's answer, which the output guard
  filters as always.
- **Events:** each call is a `tool` event (`start`, then `done` or `error`, with `builtin: true`), and each web page a
  result names, when the link policy (`output.links`) lets its address reach the browser, is a `citation` event (`index`, `title`, `url`, `source`), numbered with the turn's other citations.
- **Traces:** a `builtin_tool <kind>` event per result, with its outcome and how many sources it named; the turn's root
  lists the agent's built-in tools.

Server runs and the OpenAI-compatible API offer the granted tools too; their results show in the answer only.
