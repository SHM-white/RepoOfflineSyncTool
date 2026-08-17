# Repo Offline Sync

Repo Offline Sync is a standalone Ubuntu 22.04 updater that will transport
repository updates over removable media. The target runtime is Python 3.10 and
uses only the standard library.

Task 1 establishes the strict project and its entrypoint contracts. Packaging,
installation, repository handling, media handling, and target update behavior
are intentionally unavailable until later tasks implement them.

## Development

Install the locked development environment and run every quality gate:

```bash
uv sync --locked --dev
./scripts/quality.sh
```

The development group contains pytest, pytest-cov, Ruff, and basedpyright.
There are no project runtime dependencies.

## Entrypoints

`./package_update.sh [repository]` accepts zero or one repository argument. The
wrapper forwards the caller's arguments unchanged and does not select a
repository. `./install_target.sh` accepts no arguments. Until their later Python
modules exist, both wrappers exit with code 2 without changing the workspace.
