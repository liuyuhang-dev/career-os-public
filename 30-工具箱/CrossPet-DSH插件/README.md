# CrossPet × DeepSeek Harness 插件

> 这是我给开源桌宠 CrossPet 写的 DeepSeek Harness 接入插件，以及配套的接入教程。
> CrossPet 本体是第三方开源项目（作者 lokicorvus，代码 MIT、立绘 CC BY-NC-SA 4.0），这里只保留我写的部分。

## 我做了什么

CrossPet 本身只会读状态文件换动作。我写了一个 Harness 插件，把 Harness 的工作事件翻译成桌宠能读的状态：

- **监听工作事件**：收到消息、调工具、派子任务、一轮结束，写 `<状态目录>/<角色>-state.json`。
- **按模型换角色**：按会话实际使用的模型（DeepSeek / GPT / Claude / Gemini）选角色；当前角色另记在 `deepseek-host.json`，切回 Harness 窗口时按它换。
- **余额查询**：每 1 分钟查一次 DeepSeek 余额（发消息、一轮结束时也查，30 秒内只查一次）；有 API Key 用 Key，没有就用 Harness 里登录的账号。
- **跟着 AI 出现**：Harness 启动、新会话、发消息时，桌宠没开着就自动打开；可在插件配置里关掉 `autostart`。
- **只观察，不改变行为**：插件不修改 DeepSeek 的任何请求或返回。

## 文件

| 文件 | 作用 |
|---|---|
| `dsh_index.js` | 插件主体：事件监听、角色映射、状态落盘、余额轮询 |
| `balance.js` | 余额阈值与摘要计算 |
| `integration_doc.md` | 各 AI 宿主（Cursor、Claude Code、Codex 等）的接入方法 |
| `windows_doc.md` | Windows 端的安装与分卷分发说明 |

## 设计上的几个决定

1. **状态走文件，不走进程间通信。** 桌宠和 Harness 是两个独立进程，用 JSON 文件做单向状态传递，任何一边崩了都不影响另一边。
2. **角色由"实际模型"决定，不由 provider 名决定。** 用户自己起的 provider 名可能千奇百怪，所以先看模型名，再看 provider 名，都认不出就按当前角色演。
3. **余额查询要节流。** 定时 1 分钟一次，事件触发时 30 秒内不重复查，避免把余额接口打爆。
4. **自动拉起有边界。** 用户从菜单主动退出后，到 Harness 下次启动前不再拉起；配置里可以彻底关掉。

## 来源与许可

- CrossPet：https://github.com/lokicorvus/crosspet （代码 MIT）
- 本目录下的插件代码与文档为本人编写，随 CrossPet 的插件机制分发。
