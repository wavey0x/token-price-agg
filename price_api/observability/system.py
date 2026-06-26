from __future__ import annotations

from pathlib import Path

_CLOSE_WAIT_HEX = "08"


def count_process_close_wait_sockets() -> int | None:
    fd_dir = Path("/proc/self/fd")
    if not fd_dir.exists():
        return None

    socket_inodes = _process_socket_inodes(fd_dir)
    if not socket_inodes:
        return 0

    count = 0
    for table_path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        count += _count_close_wait_in_table(table_path, socket_inodes)
    return count


def _process_socket_inodes(fd_dir: Path) -> set[str]:
    inodes: set[str] = set()
    for fd_path in fd_dir.iterdir():
        try:
            target = fd_path.readlink()
        except OSError:
            continue
        target_str = str(target)
        if not target_str.startswith("socket:[") or not target_str.endswith("]"):
            continue
        inodes.add(target_str.removeprefix("socket:[").removesuffix("]"))
    return inodes


def _count_close_wait_in_table(table_path: Path, socket_inodes: set[str]) -> int:
    try:
        lines = table_path.read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return 0

    count = 0
    for line in lines:
        parts = line.split()
        if len(parts) < 10:
            continue
        state = parts[3]
        inode = parts[9]
        if state == _CLOSE_WAIT_HEX and inode in socket_inodes:
            count += 1
    return count
