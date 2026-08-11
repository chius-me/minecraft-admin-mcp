# Security policy

## Reporting

Do not open a public issue for a suspected vulnerability that exposes credentials or enables command
execution. Contact the repository maintainers privately through GitHub's security advisory feature.
Include the affected revision, reproducible steps, impact, and any proposed mitigation.

## Supported boundary

`minecraft-admin-mcp` deliberately supports only named, validated Minecraft operations. Raw RCON,
shell, code execution, Docker access, arbitrary paths, and operator grants are not supported. A
change that bypasses these boundaries is a security defect.

### V0.3 high-risk operations

Restart, ban, and restore may be enabled only as:

- `disabled` (not registered);
- `approval` (Agent may call `request_*` only; execution requires a separate admin token);
- `allow` (direct tool — recommended only on isolated test servers).

Agents **cannot self-approve**. The MCP bearer token is rejected by the approval API. There is no
approve/reject MCP tool. Operators use `MC_ADMIN_TOKEN` with `minecraft-admin-mcp-approve` or the
`ApprovalAdmin` entry point. Approval records expire and execute at most once.

Restore accepts only an opaque instance `backup_id` after SHA-256 verification — never caller-supplied
filesystem paths. Restart uses fixed RCON `stop` and never the Docker socket or API.

Production operators should use unique high-entropy tokens per instance (`MC_MCP_TOKEN` and
`MC_ADMIN_TOKEN` must differ), keep RCON off host-facing ports, restrict port 8101 with a firewall or
private network, terminate HTTPS before the MCP server, pin container versions, protect writable
volumes, and review the SQLite audit log. Compromise of a Minecraft plugin may still compromise that
Minecraft server; deploy each stack with separate networks, credentials, and volumes to contain that
risk.

Log and world inputs remain untrusted even though they are read from fixed configured paths. Keep the
Minecraft mount read-only where possible, keep each backup volume instance-local, and treat restore
execution as a high-risk, audited action.
