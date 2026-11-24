# AI 与机器人常驻运行

本项目使用 `scripts/axiom-runtime` 适配当前安装的 `@axiom/cli`。源文件保存在项目中，不修改 `node_modules`；`npm install` 后仍通过同一入口生效。Node.js 需支持 `node:module.register`（Node 20.6+；本次验证使用 26.7）。原生 `axiom ui` 仍是上游的前台命令，项目的 `npm run ui` 才提供常驻和恢复能力。

```bash
export AXIOM_UI_PORT=17621
npm run ui
npm run ui:status
npm run ui:logs
npm run ui:restart
```

`AXIOM_UI_PORT` 未设置时默认 17621，必须是 1–65535 的整数。启动器向后台和 AI 注入实际 `AXIOM_UI_PORT` / `AXIOM_UI_URL`。外部终端自行 export，不扫描或猜端口。固定相同工作区与端口重启，才能沿用现有 SurrealDB（上游按工作区+端口隔离数据库）。更换端口不是数据库迁移。

`AXIOM_OPENCODE_PORT` 未设置时由上游随机分配空闲端口；设置后 AI 服务固定监听该端口（被占用时启动失败并写入 `ai.log`），仅供本机调试直连。

### 多环境预设

同一套托管逻辑按端口隔离出多个互不干扰的实例，端口分配集中在 `scripts/axiom-runtime/preset.mjs`：

| 命令 | UI 端口 | opencode 端口 |
| --- | --- | --- |
| `npm run ui` | 17621 | 随机 |
| `npm run ui:ll` | 17622 | 19622 |
| `npm run ui:ll-test` | 17623 | 19623 |
| `npm run ui:pl` | 17624 | 19624 |
| `npm run ui:pl-test` | 17625 | 19625 |

预设命令支持与 `ui` 相同的动作，例如 `npm run ui:ll -- status` / `-- restart` / `-- stop` / `-- stop-all`；不传动作时为 `start`（带 `--gossip --bootstrap`）。

- `ui`：后台托管，重复启动复用同一个托管实例。关闭启动终端不会停止服务。端口被未知实例占用时拒绝接管。已托管时也会实测端口后才报成功；supervisor 活着但端口打不开（UI 崩溃循环）且代码未变时直接失败并打印端口探测与日志尾部，退出码为 1；若运行中的 supervisor 比当前 `scripts/axiom-runtime` 旧（代码更新后它只会重复旧的启动参数），则自动停掉旧进程并用当前代码重新启动，不需要先 `ui:stop-all`。
- 启动锁 `ui.lock`：旧 supervisor 被强杀（Windows 上 `SIGKILL` 等于 `TerminateProcess`，不会执行清理）会残留。新 supervisor 发现 owner 已退出会接管；若锁仍被存活进程占用，启动会在数秒内失败并直接报出锁的 owner，不再空等 45 秒超时。
- `ui:restart`：仅重启 UI 宿主；独立 AI 进程继续执行，恢复后接回同一会话。supervisor 处于崩溃循环或已过期，或 UI 未托管时，改为替换 supervisor 后重新启动（保留原来的启动参数）。
- `ui:stop`：停止 UI 和它的自动重启，保留独立 AI 执行。再次 `ui` 后补收结果。
- `ui:stop-all`：明确停止 UI 和 AI，会中断正在执行的 AI。
- `ui:status`：输出端口、进程存活、启动锁 owner、<IP> / ::1 端口探测、HTTP 探测和日志路径与尾部，不输出认证密码；`--json` 输出机器可读字段。
- `ui:logs`：打印 `ui.log` / `ai.log` 的最后若干行，`--lines=N` 指定行数（默认 40），用于定位启动失败。
- `ui` 启动失败（超时，或起来后 3 秒内连续探测失败）同样以退出码 1 结束，并在终端给出上述诊断信息，不必先去找日志文件。

运行文件位于 `~/.axiom/runtime/<工作区与端口哈希>/`，包括 UI/AI 日志、私有认证连接记录、启动锁和持久结果收件箱。不要删除仍在运行的实例目录。OpenCode 的提示词、会话历史与机器人数据库沿用原本的持久存储。

## Windows 后台窗口

守护进程拉起 UI，以及 UI 宿主调用 Node.js 的子进程接口时，统一使用 `windowsHide: true`。参数、回调、标准输出和错误仍按原接口传递。此设置只作用于经过项目 preload 启动的 Node 宿主，不覆盖独立原生程序（如 OpenCode 自身）内部启动的子进程。

已有 Windows 计划任务若直接启动 PowerShell，即使使用 `-WindowStyle Hidden` 也可能在进程初始化时闪出控制台。可用 PowerShell 7 执行：

```powershell
pwsh -NoProfile -File scripts/axiom-runtime/hide-keepalive-task.ps1 -WhatIf
pwsh -NoProfile -File scripts/axiom-runtime/hide-keepalive-task.ps1
```

脚本只转换已存在的 `Merge-Axiom-Robot-KeepAlive` 单条 PowerShell 动作，改用 Windows Script Host 的隐藏启动入口；可通过 `-TaskName` / `-TaskPath` 指定任务。原触发器、身份和保活命令保持原值，不新建保活任务，不启动或停止 UI/AI。原计划任务 XML 与启动脚本保存在 `%LOCALAPPDATA%\Axiom\launchers`（可用 `-OutputDirectory` 更改），不包含在 Git 中。保活命令仍等待完成并返回原退出码；已使用 `wscript.exe` 的任务不重复修改。

仓库代码在下次启动对应宿主时生效；仅提交或创建 MR 不会改变当前运行进程。`scripts/axiom-runtime` 改动后，`ui` 在发现旧 supervisor 正在崩溃循环、或 `ui:restart` 时会把旧进程替换成当前代码（supervisor 记录了启动时的脚本指纹），不必手动 `ui:stop-all`。不要为了应用窗口设置而中断正在运行的机器人。

## 结果与恢复

机器人派发时先保存执行标识，再 fork；宿主适配层在发送提示词前保存会话 ID。完成回报通过 `report.mjs` 原子写入并 fsync 到本地收件箱，UI 离线也能成功。HTTP 回报与本地回报使用同一份不可变收据；新任务必须携带 attempt，旧任务没有 attempt 时保留兼容。旧执行不能完成新一轮任务，同次执行的冲突结果拒绝覆盖。

短暂网络、AI 服务或数据库故障保留 running 状态并重连。宿主启动时先接回原 running 会话；暂停机器人的当前任务仍被监听，但不会派发下一条。命名队列从持久 running 记录识别已有执行者，恢复期间不插队。持久数据库不可用时拒绝加载机器人，不降级为重启会丢失的内存。

AI 进程崩溃后，守护进程重新拉起 AI；原会话已空闲而没有回报时，最多发送一次核对/恢复提示。无法确定外部操作结果时进入 blocked 等待确认，不盲目重跑。任意外部操作无法保证 exactly-once，发消息、提交、支付等仍需操作本身提供幂等或结果核查。整机断电期间不能继续计算；机器恢复后需启动 `npm run ui`。

## 已有前台服务的首次迁移

旧的前台 UI 会在退出时结束其 AI 子进程。迁移前必须确认所有旧 AI 会话与机器人当前任务已结束，不能直接在任务运行中 kill 旧服务。新启动器会拒绝接管占用端口的旧服务。完成迁移后，今后的 UI 重启才由独立 AI 托管保护。

## 验证

```bash
npm run test:ai-runtime
```

测试使用确定性 HTTP 执行器，不调用真实模型、不发外部消息。集成测试启动当前安装的真实 Axiom 宿主与真实 SurrealDB，在任务执行中 SIGKILL UI 宿主，验证自动拉起、AI PID/会话不变、结果落库且 fork 次数不增加。
