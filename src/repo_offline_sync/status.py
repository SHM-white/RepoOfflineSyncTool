"""Small operator status command for an installed target."""

from __future__ import annotations

import json
from pathlib import Path

from repo_offline_sync.configuration import load_target_config, target_state_root
from repo_offline_sync.core import load_json


def main() -> int:
    config = load_target_config()
    root = target_state_root()
    print(f"target_id: {config.get('target_id')}")
    print(f"state_root: {root}")
    results = sorted((root / "results").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if (root / "results").is_dir() else []
    if results:
        value = load_json(results[0])
        if isinstance(value, dict):
            print(f"last_status: {value.get('status')}")
            print(f"last_package: {value.get('package_id')}")
            print(f"active_release: {value.get('active_release')}")
    pending = sum(1 for _ in (root / "pending-results").glob("*/*.json")) if (root / "pending-results").is_dir() else 0
    print(f"pending_results: {pending}")
    transactions = []
    if (root / "transactions").is_dir():
        for path in sorted((root / "transactions").glob("*.json")):
            try:
                value = load_json(path)
            except OSError:
                continue
            if isinstance(value, dict):
                transactions.append({"id": value.get("transaction_id"), "phase": value.get("phase"), "package": value.get("package_id")})
    if transactions:
        print("transactions:")
        print(json.dumps(transactions[-10:], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
