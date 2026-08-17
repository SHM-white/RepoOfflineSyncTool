from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
PACKAGE_WRAPPER: Final = PROJECT_ROOT / "package_update.sh"
INSTALL_WRAPPER: Final = PROJECT_ROOT / "install_target.sh"
USAGE_ERROR: Final = 2


def _run_wrapper(
    wrapper: Path,
    arguments: tuple[str, ...],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (str(wrapper), *arguments),
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_project_metadata_separates_runtime_and_development_dependencies() -> None:
    # Given the standalone project metadata
    metadata = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    # When dependency declarations are inspected
    project_section, dependency_section = metadata.split(
        "[dependency-groups]", maxsplit=1
    )

    # Then Python 3.10 is exclusive, runtime is stdlib-only, and tools are dev-only
    assert 'requires-python = ">=3.10,<3.11"' in project_section
    assert "dependencies = []" in project_section
    assert all(
        f'"{tool}' in dependency_section
        for tool in ("basedpyright", "pytest", "pytest-cov", "ruff")
    )


def test_root_wrappers_are_executable() -> None:
    # Given both root entrypoint wrappers
    wrappers = (PACKAGE_WRAPPER, INSTALL_WRAPPER)

    # When their filesystem modes are inspected
    executable_modes = tuple(wrapper.stat().st_mode for wrapper in wrappers)

    # Then each wrapper is executable by its owner
    assert all(mode & stat.S_IXUSR for mode in executable_modes)


def test_package_wrapper_rejects_too_many_arguments_without_mutation(
    tmp_path: Path,
) -> None:
    # Given an isolated caller directory and an invalid two-argument invocation
    before = tuple(tmp_path.iterdir())

    # When the package wrapper is invoked from outside its source directory
    result = _run_wrapper(PACKAGE_WRAPPER, ("a", "b"), cwd=tmp_path)

    # Then it returns the usage contract and leaves the caller directory unchanged
    assert result.returncode == USAGE_ERROR
    assert tuple(tmp_path.iterdir()) == before


def test_install_wrapper_rejects_arguments_without_mutation(tmp_path: Path) -> None:
    # Given an isolated caller directory and an invalid installer argument
    before = tuple(tmp_path.iterdir())

    # When the installer wrapper is invoked from outside its source directory
    result = _run_wrapper(INSTALL_WRAPPER, ("unexpected",), cwd=tmp_path)

    # Then it returns the usage contract and leaves the caller directory unchanged
    assert result.returncode == USAGE_ERROR
    assert tuple(tmp_path.iterdir()) == before


def test_package_wrapper_forwards_one_repository_argument_unchanged(
    tmp_path: Path,
) -> None:
    # Given a fake interpreter that records module invocation arguments
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    invocation_log = tmp_path / "invocation"
    fake_python = binary_directory / "python3"
    _ = fake_python.write_text(
        """#!/usr/bin/env bash
if [[ $1 == -c ]]; then exit 0; fi
printf '%s\\n' "$@" > "$INVOCATION_LOG"
""",
        encoding="utf-8",
    )
    _ = fake_python.chmod(0o755)
    repository_argument = "repository path with spaces"
    environment = os.environ.copy()
    environment["INVOCATION_LOG"] = str(invocation_log)
    environment["PATH"] = f"{binary_directory}:{environment['PATH']}"

    # When one repository argument is passed from an unrelated working directory
    result = _run_wrapper(
        PACKAGE_WRAPPER,
        (repository_argument,),
        cwd=tmp_path,
        environment=environment,
    )

    # Then the wrapper invokes the future module with that exact argument
    assert result.returncode == 0
    assert invocation_log.read_text(encoding="utf-8").splitlines() == [
        "-m",
        "repo_offline_sync.package_update",
        repository_argument,
    ]


def test_package_wrapper_forwards_zero_repository_arguments(tmp_path: Path) -> None:
    # Given a fake interpreter that records module invocation arguments
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    invocation_log = tmp_path / "invocation"
    fake_python = binary_directory / "python3"
    _ = fake_python.write_text(
        """#!/usr/bin/env bash
if [[ $1 == -c ]]; then exit 0; fi
printf '%s\\n' "$@" > "$INVOCATION_LOG"
""",
        encoding="utf-8",
    )
    _ = fake_python.chmod(0o755)
    environment = os.environ.copy()
    environment["INVOCATION_LOG"] = str(invocation_log)
    environment["PATH"] = f"{binary_directory}:{environment['PATH']}"

    # When no repository argument is passed from an unrelated working directory
    result = _run_wrapper(
        PACKAGE_WRAPPER,
        (),
        cwd=tmp_path,
        environment=environment,
    )

    # Then the wrapper selects no repository and forwards no repository argument
    assert result.returncode == 0
    assert invocation_log.read_text(encoding="utf-8").splitlines() == [
        "-m",
        "repo_offline_sync.package_update",
    ]


def test_unavailable_later_modules_return_configuration_error(tmp_path: Path) -> None:
    # Given valid wrapper argument counts before later CLI modules exist
    invocations = ((PACKAGE_WRAPPER, ()), (INSTALL_WRAPPER, ()))

    # When each wrapper is invoked against the bootstrap-only source tree
    results = tuple(
        _run_wrapper(wrapper, arguments, cwd=tmp_path)
        for wrapper, arguments in invocations
    )

    # Then each unavailable entrypoint returns the configuration contract
    assert tuple(result.returncode for result in results) == (
        USAGE_ERROR,
        USAGE_ERROR,
    )


def test_package_rejects_non_310_interpreter_when_available() -> None:
    # Given a non-3.10 system interpreter when the host provides one
    interpreter = Path("/usr/bin/python3.11")
    if not interpreter.exists():
        return
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")

    # When the package is started under the unsupported interpreter
    result = subprocess.run(
        (str(interpreter), "-m", "repo_offline_sync"),
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    # Then startup is rejected before any updater behavior can run
    assert result.returncode != 0


def test_runtime_package_imports_only_standard_library_modules() -> None:
    # Given a clean interpreter with only the source tree on its import path
    environment = {
        "PATH": os.environ["PATH"],
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(PROJECT_ROOT / "src"),
    }

    # When the runtime package is imported without development dependencies
    result = subprocess.run(
        (sys.executable, "-S", "-c", "import repo_offline_sync"),
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    # Then the import succeeds without third-party packages
    assert result.returncode == 0
