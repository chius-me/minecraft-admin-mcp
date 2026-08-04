# Codex

Add an HTTP MCP server to `~/.codex/config.toml` and keep the token in the environment:

```toml
[mcp_servers.mc_survival]
url = "https://mc-survival.example.com/mcp"
bearer_token_env_var = "MC_SURVIVAL_MCP_TOKEN"
```

