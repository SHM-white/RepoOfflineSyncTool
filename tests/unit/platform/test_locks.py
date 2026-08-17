from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from repo_offline_sync.platform.locks import (
    LockAcquired,
    LockBusy,
    LockFailure,
    exclusive_lock,
)

HOLDER_SCRIPT = """
import os
import sys
from pathlib import Path
from repo_offline_sync.platform.locks import LockAcquired, exclusive_lock

with exclusive_lock(Path(sys.argv[1])) as outcome:
    if not isinstance(outcome, LockAcquired):
        raise SystemExit(3)
    os.write(int(sys.argv[2]), b"R")
    os.read(int(sys.argv[3]), 1)
"""

CONTENDER_SCRIPT = """
import sys
from pathlib import Path
from repo_offline_sync.platform.locks import LockBusy, exclusive_lock

with exclusive_lock(Path(sys.argv[1])) as outcome:
    raise SystemExit(0 if isinstance(outcome, LockBusy) else 4)
"""


def test_two_process_lock_contention_returns_busy_without_polling(
    tmp_path: Path,
) -> None:
    # Given one process holding a preexisting lock file and an explicit ready pipe
    lock_path = tmp_path / "operation.lock"
    _ = lock_path.write_text("stale metadata", encoding="utf-8")
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[3] / "src")
    holder = subprocess.Popen(
        (
            sys.executable,
            "-c",
            HOLDER_SCRIPT,
            str(lock_path),
            str(ready_write),
            str(release_read),
        ),
        env=environment,
        pass_fds=(ready_write, release_read),
    )
    os.close(ready_write)
    os.close(release_read)

    try:
        assert os.read(ready_read, 1) == b"R"

        # When an independent second process attempts the same nonblocking lock
        contender = subprocess.run(
            (sys.executable, "-c", CONTENDER_SCRIPT, str(lock_path)),
            env=environment,
            check=False,
            timeout=5,
        )

        # Then contention is reported as busy and release allows later acquisition
        assert contender.returncode == 0
        _ = os.write(release_write, b"X")
        assert holder.wait(timeout=5) == 0
        with exclusive_lock(lock_path) as after_release:
            assert isinstance(after_release, LockAcquired)
    finally:
        os.close(ready_read)
        os.close(release_write)
        if holder.poll() is None:
            holder.terminate()
            _ = holder.wait(timeout=5)


def test_same_process_second_lock_is_busy_until_context_exit(tmp_path: Path) -> None:
    # Given one acquired flock context
    lock_path = tmp_path / "operation.lock"

    # When the same process opens and locks the stable file again
    with exclusive_lock(lock_path) as first, exclusive_lock(lock_path) as second:
        observed = (first, second)

    # Then the first owns the lock and the second deterministically reports busy
    assert isinstance(observed[0], LockAcquired)
    assert isinstance(observed[1], LockBusy)


def test_lock_open_failure_is_typed_without_creating_parent(tmp_path: Path) -> None:
    # Given a lock path beneath a missing parent
    lock_path = tmp_path / "missing" / "operation.lock"

    # When acquisition is attempted
    with exclusive_lock(lock_path) as outcome:
        observed = outcome

    # Then open failure is typed and no stale path is created
    assert isinstance(observed, LockFailure)
    assert observed.operation == "open"
    assert not lock_path.parent.exists()
