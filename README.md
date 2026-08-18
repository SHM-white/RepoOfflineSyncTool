# Repo Offline Sync

A lightweight Ubuntu 22.04 / Python 3.10 offline Git deployment framework. It
moves exact Git revisions through a removable drive without using the network,
materializes versioned releases on the target, runs finite argv-based build and
health actions, atomically activates the new release, and records a receipt for
the next incremental package.

The target runtime uses only Python 3.10's standard library plus normal Ubuntu
system tools (`git`, `systemd`, `udev`, `lsblk`, `findmnt`, `mount`, `umount`).
It does not install packages or fetch dependencies from the network.

## Host workflow

Run from a clean, non-shallow Git worktree:

```bash
./package_update.sh [repository]
```

With no argument the caller's current directory is used. The first run creates
an XDG host profile under `~/.config/repo-offline-sync/repos/` and asks for the
target ID, pairing token, destination, service user/unit, persistent paths and
failure policy. Later runs reuse that profile.

The packager:

1. rejects a dirty source tree;
2. resolves the exact `HEAD` and recursively verifies committed submodule
   gitlinks against locally initialized submodules;
3. scans only exact-target Git LFS pointer blobs and verifies the corresponding
   local SHA-256 objects;
4. reads target receipts from the selected media and uses installed commits as
   incremental bundle prerequisites;
5. builds one indivisible bundle per repository and optionally a self-contained
   full fallback (prompted every run, default **No**);
6. writes the package into `offline-update/staging/`, fsyncs it, renames it into
   `inbox/`, fully rereads SHA-256/CRC32 inventory, and only then publishes
   `READY.json`.

If no initialized medium is discovered, the command asks for a mounted path and
can initialize only its `offline-update/` directory. ext4, exFAT and NTFS are
accepted. `VTOYEFI`, EFI and ISO9660 partitions are ignored by automatic
scanning. Set `REPO_OFFLINE_SYNC_MEDIA=/mount/path` to select a medium without a
prompt.

### Host profile maintenance

```bash
PYTHONPATH=src python3 -m repo_offline_sync.profile_tool show [repo]
PYTHONPATH=src python3 -m repo_offline_sync.profile_tool edit [repo]
PYTHONPATH=src python3 -m repo_offline_sync.profile_tool rotate-token [repo]
PYTHONPATH=src python3 -m repo_offline_sync.profile_tool reset-settings [repo]
```

The profile's `actions` object contains five finite phases:
`preflight`, `build`, `pre_activate`, `post_activate`, and `health`. Each entry is
an object such as:

```json
{
  "name": "build",
  "argv": ["cmake", "--build", "build", "-j2"],
  "cwd": ".",
  "env": {"MY_MODE": "production"},
  "user": "robot",
  "timeout": 1200
}
```

Actions are executed as argv arrays, not `shell=True`; shell `-c`/`-lc` command
strings are rejected by the packager. The action environment is reduced to a
small base environment plus the explicitly declared values.

Destinations outside the normal `/home/<service-user>/...` tree require the
saved `danger_enabled` switch and an interactive confirmation on every package
operation. `/` is never accepted.

## Target installation

On Ubuntu 22.04:

```bash
sudo ./install_target.sh
```

The no-argument installer installs/repairs the runtime under
`/usr/lib/repo-offline-sync`, creates `/etc/repo-offline-sync/target.json`,
installs systemd/udev integration and prints the generated `target_id` and
128-bit pairing token. Copy those two values into the host profile. The token is
only a mismatch guard; it is **not** cryptographic package authentication.

Running `install_target.sh` again offers repair/reinstall, token rotation and
uninstall. Target state is stored below `/var/lib/repo-offline-sync/`.

Status after installation:

```bash
sudo /usr/libexec/repo-offline-sync/status
```

## Target update flow

The boot unit and udev-triggered scan service serialize updates with a global
lock. The scanner may privately mount supported unmounted partitions without
force/lazy options, ignores unmarked media, replays old pending receipts, fully
copies READY packages into local staging and normally unmounts the removable
medium before executing an update.

For each selected package the target:

1. validates target ID/pairing, destination structure and package bytes;
2. selects the smallest usable incremental bundle whose base commit already
   exists in its managed bare repository, falling back to a full bundle when
   present; otherwise returns `needs-full-bundle` with no activation;
3. imports bundles into managed bare repositories and caches exact LFS objects;
4. snapshots an unmanaged existing destination without changing its Git index,
   preserving its committed HEAD in an `offline-backup/...` ref plus tracked and
   nonignored-untracked data;
5. creates a detached versioned root worktree and exact recursive submodule
   worktrees, replaces LFS pointers with packaged object bytes, and links
   configured persistent paths to `/var/lib/repo-offline-sync/persistent/`;
6. runs preflight/build actions, stops the configured service, records the
   `activating` transaction checkpoint, atomically swaps the destination symlink,
   starts the service and runs health actions;
7. commits success, rolls back to the old destination for `rollback`, or leaves
   the failed release stopped for `keep-failed-stopped`;
8. saves a machine-readable result locally and queues it for the next writable
   insertion of the same medium. The host ingests that receipt on the next
   package operation and can create a smaller increment.

Interrupted transactions are reconciled at scanner startup. An interruption
before activation is aborted without touching the active destination; an
interruption across the activation boundary is rolled back when the package
policy requests rollback.

## Media layout

```text
offline-update/
├── media.json
├── inbox/
│   └── pkg-<id>/
│       ├── manifest.json
│       ├── bundles/
│       ├── lfs/
│       └── READY.json
├── staging/
└── results/
```

Bundle and LFS files are never physically chunked. A bundle records its logical
generation-tier target (approximately 25/50/95 MiB) and may be marked oversize
when one indivisible bundle exceeds that target.

## RM2026-AutoAim example

`examples/rm2026-autoaim/profile.example.json` shows a minimal CMake/service
configuration. It is an example only; this updater does not modify the original
`/home/shm-white/RM2026-AutoAim` repository during packaging or installation.

## Security boundary

SHA-256/CRC32 protect transfer integrity and the pairing token helps catch a
wrong target/drive combination. This version does **not** provide publisher
signatures or defend against an attacker who can replace the whole package on
the removable medium.
