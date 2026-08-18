"""`package_update.sh [repo-path]` Python entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

from repo_offline_sync.core import SyncError
from repo_offline_sync.packager import package_repository


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        print("Usage: package_update.sh [repository]", file=sys.stderr)
        return 2
    repo = Path(args[0]) if args else Path.cwd()
    try:
        package_repository(repo)
    except (SyncError, OSError) as exc:
        code = exc.exit_code if isinstance(exc, SyncError) else 8
        print(f"error: {exc}", file=sys.stderr)
        return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
