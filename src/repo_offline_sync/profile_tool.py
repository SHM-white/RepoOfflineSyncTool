"""Small host-profile maintenance utility."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

from repo_offline_sync.configuration import load_or_create_profile, save_profile
from repo_offline_sync.core import SyncError
from repo_offline_sync.gitrepo import repository_facts


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in {"show", "edit", "rotate-token", "reset-settings"} or len(args) > 2:
        print("Usage: python3 -m repo_offline_sync.profile_tool {show|edit|rotate-token|reset-settings} [repo]", file=sys.stderr)
        return 2
    command = args[0]
    repo = Path(args[1]) if len(args) == 2 else Path.cwd()
    try:
        facts = repository_facts(repo)
        path, profile, _ = load_or_create_profile(facts["common_git_dir"], facts["remote"])
        if command == "show":
            print(path)
            print(f"repo_id={profile.get('repo_id')}")
            print(f"target_id={profile.get('target_id')}")
            print(f"destination={profile.get('destination')}")
            print(f"generation={profile.get('generation', 0)}")
            return 0
        if command == "edit":
            editor = os.environ.get("EDITOR", "vi")
            return subprocess.run([editor, str(path)], check=False).returncode
        if command == "rotate-token":
            token = secrets.token_hex(16)
            profile["pairing_token"] = token
            save_profile(path, profile)
            print(f"rotated pairing token in {path}")
            print(f"new_pairing_token={token}")
            print("Set the target config to the same token before using new packages.")
            return 0
        for key, value in {
            "service_unit": "",
            "persistent_paths": [],
            "failure_policy": "rollback",
            "danger_enabled": False,
            "actions": {"preflight": [], "build": [], "pre_activate": [], "post_activate": [], "health": []},
        }.items():
            profile[key] = value
        save_profile(path, profile)
        print(f"reset editable execution settings in {path}")
        return 0
    except (SyncError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 8)


if __name__ == "__main__":
    raise SystemExit(main())
