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

The provider runs these tools, so Evokai cannot hold one of their calls for the person's confirmation. The tool
policy and the ask rules therefore decide whether a tool is offered at all, under two names, `builtin:<kind>` and
`<kind>` (`builtin:web_search` and `web_search`):

- a tool denied under either name (`tools.deny`), or with an allow-list (`tools.allow`) that names neither, is not
  offered (while `tools.policy` is enforced);
- a tool the policy confirms (`tools.confirm`) or an [ask rule](security.md#ask-rules) may ask for is **refused**, never run
  without its confirmation: `tools.confirm: ["builtin:*"]` turns every built-in tool off;
- the same applies to each tool an `mcp_server` lists, under its own name.

### MCP servers the provider calls

An `mcp_server` must list the tools the provider may call on it, in `allowed_tools` (an agent without it cannot be
saved, and one stored before is left out of its turns):

```yaml
builtin_tools:
  - kind: mcp_server
    config:
      id: crm
      url: https://mcp.example.com/mcp
      allowed_tools: [crm_find_customer, crm_order_status]
```

**Every listed tool runs without a confirmation**, in browser turns, server runs and the OpenAI-compatible API alike,
because the provider calls it, not Evokai. List only tools you would let the model call on its own. A listed tool the
policy denies or confirms, or an ask rule may ask for, is taken off the list, and a server left with none is not
offered. For tools that change data and must wait for the person, connect the server to Evokai instead
(`mcp_servers`, see [configuration](configuration.md#bootstrap-from-a-mounted-config-file)): Evokai then asks before each change.

## Guard, events and traces

- **Egress:** while `tools.egress` is enforced with an allow-list, `web_search` and `web_fetch` are limited to it: an
  agent's own `allowed_domains` can narrow it, never widen it (a tool left with no domain is not offered, and an empty
  allow-list allows none). An `mcp_server` whose address is outside an allow-list is left out whenever one is set and
  `tools.egress` is not off (in monitor too: its calls cannot be checked one by one). Without an allow-list, as for
  every other tool, they may reach any address: the Protection page says so for each one.
- **Output:** what the tools return reaches the person only through the model's answer, which the output guard
  filters as always.
- **Events:** each call is a `tool` event (`start`, then `done` or `error`, with `builtin: true`), and each web page a
  result names, when the link policy (`output.links`) lets its address reach the browser, is a `citation` event (`index`, `title`, `url`, `source`), numbered with the turn's other citations.
- **Traces:** a `builtin_tool <kind>` event per result, with its outcome and how many sources it named; the turn's root
  lists the agent's built-in tools.

Server runs and the OpenAI-compatible API offer the granted tools too; their results show in the answer only.

## Regenerating

An answer that called a tool that can change data (`mcp_server`, `memory`, and `code_execution`, whose sandbox can
keep files between requests) cannot be [regenerated](architecture.md#regenerating-the-last-answer): its `done` event
and the conversation say `regenerable: false`, as after a custom client tool.
