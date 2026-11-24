---
name: axiom-scheduler-skill
description: Axiom Web 插件：本机定时任务管理与脚本执行。Use when 用户提到 定时任务、定时脚本、cron、crontab、周期任务、每隔 N 秒/分钟运行、每天几点执行、单次定时、运行日志、停止任务、axiom-scheduler、scheduler、AI 创建定时任务。
---

# Axiom Scheduler Skill

通过 `axiom ui` 加载的**本机定时任务**插件：管理脚本，按固定间隔 / cron / 单次时间执行，查看运行日志，支持界面操作与面向 AI 的 HTTP API。

- 插件名：`axiom-scheduler`，左侧入口「定时任务」
- 页面：`/axiom-scheduler/iframe`
- API 前缀：`/axiom-scheduler/api`（AI API 为 `/axiom-scheduler/api/ai/...`）
- 数据目录：**机器用户目录**（默认 `~/.axiom/scheduler`），**不写入工程目录、不进入 git**

> 脚本以当前登录用户权限执行，等价于本机 shell 能力。插件只监听 `<IP>`，且未登记 `app.external`，不会随 iroh 对外暴露。

## 功能

- **任务管理**：新建 / 编辑 / 启用停用 / 删除；标题、描述、脚本语言、工作目录、环境变量、超时、成功通知。
- **三种调度**
  - `interval`：固定间隔（秒，最少 10）
  - `cron`：5 段标准表达式（分 时 日 月 周，本地时区）
  - `once`：单次时间点，执行后自动停用
- **脚本执行**：Bash、Node.js、Python 3、PowerShell、Windows CMD；捕获 stdout / stderr、退出码、耗时、超时与手动停止。
- **运行记录**：每个任务保留最近 30 条，单条输出最多 128 KiB；可查看命令、工作目录、stdout / stderr。
- **手动操作**：立即运行、停止、清空运行记录；同一任务不会并发执行。
- **通知中心**：失败自动写入 `ctx.notify`（站内 + 浏览器通知）；可选成功也通知。
- **面向 AI**：稳定的 `/api/ai/*` 接口 + `GET /api/ai/manifest` 自描述清单（含 schema、示例、数据目录、调度器状态）。

## 数据目录（机器用户目录，不入 git）

默认：`~/.axiom/scheduler`

覆盖方式（按优先级）：

1. 环境变量 `AXIOM_SCHEDULER_DIR`
2. 启动目录或插件 `package.json` 的 `axiom.scheduler.dir`，也兼容 `axiom.scheduler` 字符串
3. 默认 `~/.axiom/scheduler`

```
~/.axiom/scheduler/
├── scheduler.lock.json          # 调度锁（pid / instance / port），避免多实例重复执行
├── tasks/<taskId>.json          # 任务元信息（不含脚本正文）
├── scripts/<taskId>.<ext>       # 脚本正文（sh/js/py/ps1/bat，权限 0600）
└── runs/<taskId>/<runId>.json   # 每次运行的命令、输出、状态
```

要点：

- 任务、脚本与日志全部在用户目录；删除任务会一并删除其脚本与运行记录，**不可恢复**。
- 同一数据目录可被多个 `axiom ui` 实例打开，但只有持有 `scheduler.lock.json` 的实例执行定时（其余实例只读展示 + 可手动执行）。持有者退出后，其他实例会自动接管；PID 已失效的锁会被视为过期。
- 每个实例每 5 秒从磁盘同步一次任务，另一个实例新建/修改/删除的任务也能被调度器感知。

## 调度方式

| type | 字段 | 说明 |
|---|---|---|
| `interval` | `every`（秒，10 - 2592000） | 保存/上次执行完成后重新计时 |
| `cron` | `cron`（5 段） | 本地时区；支持 `*`、`a`、`a-b`、`*/n`、`a-b/n`、逗号组合 |
| `once` | `at`（毫秒时间戳或可解析时间字符串） | 执行一次后自动 `enabled=false`；停机期间错过会在下次启动后补跑一次 |

cron 示例：

| 表达式 | 含义 |
|---|---|
| `*/5 * * * *` | 每 5 分钟 |
| `0 * * * *` | 每小时整点 |
| `0 9 * * *` | 每天 09:00 |
| `0 9 * * 1` | 每周一 09:00 |
| `0 9 1 * *` | 每月 1 日 09:00 |
| `*/15 9-18 * * mon-fri` | 工作日 9:00-18:59 每 15 分钟 |

- 星期：`0`/`sun` 为周日，`1`/`mon` 为周一，`7` 也按周日处理。
- 日与星期同时受限时，按 cron 标准取「或」。
- `once` 的执行时间可用 `2026-01-01T09:00:00+08:00`、`2026-01-01 09:00` 或毫秒时间戳。

## 脚本与运行

| language | 扩展名 | 解释器 |
|---|---|---|
| `bash` | `.sh` | `bash` |
| `node` | `.js` | 当前 Node 可执行文件（`process.execPath`） |
| `python` | `.py` | `python3`（Windows 为 `python`） |
| `powershell` | `.ps1` | `pwsh`（Windows 为 `powershell.exe`） |
| `cmd` | `.bat` | `cmd.exe /d /s /c` |

- `cwd` 留空使用 `axiom ui` 启动目录；支持 `~` 与绝对路径，保存时校验目录存在。
- `env` 为字符串键值对，运行时合并到父进程环境；额外注入 `AXIOM_SCHEDULER_TASK_ID`、`AXIOM_SCHEDULER_TASK_TITLE`、`AXIOM_SCHEDULER_RUN_ID`。环境变量不会写入运行日志。
- `timeoutSec` 默认 600，超时先 `SIGTERM`，3 秒后 `SIGKILL`（进程组）。
- 运行中可调用停止接口（同样先 `SIGTERM` 后 `SIGKILL`）。
- 同一调度器实例内，任务运行期间再次触发（定时或手动）会被拒绝（HTTP 409）；多实例共享数据目录时，建议只在调度实例中手动执行，避免与定时执行重叠。
- 退出码 `0` 为 `success`，否则 `failed`；另有 `timeout` / `stopped` / `error`。
- 任务失败自动通知：`level=error`、`browser=true`、`url=/axiom-scheduler`；`notifyOnSuccess=true` 时成功也通知。

## 界面

- 左侧：任务列表（搜索、启用状态、下次执行、上次结果）+ 新建按钮。
- 主区：任务信息、调度配置（含常用预设）、运行配置、脚本编辑器、操作按钮、运行记录与输出详情。
- 底部状态栏：任务数 / 启用数、运行中数量、调度器状态（本实例 / 只读及持有者 pid）、数据目录、刷新时间。
- 遵守 `axiom-web-skill` 规则：不自绘标题栏、内容自适应铺满 iframe、底部状态栏。
- 有未保存修改时轮询不会覆盖表单；切换任务会提示确认。

## API（界面与 AI 共用）

Base：`$AXIOM_UI_URL/axiom-scheduler`（等价 `http://<IP>:${AXIOM_UI_PORT}/axiom-scheduler`）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/meta` | 数据目录、语言、调度类型、锁与调度器状态 |
| GET | `/api/tasks` | 任务列表 + 调度器状态 + `dataDir` |
| POST | `/api/tasks` | 新建任务，返回 `{ task, script }` |
| GET | `/api/tasks/:id` | 单个任务 + 脚本正文 |
| PUT | `/api/tasks/:id` | 修改任务（只传需要变更的字段） |
| DELETE | `/api/tasks/:id` | 删除任务、脚本与运行记录 |
| POST | `/api/tasks/:id/run` | 立即运行；默认 `202 { runId }`，`?wait=1` 或 body `{wait:true}` 等待结束 |
| POST | `/api/tasks/:id/stop` | 停止正在运行的任务 |
| GET | `/api/tasks/:id/runs?limit=30` | 运行记录摘要（最新在前） |
| GET | `/api/tasks/:id/runs/:runId` | 单条运行记录（含 stdout/stderr） |
| DELETE | `/api/tasks/:id/runs` | 清空运行记录 |

任务对象核心字段：

```json
{
  "id": "tsk_xxx",
  "title": "每日备份",
  "description": "",
  "enabled": true,
  "language": "bash",
  "schedule": { "type": "cron", "cron": "0 9 * * *" },
  "cwd": "",
  "env": { "FOO": "bar" },
  "timeoutSec": 600,
  "notifyOnSuccess": false,
  "scriptFile": "scripts/tsk_xxx.sh",
  "nextRunAt": 1790000000000,
  "lastRun": { "id": "run_xxx", "status": "success", "startedAt": 1789990000000 }
}
```

请求错误统一返回 `{ "error": "..." }`：`400` 参数/调度表达式非法，`404` 任务或运行记录不存在，`409` 任务运行中，`413` 请求体过大。

## 面向 AI 的 API

1. 先读清单：`GET /api/ai/manifest`，返回端点表、任务 schema、语言、调度说明、示例、`dataDir`、当前调度器状态。
2. 任务 CRUD 与运行接口是 `/api/tasks/*` 的稳定别名：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/ai/manifest` | 能力清单（建议每次会话先读） |
| GET | `/api/ai/tasks` | 列出任务 |
| POST | `/api/ai/tasks` | 新建任务 |
| GET | `/api/ai/tasks/:id` | 读取任务与脚本 |
| PUT | `/api/ai/tasks/:id` | 修改任务 |
| DELETE | `/api/ai/tasks/:id` | 删除任务 |
| POST | `/api/ai/tasks/:id/run` | 运行（AI 触发记为 `trigger=ai`；`?wait=1` 同步等结果） |
| POST | `/api/ai/tasks/:id/stop` | 停止 |
| GET | `/api/ai/tasks/:id/runs` | 运行记录摘要 |
| GET | `/api/ai/tasks/:id/runs/:runId` | 运行详情（含输出） |
| DELETE | `/api/ai/tasks/:id/runs` | 清空运行记录 |

示例（AI 通过宿主注入的 `$AXIOM_UI_URL` 调用）：

```bash
UI="${AXIOM_UI_URL:-http://<IP>:${AXIOM_UI_PORT:-17621}}"
BASE="$UI/axiom-scheduler"

# 清单
curl -s "$BASE/api/ai/manifest"

# 新建：cron 简写、interval、once 均可
curl -s -X POST "$BASE/api/ai/tasks" -H 'Content-Type: application/json' -d '{
  "title": "每日备份",
  "language": "bash",
  "schedule": "0 9 * * *",
  "script": "set -euo pipefail\ntar -czf ~/backup-$(date +%F).tgz ~/data\necho done",
  "timeoutSec": 3600
}'

# 立即运行并等待输出
curl -s -X POST "$BASE/api/ai/tasks/tsk_xxx/run?wait=1" \
  -H 'Content-Type: application/json' -d '{"trigger":"ai"}'

# 查看最近运行
curl -s "$BASE/api/ai/tasks/tsk_xxx/runs?limit=5"

# 停用
curl -s -X PUT "$BASE/api/ai/tasks/tsk_xxx" \
  -H 'Content-Type: application/json' -d '{"enabled":false}'
```

约定：

- `schedule` 既接受对象 `{type,...}`，也接受 cron 字符串、间隔秒数字或时间字符串。
- `script` 必填；修改其他字段时不必重复传脚本。
- 脚本正文保存在 `dataDir/scripts/`，AI 也可直接修改该文件，但推荐走 API（保证任务索引与调度信息一致）。
- 失败/超时会写入宿主通知中心，AI 无需重复通知。
- 运行失败属于正常业务结果：`?wait=1` 仍返回 HTTP 200，结果看 `success` 与 `run.status`。

## 目录结构

```
skills/axiom-scheduler-skill/
├── SKILL.md
└── app/
    ├── package.json            # name=axiom-scheduler，axiom.plugin=plugin.js
    ├── plugin.js               # Express 子应用：页面、任务 API、AI API
    ├── server/
    │   ├── store.js            # 用户目录下的任务 / 脚本 / 运行记录存储
    │   ├── schedule.js         # interval / cron / once 解析与下次执行计算
    │   ├── runner.js           # 脚本执行器（输出、超时、停止）
    │   ├── scheduler.js        # 调度循环、锁、通知、多实例同步
    │   └── util.js             # 路径、请求体、ID、格式化
    └── public/
        └── index.html          # 自包含界面（内联 CSS/JS，无需构建）
```

## 开发与验证

界面与插件均为原生 ESM / 单文件 HTML，无需构建：

```bash
axiom ui
# 左侧「定时任务」→ 新建 → 编辑脚本 → 保存并运行 → 查看运行记录
```

本地校验（不依赖 UI）：

```bash
node --check skills/axiom-scheduler-skill/app/plugin.js
node --check skills/axiom-scheduler-skill/app/server/*.js
# 建议使用临时目录，避免污染真实任务
AXIOM_SCHEDULER_DIR=/tmp/axiom-scheduler-test axiom ui
```

要点：

- 修改插件代码后需重启 `axiom ui` 生效。
- 不要自行打开 `ctx.surreal` 数据库；本插件不依赖共享库。
- 不要把任务/脚本写到工程目录（`cwd` 仅控制脚本运行目录，不改变存储位置）。
