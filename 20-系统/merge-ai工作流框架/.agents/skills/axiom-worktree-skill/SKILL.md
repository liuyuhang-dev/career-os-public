---
name: axiom-worktree-skill
description: Axiom Web 插件：Git worktree 管理 + 每个 worktree 独立 UI 服务。Use when 用户提到 worktree、git worktree、多分支并行开发、为某个工作区单独启动 axiui/ui 服务、独立端口、独立 opencode、查看服务状态或服务日志、批量启动/停止 worktree 服务、axiom-worktree。
---

# Axiom Worktree Skill

通过 `axiom ui` 加载的 **git worktree 管理**插件：列出 / 新建 / 移除 worktree，并为每个 worktree 以**不同端口**启动一个独立的 `axiom ui`（内含该工作区独立的 opencode），支持状态检测、启停/重启、批量操作与实时日志查看。

- 插件名：`axiom-worktree`，左侧入口「工作区」
- 页面：`/axiom-worktree/iframe`
- API 前缀：`/axiom-worktree/api`
- 数据目录：**机器用户目录**（默认 `~/.axiom/worktree`），**不写入工程目录、不进入 git**
- 默认 worktree 根目录：主仓库同级 `<仓库名>-worktrees`（可在界面「设置」或 API 中修改）

> 服务只监听 `<IP>`，插件未登记 `app.external`，不会随 iroh 对外暴露。启动服务等价于在本机执行 `axiom ui`，请只对本机可信仓库使用。

## 功能

- **Worktree 管理**
  - 列表展示：分支、HEAD、最近提交、工作区干净/改动数、主工作区 / 当前界面 / detached / 锁定 / 目录缺失等标记
  - 新建：新建分支（`git worktree add -b`）或检出已有分支，可自定义目录与起始点；创建完成后在列表里点「启动」再拉起独立 UI
  - 移除：可选强制移除（丢弃未提交改动）、同时删除分支（`git branch -D`）；主工作区与当前界面所在的 worktree 禁止移除
  - 清理失效：`git worktree prune -v`，并停止 / 删除已失效 worktree 的服务记录
- **独立 UI 服务**
  - 每个 worktree 一个 `axiom ui --port <port> --no-open`（在 worktree 目录内启动），端口从「起始端口」开始自动分配，避免冲突
  - 每个服务会拉起**自己的 opencode**（`axiom ui` 内置行为），互不共享会话 / 上下文
  - 支持单个/全部启动、停止、重启；停止时清理整个进程组（含 opencode 子进程）
  - 服务以 **detached** 方式运行：关闭当前 axiom ui 后服务继续存活，可随时回到任意界面停止
- **状态检测**
  - 双重判定：进程 pid 存活 + 端口 HTTP 探测（`GET /api/plugins`），再读取该服务的 `GET /api/opencode` 展示 AI 对话是否就绪
  - 状态：`running` / `starting`（进程存活但端口未就绪，最多等待 60 秒）/ `stopped` / `error`（含失败原因）
  - 停止前会校验 pid 对应的命令行包含本服务的 `--port`，避免系统复用 pid 后误杀无关进程
- **日志**
  - 服务 stdout/stderr 追加写入 `<数据目录>/logs/<id>.log`，界面右侧抽屉实时刷新（默认 2 秒），支持自动刷新开关、清空、复制
  - 读取按行尾截取（默认 300 行，界面 800 行），单次最多回传末尾 512 KiB；单文件超过 5 MiB 时启动前轮转为 `.log.1`

## 数据目录（机器用户目录，不入 git）

默认：`~/.axiom/worktree`

覆盖：环境变量 `AXIOM_WORKTREE_DIR`

各仓库按 `sha1(git-common-dir)` 前 12 位隔离（同一仓库的多个 worktree 共享同一份 registry，方便跨界面管理）：

```
~/.axiom/worktree/
└── repos/<repo-key>/
    ├── registry.json        # 设置（root / portBase）+ 每个 worktree 的服务记录（端口 / pid / 日志路径）
    ├── repo.json            # 仓库信息（root / mainRoot / commonDir）
    ├── locks/<id>.lock      # 启停操作锁（跨进程互斥，自动过期）
    └── logs/<id>.log        # 服务输出
```

设置项：

| 设置 | 默认值 | 说明 |
|---|---|---|
| `root` | 主仓库同级 `<仓库名>-worktrees` | 新建 worktree 的默认父目录 |
| `portBase` | `17630`（或环境变量 `AXIOM_WORKTREE_PORT_BASE`） | 服务端口起始值，最多向后扫描 500 个端口 |

> 端口与 pid 记录在 registry 中，重启 `axiom ui` 后会继续复用原端口；服务进程本身不依赖宿主存活。

## 服务命令行与依赖

启动服务时会按以下顺序选择 axiom CLI（在 worktree 目录内执行）：

1. `<worktree>/bin/axiom.js`、`<worktree>/app/dist/index.html` 与 `<worktree>/node_modules/express` 都存在 → 用该 worktree 自己的源码（改动 `lib/*` 也会生效）
2. `<worktree>/node_modules/.bin/axiom` → worktree 自己安装的 axiom
3. 当前宿主的 axiom 入口（最常见；clone 出来的 worktree 无需 `npm install` 也能起 UI）
4. 主仓库 `<mainRoot>/node_modules/.bin/axiom`
5. PATH 中的 `axiom`

注意：

- 只克隆代码的 worktree 默认复用宿主 CLI，**worktree 内的 `skills/` 会被独立扫描**（这正是并行开发要验证的内容）
- 新建 / 启动时会自动把主仓库的 `node_modules` 软链到 worktree（仅在 worktree 没有该目录时），避免插件 `import` 的第三方包全部失败；worktree 若需要不同版本的依赖，删掉该软链后自行 `npm install`
- worktree 里只会有**已提交**的文件：未提交 / 未跟踪的 skill（例如本地新写的插件）不会出现在 worktree 中，需要先提交到主仓库分支
- 每个服务的 opencode 由该服务自己的 `axiom ui` 拉起，使用该 worktree 作为工作目录
- **iroh / gossip 跟随宿主**：宿主以 `axiom ui --gossip --bootstrap <ids>`（或 `AXIOM_IROH_GOSSIP=1`、`AXIOM_IROH_BOOTSTRAP`）启动时，子 UI 会带上同样的 gossip 配置（写入子进程环境变量），与宿主加入同一 gossip 网络；宿主只开 mDNS 时子 UI 也保持 mDNS

## HTTP API（界面与 AI 共用）

约定：所有请求只允许本机访问；返回 JSON；失败时 HTTP 4xx/5xx + `{ "error": "..." }`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/state` | 仓库信息、设置、worktree 列表与每个服务状态（界面轮询用） |
| `GET` | `/api/meta` | 插件版本、数据目录、端口、接口清单 |
| `GET` | `/api/branches` | 本地分支列表（含最近提交） |
| `POST` | `/api/worktrees/create` | `{ branch, base?, createBranch?=true, path? }`（新建不自动启动服务） |
| `POST` | `/api/worktrees/remove` | `{ path, force?, deleteBranch? }` |
| `POST` | `/api/worktrees/prune` | 清理失效 worktree 与服务记录 |
| `POST` | `/api/services/start` | `{ path, port? }` |
| `POST` | `/api/services/stop` | `{ path }` |
| `POST` | `/api/services/restart` | `{ path }` |
| `POST` | `/api/services/start-all` | `{ paths? }`（缺省为所有未运行的 worktree） |
| `POST` | `/api/services/stop-all` | 停止本插件管理的全部服务 |
| `POST` | `/api/services/reap` | 停止并删除非 worktree 的残留服务记录 |
| `GET` | `/api/services/logs?path=<path>&lines=300` | 读取日志末尾，返回 `{ text, size, file, truncated }` |
| `DELETE` | `/api/services/logs?path=<path>` | 清空日志文件 |
| `POST` | `/api/settings` | `{ root?, portBase? }` |

### AI 调用示例

宿主已向 opencode 子进程注入 `$AXIOM_UI_URL`，直接调用本地接口：

```bash
# 查看所有 worktree 与服务状态
curl -s "$AXIOM_UI_URL/axiom-worktree/api/state" | jq '.worktrees[] | {branch, path, port: .service.port, status: .service.status}'

# 为某分支创建 worktree（创建后不会自动启动服务）
curl -s -X POST "$AXIOM_UI_URL/axiom-worktree/api/worktrees/create" \
  -H 'Content-Type: application/json' \
  -d '{"branch":"feature/x","base":"main"}'

# 再按路径启动它的独立 UI（端口自动分配）
curl -s -X POST "$AXIOM_UI_URL/axiom-worktree/api/services/start" \
  -H 'Content-Type: application/json' \
  -d '{"path":"/abs/path/to/feature-x"}'

# 启动 / 停止 / 重启
curl -s -X POST "$AXIOM_UI_URL/axiom-worktree/api/services/start" -H 'Content-Type: application/json' -d '{"path":"/abs/worktree"}'
curl -s -X POST "$AXIOM_UI_URL/axiom-worktree/api/services/stop"  -H 'Content-Type: application/json' -d '{"path":"/abs/worktree"}'
curl -s -X POST "$AXIOM_UI_URL/axiom-worktree/api/services/restart" -H 'Content-Type: application/json' -d '{"path":"/abs/worktree"}'

# 查看日志末尾
curl -s "$AXIOM_UI_URL/axiom-worktree/api/services/logs?path=/abs/worktree&lines=100" | jq -r '.text'
```

`/api/state` 中单个 service 的主要字段：

```json
{
  "status": "running | starting | stopped | error",
  "port": 17630,
  "url": "http://<IP>:17630",
  "pid": 12345,
  "pidAlive": true,
  "hosted": false,
  "startedAt": 1730000000000,
  "uptimeMs": 123456,
  "opencode": { "enabled": true, "error": null },
  "cli": "宿主 axiom",
  "logFile": "~/.axiom/worktree/repos/<key>/logs/wt-xxx.log",
  "logSize": 4096,
  "lastExit": null,
  "error": null
}
```

- `hosted: true` 表示该服务就是当前页面所在的 UI 进程（停止/重启会让当前页面断开）
- `opencode.enabled` 为 false 时 `opencode.error` 给出原因（如模型凭据缺失、opencode 启动失败）

## 使用建议

- **并行开发**：每个 worktree 一个独立 UI + opencode，分别在各自页面里使用 AI 对话，默认不共享会话
- **端口规划**：多个仓库同时开启插件时，给不同仓库配置不同的「起始端口」，避免互相挤占
- **清理**：长期不用的服务先在界面「停止」，再移除 worktree；直接删除目录后用「清理失效」回收记录
- **排障**：服务起不来时先看日志抽屉；常见原因是端口被占用、worktree 缺少依赖、CLI 路径不存在
- 修改本插件代码后需要重启对应的 `axiom ui`（服务端的插件代码在宿主进程内，日志抽屉只反映子服务输出）
- **当前界面所在服务**：不能在自身界面里重启（请求还没返回进程就被结束了），请到其它 worktree 的界面操作；「全部停止」会自动跳过当前界面服务
- **可移除范围**：主工作区、当前界面所在 worktree、目录已缺失的 worktree 都不能通过「移除」删除（后者用「清理失效」）
- **并发启动**：多个界面同时给不同 worktree 分配端口时，端口选择为尽力而为（先到先得）；极少情况下需要重试一次启动
