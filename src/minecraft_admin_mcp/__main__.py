import sys

from .config import load_config
from .errors import MinecraftAdminError
from .server import create_mcp_server


def main() -> None:
    try:
        config = load_config()
        server = create_mcp_server(config)
        server.run(
            transport="http",
            host=config.http.host,
            port=config.http.port,
            path=config.http.path,
            show_banner=False,
        )
    except MinecraftAdminError as exc:
        print(exc.public_message(), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
