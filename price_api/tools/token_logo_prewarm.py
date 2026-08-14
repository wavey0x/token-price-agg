from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from price_api.app.config import get_settings
from price_api.core.validator import AddressValidator
from price_api.token_metadata.cache import (
    TokenLogoStatus,
    TokenMetadataCache,
    read_logo_statuses,
)
from price_api.token_metadata.logo_service import DEFAULT_LOGO_MAINTENANCE_SETTINGS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="token-logo-prewarm",
        description="Enroll or read the bounded identity-only token-logo prewarm state.",
    )
    parser.add_argument("--db-path", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    enroll = subparsers.add_parser("enroll")
    enroll.add_argument("--input", required=True)
    enroll.add_argument("--force-existing", action="store_true")
    enroll.add_argument("--confirm-service-stopped", action="store_true", required=True)
    enroll.add_argument("--service-name", default="price-api")

    wait = subparsers.add_parser("wait")
    wait.add_argument("--input", required=True)
    wait.add_argument("--started-at-ms", required=True, type=int)
    wait.add_argument(
        "--deadline-seconds",
        type=int,
        default=DEFAULT_LOGO_MAINTENANCE_SETTINGS.prewarm_deadline_ms // 1000,
    )
    wait.add_argument("--poll-seconds", type=float, default=2.0)

    status = subparsers.add_parser("status")
    status.add_argument("--input", required=True)
    status.add_argument("--started-at-ms", type=int)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    db_path = args.db_path or get_settings().token_metadata_db_path
    identities = read_identities(Path(args.input))

    if args.command == "enroll":
        if _service_is_active(args.service_name):
            raise SystemExit(
                f"{args.service_name} is active; stop it before enrolling logo identities"
            )
        cache = TokenMetadataCache(db_path=db_path)
        started_at = cache.enroll_identities(
            identities=identities,
            force_existing=bool(args.force_existing),
        )
        _print_json(
            {
                "command": "enroll",
                "identity_count": len(identities),
                "force_existing": bool(args.force_existing),
                "enrollment_started_at_ms": started_at,
            }
        )
        return

    if args.command == "status":
        _print_json(_status_payload(db_path, identities, args.started_at_ms))
        return

    if args.deadline_seconds <= 0 or args.poll_seconds <= 0:
        raise SystemExit("deadline and poll intervals must be positive")
    deadline = time.monotonic() + args.deadline_seconds
    while True:
        payload = _status_payload(db_path, identities, args.started_at_ms)
        if payload["pending"] == 0:
            _print_json(payload)
            return
        if time.monotonic() >= deadline:
            _print_json(payload)
            raise SystemExit(1)
        time.sleep(min(args.poll_seconds, max(deadline - time.monotonic(), 0.01)))


def read_identities(path: Path) -> list[tuple[int, str]]:
    identities: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lowered = line.lower().replace(" ", "")
        if lowered in {"chain_id,address", "chain_id\taddress"}:
            continue
        fields = line.replace("\t", ",").split(",")
        if len(fields) != 2:
            raise ValueError(f"{path}:{line_number}: expected chain_id,address")
        try:
            chain_id = int(fields[0].strip())
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: chain_id must be an integer") from exc
        if chain_id <= 0:
            raise ValueError(f"{path}:{line_number}: chain_id must be positive")
        address = AddressValidator.normalize_address(fields[1].strip())
        identity = (chain_id, address)
        if identity not in seen:
            identities.append(identity)
            seen.add(identity)
    if not identities:
        raise ValueError("identity input is empty")
    return identities


def _status_payload(
    db_path: str,
    identities: list[tuple[int, str]],
    started_at_ms: int | None,
) -> dict[str, object]:
    statuses = read_logo_statuses(db_path=db_path, identities=identities)
    completed = [
        status
        for status in statuses
        if _completed_for_checkpoint(status=status, started_at_ms=started_at_ms)
    ]
    outcomes = Counter(status.last_outcome or "unknown" for status in completed)
    return {
        "command": "status",
        "identity_count": len(statuses),
        "attempted": len(completed),
        "pending": len(statuses) - len(completed),
        "success": outcomes["success"],
        "unavailable": outcomes["unavailable"],
        "transient": outcomes["transient"],
    }


def _completed_for_checkpoint(
    *,
    status: TokenLogoStatus,
    started_at_ms: int | None,
) -> bool:
    if started_at_ms is None:
        return status.last_attempt_at is not None
    if status.has_image and status.next_attempt_at is None:
        return True
    return status.last_attempt_at is not None and status.last_attempt_at > started_at_ms


def _service_is_active(service_name: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _print_json(payload: dict[str, object]) -> None:
    json.dump(payload, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
