# minecraft-admin-mcp

`minecraft-admin-mcp` is a lightweight, self-hosted MCP server that gives an MCP-compatible agent a
small set of administration tools for exactly one Minecraft Java Edition server. It uses standard
Streamable HTTP and has no dependency on a particular agent or client.

The current development branch implements the V0.2 scope from `PROJECT_SPEC.md`.

## Security model

This service is intentionally not a general remote administration interface:

- One MCP process is configured for one Minecraft server; tools never accept a `server_id`.
- It exposes no shell, code execution, arbitrary RCON, Docker API, or arbitrary filesystem tools.
- RCON operations are fixed adapter methods and all player names and text are validated.
- Disabled or approval-gated operations are absent from the V0.1 tool list. V0.1 does not execute
  approval-gated high-risk operations.
- Every write operation, including rejected attempts, is recorded in the instance-local SQLite
  audit database. Known secret fields are redacted.
- Each instance has its own bearer token and RCON password, read only from environment variables.
- The default container runs as non-root UID 1000 with a read-only root filesystem, all Linux
  capabilities dropped, and no Docker socket. UID 1000 matches the default owner used by the
  recommended Minecraft image so private world files remain readable through the read-only mount.

Player names, chat, server output, books, signs, and mod text remain untrusted input. Bearer tokens
protect access but do not encrypt traffic; use HTTPS at a reverse proxy, Tailscale, or another
trusted private network in production.

## Tools

| Tool | Operation | Default example permission |
|---|---|---|
| `get_identity` | Describe this one configured server | allow |
| `get_status` | Check RCON and online-player status | allow |
| `list_players` | List online players | allow |
| `get_whitelist` | List whitelisted players | allow |
| `broadcast` | Send a validated announcement | allow |
| `whitelist_add` | Add a validated player name | allow |
| `whitelist_remove` | Remove a validated player name | allow |
| `kick_player` | Kick a player with a validated reason | allow |
| `save_world` | Run the fixed `save-all flush` operation | allow |
| `get_metrics` | Return reliable visible metrics and null for unavailable values | allow |
| `get_recent_events` | Parse recent events from the configured fixed log | allow |
| `get_recent_errors` | Parse recent failures from the configured fixed log | allow |
| `create_backup` | Create a consistent Zstandard world backup | allow |
| `list_backups` | List backup ID, time, size, SHA-256, and reason | allow |

There is deliberately no raw command tool. Restart, ban, restore, OP, and arbitrary file access
remain outside V0.2.

## Docker Compose deployment

Requirements: Docker Engine with Compose v2 and enough memory for Minecraft.

```bash
cp .env.example .env
# Replace both values in .env with independent random secrets.
docker compose config
docker compose up -d --build
docker compose ps
```

The MCP endpoint is `http://127.0.0.1:8101/mcp`. Minecraft gameplay is published on port 25565. The
RCON port has no host mapping; the MCP container reaches it as `minecraft:25575` on
`minecraft_internal`.

The default stack mounts:

- `minecraft_data` at `/data` in Minecraft and read-only at `/minecraft` in MCP;
- `minecraft_backups` at writable `/backups` only in MCP;
- `minecraft_mcp_data` at `/var/lib/minecraft-admin-mcp` for the SQLite audit database;
- `config/survival.example.yaml` read-only as the MCP configuration.

Use a copied configuration file for a real deployment instead of editing the example. Host ports can
be changed with `MC_GAME_PORT` and `MC_MCP_PORT`. Pin the Minecraft image tag and server version
according to your own upgrade policy.

## Multiple isolated servers

Run a separate Compose project for each Minecraft server. Give each project a different config,
host port, project name, bearer token, and RCON password:

```bash
docker compose --project-name mc-survival --env-file .env.survival up -d
MC_MCP_PORT=8102 MC_ADMIN_CONFIG_FILE=./config/test.example.yaml \
  docker compose --project-name mc-test --env-file .env.test up -d
```

Do not attach the stacks to a shared network. Each MCP still uses the internal DNS name `minecraft`,
but Docker project scoping resolves it only inside its own network. Stopping one project does not
stop or address another project's services or volumes.

## Configuration

Set `MC_ADMIN_CONFIG` to a YAML file. `config/config.example.yaml` documents all V0.1 fields.
Secrets are references, never YAML values:

```yaml
rcon:
  host: minecraft
  password_env: MC_RCON_PASSWORD
http:
  path: /mcp
  token_env: MC_MCP_TOKEN
```

Permission values are `allow`, `approval`, or `disabled`:

- `allow`: register the directly executable tool;
- `approval`: do not register a direct tool in V0.1 (approval queues arrive in V0.3);
- `disabled`: do not register the tool.

The bearer token must be at least 32 characters. Send it only in the header:

```http
Authorization: Bearer <instance-specific-token>
```

Tokens in URL query parameters are not supported.

### Logs, metrics, and backups

The log tools only read `minecraft.log_file`; callers cannot provide a path. Recognized player chat
is returned with `trusted: false` and `source: player_chat`. All log-derived content is untrusted,
control characters are sanitized, and IP addresses are redacted by default.

The standard Compose deployment does not share the Minecraft PID namespace with MCP. CPU and memory
therefore return `null` instead of reporting the wrong process. Data-volume free space is reported;
TPS and MSPT remain `null` in V0.2.

Backups cover only configured `minecraft.world_directories`. Creation acquires an instance-wide
lock, disables saving, flushes the worlds, writes a `.tar.zst` archive, calculates SHA-256, writes
metadata, restores saving in a `finally` path, and enforces `retention_count`. Tool results never
expose a filesystem path. V0.2 intentionally provides no restore operation; copy and verify backups
through a trusted operator-controlled recovery process.

## MCP clients

Connection examples are in `examples/clients/` for Codex, Claude Desktop, Cursor, Hermes, and a
generic MCP client. All use:

- Transport: Streamable HTTP
- URL: `https://your-host.example/mcp`
- Authentication: `Authorization: Bearer ...`

Client configuration formats change over time; verify the example against the installed client
version. Client-specific behavior is never imported into the MCP server.

## Direct development

Python 3.12+ and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv sync --frozen
cp config/survival.example.yaml config/config.yaml
export MC_ADMIN_CONFIG=config/config.yaml
export MC_RCON_PASSWORD='development-rcon-secret'
export MC_MCP_TOKEN='development-token-at-least-32-characters'
uv run minecraft-admin-mcp
```

Quality gates:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

## Audit and errors

Writes are stored in `audit_events` with timestamp, server ID, tool, redacted arguments, outcome,
stable error code, and duration. The service never intentionally stores authorization headers,
tokens, or RCON passwords and masks internal exception details at the MCP boundary.

See `SECURITY.md` for reporting and production guidance and `CONTRIBUTING.md` for development rules.
