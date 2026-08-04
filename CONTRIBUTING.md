# Contributing

Use Python 3.12 or later and uv. Keep every MCP instance single-server and preserve the fixed-method
RCON boundary. New user-controlled parameters require explicit validation and security tests. Do not
add shell execution, raw RCON, Docker access, arbitrary filesystem paths, secrets, private domains,
or internal addresses.

Before submitting a change, run:

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
docker compose config
```

Add tests alongside each module change and document externally visible behavior.

