# Generic MCP client

- Transport: Streamable HTTP
- Endpoint: `https://mc-survival.example.com/mcp`
- Header: `Authorization: Bearer <instance-specific-token>`

Do not put the token in the URL query string. Use HTTPS or a trusted private overlay network outside
the local Docker host. Connect each server instance under a distinct client-side name.

