# Repo Offline Sync 完整使用与手动测试指南

本文档面向当前轻量功能框架，目标是让你可以先把核心流程跑起来，再逐步调真实 U 盘、systemd、udev、构建命令和业务服务。

> 当前目标运行环境：Ubuntu 22.04 + Python 3.10。
>
> 目标端运行时只依赖 Python 3.10 标准库和常见系统工具，不会联网安装 Python 包或系统包。

---

## 1. 当前实现覆盖的功能

当前框架包含以下主流程：

- `./package_update.sh [仓库路径]` 主机端打包入口；
- XDG 仓库配置复用；
- 干净 Git 工作区检查；
- 精确目标 commit；
- recursive Git submodule；
- exact-target Git LFS object；
- Git full bundle 与 incremental bundle；
- 根据目标回执选择增量 base；
- removable-media `staging -> inbox -> READY` 发布；
- SHA-256 + CRC32 完整重读验证；
- 目标端 managed bare repository；
- detached versioned release worktree；
- recursive submodule materialization；
- LFS pointer 离线替换；
- persistent paths；
- 已有 dirty 目录接管与备份；
- build/pre-activate/post-activate/health 结构化 action；
- 原子 destination symlink 激活；
- `rollback`；
- `keep-failed-stopped`；
- transaction 状态与中断恢复；
- result receipt 本地保存、排队与下次插盘回写；
- systemd/udev scanner 框架；
- `install_target.sh` 安装/修复/配对令牌轮换/卸载；
- status 命令。

当前不提供：

- package 数字签名或 publisher authentication；
- 操作系统/内核/bootloader 更新；
- 在线下载依赖；
- GPIO/LED/蜂鸣器安全拔盘提示；
- 自动格式化/重新分区 U 盘；
- 强制或 lazy unmount；
- dirty/hibernated NTFS 强制挂载。

pairing token 只是防止“包/目标机配错”，不是密码学认证。

---

# 2. 环境要求

## 2.1 主机端

推荐 Ubuntu 22.04，至少需要：

```bash
git --version
python3 --version
lsblk --version
findmnt --version
```

Python 必须是：

```text
3.10.x
```

项目会主动拒绝其他 Python major/minor 版本。

## 2.2 目标机

目标机预期：

```text
Ubuntu 22.04
Python 3.10
systemd
udev
git
lsblk
findmnt
mount
umount
```

实际安装前可检查：

```bash
python3 --version
git --version
systemctl --version
udevadm --version
```

如果在 WSL 中调试，请确认 WSL 已启用 systemd。真实 udev removable-media 行为与普通 Ubuntu 真机可能存在差异，最终仍建议在目标设备上验证一次。

---

# 3. 目录结构

## 3.1 主机配置

默认：

```text
~/.config/repo-offline-sync/repos/<identity>.json
~/.cache/repo-offline-sync/
~/.local/state/repo-offline-sync/
```

可通过：

```bash
XDG_CONFIG_HOME
XDG_CACHE_HOME
XDG_STATE_HOME
```

覆盖。

## 3.2 目标机

默认配置：

```text
/etc/repo-offline-sync/target.json
```

默认状态：

```text
/var/lib/repo-offline-sync/
├── repos/
├── releases/
├── persistent/
├── staging/
├── transactions/
├── results/
├── pending-results/
└── backups/
```

本地调试时可用：

```bash
export REPO_OFFLINE_SYNC_STATE=/tmp/ros-test/target-state
export REPO_OFFLINE_SYNC_TARGET_CONFIG=/tmp/ros-test/target.json
```

避免污染正式系统目录。

## 3.3 更新介质

```text
offline-update/
├── media.json
├── inbox/
│   └── pkg-<package-id>/
│       ├── manifest.json
│       ├── bundles/
│       ├── lfs/
│       └── READY.json
├── staging/
└── results/
```

只有存在：

```text
offline-update/media.json
```

的分区才会被自动识别为 Repo Offline Sync 更新介质。

---

# 4. 正式使用流程

## 4.1 第一步：在目标机安装 updater

在目标机的 RepoOfflineSyncTool 目录执行：

```bash
sudo ./install_target.sh
```

首次安装会创建目标配置并输出：

```text
target_id=...
pairing_token=...
```

保存这两个值，第一次在主机给对应仓库打包时需要输入。

再次执行：

```bash
sudo ./install_target.sh
```

会提供 repair/reinstall、rotate pairing token、uninstall、quit。

安装后可查看状态：

```bash
sudo /usr/libexec/repo-offline-sync/status
```

## 4.2 第二步：准备 Git 仓库

主仓库必须：

- 是普通 Git worktree；
- 不是 shallow repository；
- `git status` 干净；
- 所有需要的 submodule 已经在精确 gitlink commit；
- recursive submodule 已初始化；
- 目标 commit 所引用的 Git LFS object 已经存在于本地。

建议执行：

```bash
cd /path/to/repository

git status
git submodule update --init --recursive
```

如果使用 Git LFS，还应确保目标对象已经下载：

```bash
git lfs pull
```

打包工具不会为了补对象访问网络。

## 4.3 第三步：插入更新 U 盘并打包

支持 ext4、exFAT、NTFS/ntfs3/ntfs-3g。自动扫描会忽略 `VTOYEFI`、EFI system partition 和 ISO9660。

进入工具目录：

```bash
cd ~/RepoOfflineSyncTool
```

指定仓库：

```bash
./package_update.sh /path/to/repository
```

或者从项目目录调用：

```bash
cd /path/to/repository
/path/to/RepoOfflineSyncTool/package_update.sh
```

无参数时使用调用者当前目录。

第一次会创建 profile，并询问目标 ID、pairing token、目标部署路径、service user、systemd service unit、persistent paths 和失败策略。

### 推荐部署路径

普通情况下推荐：

```text
/home/<service-user>/<应用目录>
```

例如：

```text
/home/shm-white/RM2026-AutoAim-release
```

只有 service user 自己的 `/home/<service-user>/...` 默认视为普通路径。`/opt/...`、`/etc/...`、`/root/...`、`/tmp/...`、`/home/其他用户/...` 等需要高风险确认；`/` 永远不能作为 destination。

### 第一次安装必须注意 full fallback

首次给一个完全没有本地 Git objects 的目标机打包时，看到：

```text
是否同时加入完整备用包？
1. 是
2. 否（默认）
```

请选择 `1`。否则 fresh target 没有增量 base，会返回 `needs-full-bundle`。

后续正常增量更新一般可以选择 `2`，减少更新包体积。

---

# 5. 目标机自动更新流程

```text
发现 marked media
    ↓
处理上一次 pending result
    ↓
验证 READY / inventory
    ↓
复制 package 到本地 staging
    ↓
正常卸载移动介质
    ↓
检查目标 / pairing / bundle route
    ↓
导入 Git objects
    ↓
恢复 LFS
    ↓
创建 versioned release
    ↓
恢复 recursive submodule
    ↓
挂接 persistent paths
    ↓
执行 build
    ↓
原子切换 destination symlink
    ↓
启动 service / health
    ↓
success 或 rollback / preserve failed
    ↓
result 保存到目标本地 pending-results
```

因为实际更新是在 U 盘卸载之后执行，所以本次更新结果无法在同一次插盘末尾再写回已经卸载的 U 盘。

更新完成后，再把同一 U 盘插到目标机一次：

```text
pending-results -> U盘 offline-update/results/
```

成功 generation 已经安装过的 package 会被跳过，不会再次应用、不会再次生成同一个 pending result，也不会选择更旧 package 自动降级。

然后将 U 盘带回主机。下一次 `package_update.sh` 会读取 receipt，以目标实际已有 commit 作为增量 base。

---

# 6. Profile 管理

查看：

```bash
PYTHONPATH=src python3 -m repo_offline_sync.profile_tool show /path/to/repository
```

编辑：

```bash
PYTHONPATH=src python3 -m repo_offline_sync.profile_tool edit /path/to/repository
```

轮换主机 profile 中的 pairing token：

```bash
PYTHONPATH=src python3 -m repo_offline_sync.profile_tool rotate-token /path/to/repository
```

重置执行相关设置：

```bash
PYTHONPATH=src python3 -m repo_offline_sync.profile_tool reset-settings /path/to/repository
```

主机与目标机 pairing token 必须一致。

---

# 7. Build / Health action 配置

profile 中包含：

```json
"actions": {
  "preflight": [],
  "build": [],
  "pre_activate": [],
  "post_activate": [],
  "health": []
}
```

每个 action 使用结构化 argv：

```json
{
  "name": "cmake-build",
  "argv": ["cmake", "--build", "build", "-j2"],
  "cwd": ".",
  "env": {},
  "user": "shm-white",
  "timeout": 1200
}
```

不要配置 `bash -c` / `sh -c` 形式的任意 shell command string；packager 会拒绝。

复杂构建逻辑建议写成仓库内受版本控制的脚本，然后 action 直接执行该脚本，例如：

```json
{
  "name": "build",
  "argv": ["./scripts/offline_build.sh"],
  "cwd": ".",
  "env": {},
  "user": "shm-white",
  "timeout": 1200
}
```

---

# 8. Persistent paths

例如：

```json
"persistent_paths": ["logs", "config/local", "calibration"]
```

实际数据保存在：

```text
/var/lib/repo-offline-sync/persistent/<repo-id>/...
```

每个 release 中对应路径是 symlink，因此升级新 commit 后运行时数据不会随旧 release 丢失。

---

# 9. 失败策略

## 9.1 rollback

默认：

```json
"failure_policy": "rollback"
```

activation 后 post_activate、service 启动或 health 失败时，工具会尝试恢复旧 destination 并重新启动旧 service。

结果状态：

```text
failed-rolled-back
```

## 9.2 keep-failed-stopped

```json
"failure_policy": "keep-failed-stopped"
```

失败后保留失败 release 和诊断现场，并保持 service 停止。

结果状态：

```text
failed-preserved
```

---

# 10. 本地手动测试：最小 v1 完整安装

下面全部使用 `/tmp/ros-test`，方便随时清理。

## 10.1 创建测试仓库

```bash
rm -rf /tmp/ros-test
mkdir -p /tmp/ros-test/repo
cd /tmp/ros-test/repo

git init
git config user.name test
git config user.email test@example.com

echo "version 1" > app.txt
git add app.txt
git commit -m "v1"
```

## 10.2 创建目录模拟更新介质

```bash
mkdir -p /tmp/ros-test/media/offline-update/{inbox,staging,results}

cat >/tmp/ros-test/media/offline-update/media.json <<'MEDIAEOF'
{
  "schema": "media-v1",
  "media_id": "local-test-media",
  "filesystem": "ext4"
}
MEDIAEOF
```

本步骤只是本地调试时模拟“已经初始化好的 U 盘”。正式使用时应使用真实挂载介质。

## 10.3 隔离主机配置

```bash
cd ~/RepoOfflineSyncTool

export XDG_CONFIG_HOME=/tmp/ros-test/host-config
export XDG_CACHE_HOME=/tmp/ros-test/host-cache
export XDG_STATE_HOME=/tmp/ros-test/host-state
export REPO_OFFLINE_SYNC_MEDIA=/tmp/ros-test/media
```

运行：

```bash
./package_update.sh /tmp/ros-test/repo
```

推荐输入：

```text
目标 ID：local-test
配对令牌：0123456789abcdef0123456789abcdef
目标机部署路径：/home/<你的用户名>/ros-test-target
运行服务的用户：<你的用户名>
systemd 服务：留空
persistent paths：留空
失败策略：1
完整备用包：1
```

不要写成 `/<用户名>/ros-test-target`。正确示例是 `/home/shm-white/ros-test-target`。

## 10.4 创建模拟目标配置

```bash
cat >/tmp/ros-test/target.json <<'TARGETEOF'
{
  "schema": "target-v1",
  "target_id": "local-test",
  "pairing_token": "0123456789abcdef0123456789abcdef"
}
TARGETEOF

export REPO_OFFLINE_SYNC_STATE=/tmp/ros-test/target-state
export REPO_OFFLINE_SYNC_TARGET_CONFIG=/tmp/ros-test/target.json
```

找到最新包：

```bash
PKG=$(ls -dt /tmp/ros-test/media/offline-update/inbox/pkg-* | head -1)
echo "$PKG"
```

## 10.5 手动执行目标核心更新

```bash
PYTHONPATH=src python3 - "$PKG" <<'PY'
import sys
from pathlib import Path

from repo_offline_sync.media import verify_ready_package, local_copy
from repo_offline_sync.target_engine import apply_package

package = Path(sys.argv[1])
verify_ready_package(package)
print("READY 校验成功")

local = local_copy(package, "manual-test-v1")
print("已复制到目标 staging:", local)

result = apply_package(local)
print("更新结果:")
print(result)
PY
```

预期 `status = success`。

检查：

```bash
cat /home/<你的用户名>/ros-test-target/app.txt
ls -l /home/<你的用户名>/ros-test-target
```

应看到 `version 1`，且目标目录是指向 `/tmp/ros-test/target-state/releases/<repo-id>/<commit>` 的 symlink。

---

# 11. 本地手动测试：v2 纯增量更新

## 11.1 把 v1 result 模拟写回介质

```bash
cp /tmp/ros-test/target-state/results/*.json \
  /tmp/ros-test/media/offline-update/results/
```

## 11.2 新 commit

```bash
cd /tmp/ros-test/repo

echo "version 2" > app.txt
git add app.txt
git commit -m "v2"
```

## 11.3 再次打包

```bash
cd ~/RepoOfflineSyncTool
./package_update.sh /tmp/ros-test/repo
```

这次“完整备用包”选择 `2. 否`。

检查 manifest：

```bash
PKG=$(ls -dt /tmp/ros-test/media/offline-update/inbox/pkg-* | head -1)
python3 -m json.tool "$PKG/manifest.json"
```

应看到类似：

```json
"kind": "incremental",
"base_commit": "v1 commit",
"target_commit": "v2 commit",
"full_fallback_included": false
```

再次手动 apply：

```bash
PYTHONPATH=src python3 - "$PKG" <<'PY'
import sys
from pathlib import Path
from repo_offline_sync.media import verify_ready_package, local_copy
from repo_offline_sync.target_engine import apply_package

package = Path(sys.argv[1])
verify_ready_package(package)
local = local_copy(package, "manual-test-v2")
print(apply_package(local))
PY
```

检查：

```bash
cat /home/<你的用户名>/ros-test-target/app.txt
```

预期 `version 2`。

---

# 12. 测试 needs-full-bundle

准备一个只有 incremental bundle 的 package，然后把目标 state 清空：

```bash
rm -rf /tmp/ros-test/fresh-state
export REPO_OFFLINE_SYNC_STATE=/tmp/ros-test/fresh-state
```

应用增量包，预期：

```text
status = needs-full-bundle
```

并且不激活 destination、不创建无意义的空 managed bare repo、不破坏现有程序。

---

# 13. 测试包篡改检测

```bash
cp -a "$PKG" /tmp/ros-test/tampered
echo X >> /tmp/ros-test/tampered/bundles/*.bundle
```

运行：

```bash
PYTHONPATH=src python3 - <<'PY'
from pathlib import Path
from repo_offline_sync.media import verify_ready_package
verify_ready_package(Path('/tmp/ros-test/tampered'))
PY
```

预期直接失败，不能进入更新阶段。

---

# 14. 测试错误 pairing token

把测试 target config 中 token 临时改成其他 32 位十六进制值，再应用合法 package。

预期：

```text
package pairing token does not match this target
```

并且当前 active symlink 不发生变化。

---

# 15. 测试 persistent path

profile 配置：

```json
"persistent_paths": ["data"]
```

v1 成功后：

```bash
echo "runtime-data" > /home/<user>/ros-test-target/data/runtime.txt
```

升级 v2 后再次 `cat`，仍应得到 `runtime-data`。

---

# 16. 测试 rollback

将 profile 的 health 暂时设置成必失败 action：

```json
"health": [
  {
    "name": "force-health-failure",
    "argv": ["/bin/false"],
    "cwd": ".",
    "env": {},
    "user": "<你的用户>",
    "timeout": 30
  }
]
```

并确保：

```json
"failure_policy": "rollback"
```

生成下一版本并应用，预期 `status = failed-rolled-back`，destination 内容仍然是上一个成功版本。

完成测试后记得移除故意失败的 health action。

---

# 17. 测试 keep-failed-stopped

把策略改成：

```json
"failure_policy": "keep-failed-stopped"
```

仍使用 `/bin/false` health。预期 `status = failed-preserved`，失败 release 被保留；如果配置了 service unit，服务应保持停止。

---

# 18. 测试 dirty 旧目录接管

准备一个普通 Git 工作目录：

```bash
mkdir -p /tmp/old-app
cd /tmp/old-app
git init
git config user.name test
git config user.email test@example.com

echo committed > legacy.txt
git add legacy.txt
git commit -m legacy

echo dirty > legacy.txt
echo untracked > untracked.txt
```

把 package destination 指向该目录并应用。

预期：

- 原目录保存为 `<destination>.pre-offline-sync-<timestamp>`；
- tracked dirty 内容仍在；
- nonignored untracked 文件仍在；
- updater state 下产生 backup metadata/archive；
- 新 destination 变为 managed release symlink。

---

# 19. 测试 recursive submodule

构造：

```text
root
└── submodule A
    └── submodule B
```

打包前务必：

```bash
git submodule update --init --recursive
```

如果用本地路径构造测试仓库，Git 新版本可能要求：

```bash
git -c protocol.file.allow=always submodule update --init --recursive
```

如果二级 submodule 目录只是存在但未真正初始化，packager 应拒绝，而不是继续打包。

成功更新后分别检查两级 submodule 文件内容，应对应 manifest 记录的精确 gitlink commit。

---

# 20. 测试 Git LFS

正常项目建议先：

```bash
git lfs pull
```

packager 会扫描目标 commit tree 中的 LFS pointer，验证 OID/size 和本地 `.git/lfs/objects/...` 对象，再把 exact-target object 放入 package。目标 release 中 pointer 会被替换为真实 bytes。

目标上可执行：

```bash
sha256sum <destination>/<lfs-file>
```

结果应与 pointer 中 OID 一致。

---

# 21. 测试 receipt 回写和重复插盘

真实流程：

1. 插盘执行更新；
2. 更新完成，result 位于 target `pending-results`；
3. 再插一次同一 U 盘；
4. scanner 将 pending result 写入 `offline-update/results/`；
5. 已经成功安装的相同 generation 和更旧 package 被跳过；
6. 不重复排队同一 result；
7. 不因为 U 盘保留旧 package 而自动降级。

检查：

```bash
sudo /usr/libexec/repo-offline-sync/status
find /你的U盘/offline-update/results -maxdepth 1 -type f -print
```

---

# 22. 测试断电/中断恢复

事务状态在：

```text
/var/lib/repo-offline-sync/transactions/
```

当前轻量恢复策略：

- activation 之前中断：标记 aborted/rejected，不触碰 active；
- activation 边界之后、策略为 rollback：恢复 previous destination；
- rollback 本身失败：标记 `recovery-failed`。

真实设备上应先在测试环境于不同阶段强制杀掉 updater，再启动 scanner 观察 transaction phase 与 active symlink。不要第一次就在生产目录验证断电恢复。

---

# 23. 真机 systemd / udev / U盘测试

这部分必须在真正的 Ubuntu 22.04 目标机上完成，容器不能完整模拟真实 block-device/udev 生命周期。

## 23.1 安装检查

```bash
sudo ./install_target.sh
systemctl status repo-offline-sync-boot.service
systemctl cat repo-offline-sync-boot.service
systemctl cat repo-offline-sync-scan.service
cat /etc/udev/rules.d/99-repo-offline-sync.rules
```

## 23.2 手动触发 scanner

```bash
sudo systemctl start repo-offline-sync-scan.service
journalctl -u repo-offline-sync-scan.service -n 100 --no-pager
```

## 23.3 插入真实 U 盘

```bash
journalctl -f -u repo-offline-sync-scan.service
```

预期：

- 未标记 U 盘被忽略；
- `VTOYEFI` 被忽略；
- 带 marker 的数据分区被处理；
- package 在 U 盘正常卸载之后才执行；
- 不使用 force/lazy unmount；
- 更新成功后 active 版本正确。

## 23.4 再次插盘获取 receipt

更新完成后拔出并重新插入同一介质，确认：

```bash
find /mount/path/offline-update/results -maxdepth 1 -type f -print
```

并检查 target pending-result 已被清掉。

---

# 24. 常见状态说明

| status | 含义 |
|---|---|
| `success` | 更新成功并提交 |
| `no-op` | 无需变更 |
| `needs-full-bundle` | 本地不具备增量 prerequisite，需要 full bundle |
| `failed-rolled-back` | 新版本失败，已恢复旧版本 |
| `failed-preserved` | 新版本失败，按策略保留现场并停止服务 |
| `recovery-failed` | updater 自身恢复失败，需要人工处理 |
| `rejected` | 更新在激活之前被拒绝/中止 |

`generation` 表示主机生成 package 的代数，不是“成功安装次数”。失败或丢弃的 package 也可能使下一次 generation 增加。

---

# 25. 常见问题排查

## 25.1 `Permission denied: '/用户名'`

通常是 destination 写成：

```text
/shm-white/app
```

正确应为：

```text
/home/shm-white/app
```

检查 package：

```bash
python3 - <<PY
import json
from pathlib import Path
m=json.loads((Path("$PKG")/"manifest.json").read_text())
print(m["destination"])
print(m["service_user"])
PY
```

## 25.2 `source repository is dirty`

```bash
git status
```

提交、stash 或删除本地修改后重新打包。

## 25.3 `required submodule is not initialized locally`

```bash
git submodule update --init --recursive
```

## 25.4 `missing Git LFS object ...`

联网开发机上：

```bash
git lfs pull
```

确认对象存在后重新打包。

## 25.5 `needs-full-bundle`

重新打包，并在“是否同时加入完整备用包？”选择 `1. 是`。

## 25.6 第二次更新仍然提示高风险路径

正常 `/home/<service-user>/...` 即使已经是指向 managed release 的 active symlink，也不应该被判为高风险。如果仍提示，确认已应用包含 safe-destination symlink 修复的最新增量 diff。

---

# 26. 清理本地测试环境

```bash
rm -rf /tmp/ros-test
```

如果使用了 `/home/<user>/ros-test-target`，确认它只是测试 symlink 后再删除：

```bash
rm /home/<user>/ros-test-target
```

不要对真实业务目录直接执行清理命令。

---

# 27. 当前沙盒手测结果

在不运行 pytest/Ruff/Pyright 的前提下，当前代码通过了以下手动功能冒烟场景：

- safe destination 与 active symlink 判断；
- fresh target full install；
- recursive 两级 submodule；
- exact-target LFS materialization；
- receipt -> pure incremental bundle -> 第二次升级；
- persistent data 跨版本保留；
- READY/inventory 篡改拒绝；
- 错误 pairing token 拒绝；
- fresh target + incremental-only -> `needs-full-bundle`；
- `needs-full-bundle` 不创建空 managed repo；
- health failure -> rollback；
- `keep-failed-stopped`；
- dirty unmanaged destination 接管与 backup archive；
- pending result replay；
- activation 中断后的 rollback recovery；
- scanner copy/select/apply/result queue；
- 同一 U 盘第二次插入时 receipt 回写且不重复应用/不重复排队；
- installer/repair/uninstall 文件流程（systemctl/udevadm 使用隔离 stub）。

尚需在真实 Ubuntu 22.04 目标机验证的外部环境行为：

- 真正 systemd PID 1 下的服务启停；
- 真实 udev block-device add event；
- 真 U 盘 ext4/exFAT/NTFS 插拔；
- Ventoy 数据分区 + `VTOYEFI` 共存；
- 真实业务项目的 build/service/health action；
- 实际断电而非状态模拟。

这些属于环境/硬件集成验证；当前本地核心更新链路已经手动跑通。
