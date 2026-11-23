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
| `mcp_server` | calls an MCP server (`id`, `url`) | no |
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

## Guard, events and traces

- **Egress:** while `tools.egress` is enforced, its allow-list becomes the allowed domains of `web_search` and
  `web_fetch` (unless the agent sets `allowed_domains` itself), and an `mcp_server` outside it is left out.
- **Output:** what the tools return reaches the person only through the model's answer, which the output guard
  filters as always.
- **Events:** each call is a `tool` event (`start`, then `done` or `error`, with `builtin: true`), and each web page a
  result names is a `citation` event (`index`, `title`, `url`, `source`), numbered with the turn's other citations.
- **Traces:** a `builtin_tool <kind>` event per result, with its outcome and how many sources it named; the turn's root
  lists the agent's built-in tools.

Server runs and the OpenAI-compatible API offer the granted tools too; their results show in the answer only.
