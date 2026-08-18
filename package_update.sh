#!/usr/bin/env bash
set -euo pipefail

if (($# > 1)); then
    printf '用法：%s [仓库路径]\n' "${0##*/}" >&2
    exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
module=repo_offline_sync.package_update

if ! PYTHONPATH="$script_dir/src" python3 -c \
    'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)' \
    "$module"; then
    printf '错误：程序入口不可用：%s\n' "$module" >&2
    exit 2
fi

exec env PYTHONPATH="$script_dir/src" python3 -m "$module" "$@"
