import socket

from .config import load_config


def main() -> None:
    config = load_config()
    wildcard_hosts = {"0.0.0.0", "::"}  # noqa: S104 - identify configured wildcard binding
    host = "127.0.0.1" if config.http.host in wildcard_hosts else config.http.host
    with socket.create_connection((host, config.http.port), timeout=3):
        pass


if __name__ == "__main__":
    main()
