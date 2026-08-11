"""Operator CLI for external approval decisions (not an MCP Agent tool)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .config import load_config
from .errors import ErrorCode, MinecraftAdminError
from .server import create_approval_admin


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="minecraft-admin-mcp-approve",
        description=(
            "Approve or reject high-risk requests using MC_ADMIN_TOKEN. "
            "This CLI is intentionally separate from the Agent MCP bearer token."
        ),
    )
    parser.add_argument(
        "action",
        choices=("list", "get", "approve", "reject"),
        help="admin action",
    )
    parser.add_argument("request_id", nargs="?", help="approval request id")
    parser.add_argument("--note", default="", help="decision note")
    parser.add_argument(
        "--token",
        default=None,
        help="admin token (default: env MC_ADMIN_TOKEN or approval.admin_token_env)",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config()
        admin = create_approval_admin(config)
        token_env = config.approval.admin_token_env
        token = args.token if args.token is not None else os.getenv(token_env, "")
        if not token:
            raise MinecraftAdminError(
                ErrorCode.AUTH_REQUIRED,
                f"admin token required via --token or {token_env}",
            )
        payload: Any
        if args.action == "list":
            payload = [item.model_dump(mode="json") for item in admin.list_pending(token)]
        elif args.action == "get":
            if not args.request_id:
                raise SystemExit("request_id is required for get")
            payload = admin.get(token, args.request_id).model_dump(mode="json")
        elif args.action == "approve":
            if not args.request_id:
                raise SystemExit("request_id is required for approve")
            payload = admin.approve(token, args.request_id, note=args.note).model_dump(mode="json")
        else:
            if not args.request_id:
                raise SystemExit("request_id is required for reject")
            payload = admin.reject(token, args.request_id, note=args.note).model_dump(mode="json")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    except MinecraftAdminError as exc:
        print(exc.public_message(), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
