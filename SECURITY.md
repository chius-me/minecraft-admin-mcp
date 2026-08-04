# Security policy

## Reporting

Do not open a public issue for a suspected vulnerability that exposes credentials or enables command
execution. Contact the repository maintainers privately through GitHub's security advisory feature.
Include the affected revision, reproducible steps, impact, and any proposed mitigation.

## Supported boundary

`minecraft-admin-mcp` deliberately supports only named, validated Minecraft operations. Raw RCON,
shell, code execution, Docker access, arbitrary paths, restarts, restores, bans, and operator grants
are not V0.1 features. A change that bypasses these boundaries is a security defect.

Production operators should use unique high-entropy tokens per instance, keep RCON off host-facing
ports, restrict port 8101 with a firewall or private network, terminate HTTPS before the MCP server,
pin container versions, protect writable volumes, and review the SQLite audit log. Compromise of a
Minecraft plugin may still compromise that Minecraft server; deploy each stack with separate
networks, credentials, and volumes to contain that risk.

Log and world inputs remain untrusted even though they are read from fixed configured paths. Keep the
Minecraft mount read-only, keep each backup volume instance-local, and perform restores outside MCP
through a trusted, integrity-checked recovery procedure.
