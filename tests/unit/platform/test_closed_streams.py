from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

PROJECT_ROOT: Final = Path(__file__).resolve().parents[3]

SUPERVISOR_SCRIPT: Final = r"""
import os
import socket
import sys
import threading
from pathlib import Path

from repo_offline_sync.platform.commands import (
    CancellationToken,
    CommandRequest,
    run_command,
)

mode, parent_socket, cancel_socket, root = sys.argv[1:]
child_script = r'''import os,signal,socket,sys
os.close(1)
os.close(2)
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as ready:
    ready.connect(sys.argv[1])
    ready.sendall(str(os.getpid()).encode())
if sys.argv[2] != "-":
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as cancel_ready:
        cancel_ready.connect(sys.argv[2])
        cancel_ready.sendall(b"R")
signal.pause()
'''
request = CommandRequest(
    argv=(sys.executable, "-c", child_script, parent_socket, cancel_socket),
    cwd=Path(root),
    environment=tuple(sorted(os.environ.items())),
    timeout_seconds=0.2 if mode == "timeout" else 5.0,
    output_limit_bytes=1024,
    termination_grace_seconds=1.0,
)
if mode == "timeout":
    outcome = run_command(request)
else:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(cancel_socket)
    listener.listen(1)
    with CancellationToken() as cancellation:
        def cancel_when_ready():
            connection = listener.accept()[0]
            with connection:
                if connection.recv(1) != b"R":
                    raise SystemExit(8)
            cancellation.cancel()
        worker = threading.Thread(target=cancel_when_ready)
        worker.start()
        outcome = run_command(request, cancellation=cancellation)
        worker.join(timeout=2)
        if worker.is_alive():
            raise SystemExit(9)
    listener.close()
print(type(outcome).__name__)
"""


def _group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    return True


def _kill_group(group_id: int) -> None:
    try:
        os.killpg(group_id, signal.SIGKILL)
    except ProcessLookupError:
        return


def _supervisor_children(supervisor_pid: int) -> tuple[int, ...]:
    children_path = Path(f"/proc/{supervisor_pid}/task/{supervisor_pid}/children")
    if not children_path.exists():
        return ()
    content = children_path.read_text(encoding="utf-8").strip()
    return tuple(int(value) for value in content.split()) if content else ()


def _cleanup_supervisor(
    supervisor: subprocess.Popen[bytes],
    child_pid: int | None,
) -> None:
    child_groups = (
        _supervisor_children(supervisor.pid) if child_pid is None else (child_pid,)
    )
    for group_id in child_groups:
        _kill_group(group_id)
    _kill_group(supervisor.pid)
    _ = supervisor.communicate(timeout=2)


def _run_closed_stream_supervisor(tmp_path: Path, mode: str) -> tuple[str, int]:
    parent_socket = tmp_path / f"parent-{mode}.sock"
    cancel_socket = tmp_path / f"cancel-{mode}.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(parent_socket))
    listener.listen(1)
    listener.settimeout(3)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    supervisor = subprocess.Popen(
        (
            sys.executable,
            "-c",
            SUPERVISOR_SCRIPT,
            mode,
            str(parent_socket),
            str(cancel_socket) if mode == "cancel" else "-",
            str(tmp_path),
        ),
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    child_pid: int | None = None
    try:
        connection = listener.accept()[0]
        with connection:
            child_pid = int(connection.recv(32))
        try:
            stdout, stderr = supervisor.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            _cleanup_supervisor(supervisor, child_pid)
            pytest.fail("run_command remained blocked after both output pipes closed")
    except TimeoutError:
        _cleanup_supervisor(supervisor, child_pid)
        pytest.fail("closed-stream child did not report its owned process group")
    finally:
        listener.close()
    assert stderr == b""
    assert supervisor.returncode == 0
    assert child_pid is not None
    assert not _group_exists(child_pid)
    return stdout.decode().strip(), child_pid


def test_closed_stream_child_remains_under_timeout_contract(tmp_path: Path) -> None:
    # Given a supervised child that closes stdout and stderr before blocking
    # When its command deadline expires
    outcome_name, child_pid = _run_closed_stream_supervisor(tmp_path, "timeout")

    # Then timeout is typed and the owned child process group is gone
    assert outcome_name == "CommandTimedOut"
    assert not Path(f"/proc/{child_pid}").exists()


def test_cancellation_after_output_eof_terminates_process_group(tmp_path: Path) -> None:
    # Given a supervised child that closes output before cancellation readiness
    # When the cancellation wake descriptor becomes readable
    outcome_name, child_pid = _run_closed_stream_supervisor(tmp_path, "cancel")

    # Then cancellation remains typed and the owned child process group is gone
    assert outcome_name == "CommandCancelled"
    assert not Path(f"/proc/{child_pid}").exists()
