# `openapi`: REST APIs

Point it at an OpenAPI 3 document and pick operations: each becomes a tool whose arguments are the operation's path,
query and header parameters plus, when it takes one, a JSON `body` (local `$ref`s are resolved).

```yaml
mcp_servers:
  - name: orders
    connector: openapi
    config:
      spec_url: https://api.shop.example/openapi.json      # or spec_file: /run/evokai/orders.yaml, or spec: {...}
      base_url: https://api.shop.example/v1                # default: the document's first server
      operations: [listOrders, getOrder, addNote]
      allowed_hosts: [api.shop.example]                    # default: the base URL's host
      auth: {header: Authorization, prefix: "Bearer ", secret_env: ORDERS_API_TOKEN}
      # or: auth: {query: api_key, secret_env: ORDERS_API_KEY}
      headers: {X-Client: evokai}                          # fixed extra headers, never secrets
      writes: true                                         # needed for addNote (a POST)
```

- **GET and HEAD read.** Any other method changes data: it is offered only with `writes: true`, and every call waits
  for the user's confirmation.
- **Hosts.** Calls go only to `allowed_hosts`; redirects are never followed; path parameters are URL-encoded, so a
  value cannot climb out of its path segment.
- **Credential.** Sent as a header (with `prefix`) or a query parameter, from the environment; the parameter the
  document declares for it is hidden from the model.
- The answer reaches the model as `{status, body}` (JSON when the API answers JSON), clipped to `max_result_chars`.
