#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$project_root"

uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest -m 'not privileged'
shellcheck package_update.sh install_target.sh scripts/*.sh
bash -n package_update.sh install_target.sh scripts/*.sh

while IFS= read -r -d '' source_file; do
    pure_lines=$(awk '!/^[[:space:]]*$/ && !/^[[:space:]]*#/' "$source_file" | wc -l)
    if ((pure_lines > 250)); then
        printf 'Source module exceeds 250 pure LOC: %s (%s)\n' "$source_file" "$pure_lines" >&2
        exit 1
    fi
done < <(find src -type f -name '*.py' -print0)
