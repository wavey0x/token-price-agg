from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from token_price_agg.app.config import get_settings
from token_price_agg.security.models import ApiKeyRecord, DeleteStatus
from token_price_agg.security.store import ApiKeyStore


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage token-price-agg consumer API keys")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate a new API key")
    generate.add_argument("--label", type=str, default=None, help="Operator label for this key")
    generate.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    list_cmd = subparsers.add_parser("list", help="List API keys")
    list_cmd.add_argument("--all", action="store_true", help="Include deleted keys")
    list_cmd.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    delete = subparsers.add_parser("delete", help="Delete an API key")
    delete.add_argument(
        "key_id",
        type=str,
        help="Public key ID (the suffix in tpa_live_<id>...)",
    )
    delete.add_argument("--reason", type=str, default=None, help="Optional deletion reason")
    delete.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    set_rate_limit = subparsers.add_parser(
        "set-rate-limit",
        help="Set per-key rate limit override (RPM)",
    )
    set_rate_limit.add_argument(
        "key_id",
        type=str,
        help="Public key ID (the suffix in tpa_live_<id>...)",
    )
    set_rate_limit.add_argument("rpm", type=int, help="Per-key rate limit override in requests/min")
    set_rate_limit.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    store = ApiKeyStore(db_path=settings.api_key_db_path)
    handlers: dict[str, Callable[[argparse.Namespace, ApiKeyStore], int]] = {
        "generate": _handle_generate,
        "list": _handle_list,
        "delete": _handle_delete,
        "set-rate-limit": _handle_set_rate_limit,
    }
    try:
        handler = handlers.get(args.command)
        if handler is None:
            print("unknown command", file=sys.stderr)
            return 2
        return handler(args, store)
    except ValueError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": "VALUE_ERROR", "message": str(exc)}))
        else:
            print(str(exc), file=sys.stderr)
        return 1


def _handle_generate(args: argparse.Namespace, store: ApiKeyStore) -> int:
    label = _resolve_label(args.label)
    issued = store.issue_key(label=label)
    payload = {
        "ok": True,
        "id": issued.public_id,
        "label": issued.label,
        "key_prefix": issued.key_prefix,
        "created_at": issued.created_at,
        "created_at_iso": _epoch_to_iso(issued.created_at),
        "key": issued.key,
    }
    if args.json:
        print(json.dumps(payload))
        return 0

    print("Generated API key")
    print(f"id: {issued.public_id}")
    print(f"label: {issued.label}")
    print(f"created_at: {_epoch_to_iso(issued.created_at)}")
    print(f"key: {issued.key}")
    print("Store this key now; it will not be shown again.")
    return 0


def _handle_list(args: argparse.Namespace, store: ApiKeyStore) -> int:
    rows = store.list_keys(include_revoked=args.all)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "count": len(rows),
                    "keys": [_record_to_json(row) for row in rows],
                }
            )
        )
        return 0

    if not rows:
        print("No API keys found.")
        return 0

    for row in rows:
        status = "deleted" if row.revoked_at is not None else "active"
        rate_limit = (
            str(row.rate_limit_rpm) if row.rate_limit_rpm is not None else "default"
        )
        print(
            " ".join(
                [
                    f"id={row.public_id}",
                    f"status={status}",
                    f"label={row.label}",
                    f"created={_epoch_to_iso(row.created_at)}",
                    f"last_used={_epoch_to_iso(row.last_used_at)}",
                    f"rate_limit_rpm={rate_limit}",
                ]
            )
        )
    return 0


def _handle_delete(args: argparse.Namespace, store: ApiKeyStore) -> int:
    result = store.delete_key(
        public_id=args.key_id.strip(),
        reason=args.reason,
    )

    payload: dict[str, Any] = {
        "ok": True,
        "id": result.public_id,
        "status": result.status.value,
        "revoked_at": result.revoked_at,
        "revoked_at_iso": _epoch_to_iso(result.revoked_at),
        "reason": result.revoked_reason,
    }
    if args.json:
        print(json.dumps(payload))
        return 0

    if result.status == DeleteStatus.DELETED:
        print(f"Deleted API key {result.public_id}.")
    elif result.status == DeleteStatus.ALREADY_DELETED:
        print(f"API key {result.public_id} is already deleted.")
    else:
        print(f"API key {result.public_id} not found.")
    return 0


def _handle_set_rate_limit(args: argparse.Namespace, store: ApiKeyStore) -> int:
    key_id = args.key_id.strip()
    updated = store.set_key_rate_limit(
        public_id=key_id,
        rate_limit_rpm=args.rpm,
    )
    payload = {
        "ok": True,
        "id": key_id,
        "status": "updated" if updated else "not_found",
        "rate_limit_rpm": args.rpm if updated else None,
    }
    if args.json:
        print(json.dumps(payload))
        return 0

    if updated:
        print(f"Set API key {key_id} rate limit override to {args.rpm} rpm.")
    else:
        print(f"API key {key_id} not found.")
    return 0


def _resolve_label(value: str | None) -> str:
    if value is not None and value.strip():
        return value.strip()

    try:
        entered = input("Label: ").strip()
    except EOFError as exc:
        raise ValueError("label is required (set --label for non-interactive usage)") from exc

    if not entered:
        raise ValueError("label is required")
    return entered


def _epoch_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


def _record_to_json(row: ApiKeyRecord) -> dict[str, Any]:
    return {
        "id": row.public_id,
        "label": row.label,
        "key_prefix": row.key_prefix,
        "created_at": row.created_at,
        "created_at_iso": _epoch_to_iso(row.created_at),
        "rate_limit_rpm": row.rate_limit_rpm,
        "last_used_at": row.last_used_at,
        "last_used_at_iso": _epoch_to_iso(row.last_used_at),
        "revoked_at": row.revoked_at,
        "revoked_at_iso": _epoch_to_iso(row.revoked_at),
        "revoked_reason": row.revoked_reason,
        "expires_at": row.expires_at,
        "expires_at_iso": _epoch_to_iso(row.expires_at),
    }


if __name__ == "__main__":
    raise SystemExit(main())
