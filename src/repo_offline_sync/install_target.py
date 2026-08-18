"""`install_target.sh` Python entrypoint."""

from __future__ import annotations

import sys

from repo_offline_sync.core import SyncError
from repo_offline_sync.installer import interactive


def main() -> int:
    if len(sys.argv) != 1:
        print("Usage: install_target.sh", file=sys.stderr)
        return 2
    try:
        interactive()
    except (SyncError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
