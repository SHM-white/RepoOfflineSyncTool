"""Small standard-library helpers shared by the functional updater."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class SyncError(RuntimeError):
    """User-facing updater failure with a stable exit class."""

    def __init__(self, message: str, exit_code: int = 3) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int | None = None,
    check: bool = True,
    clean_env: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one argv-only command and capture text output."""
    if clean_env:
        merged = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TZ") if key in os.environ}
    else:
        merged = os.environ.copy()
    if env:
        merged.update(env)
    try:
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            env=merged,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise SyncError(f"required command not found: {argv[0]}", 2) from exc
    except subprocess.TimeoutExpired as exc:
        raise SyncError(f"command timed out: {' '.join(argv)}", 3) from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SyncError(f"command failed ({result.returncode}): {' '.join(argv)}\n{detail}")
    return result


def canonical_json(value: Any) -> bytes:
    """Encode portable deterministic UTF-8 JSON."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def load_json(path: Path) -> Any:
    with path.open("rb") as handle:
        return json.load(handle)


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write and fsync one file, then replace it atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        fsync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_json(path: Path, value: Any, mode: int = 0o600) -> None:
    atomic_write(path, canonical_json(value), mode)


def fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def hash_file(path: Path) -> dict[str, Any]:
    sha = hashlib.sha256()
    crc = 0
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            sha.update(chunk)
            crc = zlib.crc32(chunk, crc)
            size += len(chunk)
    return {"sha256": sha.hexdigest(), "crc32": f"{crc & 0xffffffff:08x}", "size": size}


def copy_verified(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    expected = hash_file(source)
    actual = hash_file(destination)
    if expected != actual:
        raise SyncError(f"copy verification failed: {destination}", 8)
    return actual


def safe_relative(raw: str) -> Path:
    value = Path(raw)
    if value.is_absolute() or not raw or raw in {".", ".."} or "\x00" in raw or any(part in ("", ".", "..") for part in value.parts):
        raise SyncError(f"unsafe relative path: {raw!r}")
    return value


def inside(root: Path, relative: str) -> Path:
    rel = safe_relative(relative)
    candidate = (root / rel).resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise SyncError(f"path escapes root: {relative!r}")
    return candidate


def iter_files(root: Path, *, exclude_names: Iterable[str] = ()) -> Iterable[Path]:
    excluded = set(exclude_names)
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in excluded:
            yield path
