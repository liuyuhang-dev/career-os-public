# 测试主干导图(mini-game)

**这份导图是测试设计的基础。** 主干 = 一个微信/抖音小游戏跑起来的必经环节;
子节点 = 每个环节上真正要判的东西。

**关键约定:每条叶子必须标注「靠什么验证」**(见文末语法)。没标的叶子意味着
这条主干的可测性还没定 —— `scripts/backbone_map.py` 会把它们挑出来。

**用法:这是活的。** 子节点的描述会反过来暴露主干缺什么(比如"重复支付"这条叶子
逼出了故障注入动作 + 状态上报判定),所以改完叶子要回头跑一遍 `backbone_map.py`,
看主干要不要跟着改 —— 这就是"叶子反推主干"的循环。

从 XMind / 幕布 / 语雀导出的缩进 Markdown 可以直接喂给 `backbone_map.py --map`,
不必转成本文件的结构。

**这份导图只列「要判的东西」;执行底座与编排不占叶子位置** ——
`run_case.py`(截图/输入/日志/性能都由它采)、`judge.py`(判定轮)、`judge_live.py`(判定轮真机自检:
真值已知的合成图跑四条判定,验证端点收不收图、答得对不对)、`validate_cases.py`(三道闸门)、
`review_sheet.py`、`pipeline.py`、`check_docs.py`、`selftest.py`、`coordmap.py`、
`scaffold_cases.py`、`import_cases.py`(把既有用例表导进来,属于"用例从哪来"的入口)、
`backbone_map.py` 属于这一层。流程视角看 `pipeline.py` 与
`references/9-lifecycle.md`,不在这张图上。

---

- 上游与静态层(零设备)
  - 配置表自洽:悬空引用/重复主键/必填空/类型范围/枚举越界 · verify=tool:check_config.py
  - 幂等防护缺失:支付/发货/结算入口有没有幂等键与状态守卫 · verify=tool:scan_idempotency.py
- 启动与进入
  - 冷启动耗时不超过基线 120% · verify=evidence:logcat
  - 启动过程不白屏/黑屏/整屏单色 · verify=tool:scan_anomalies.py
  - 公告与隐私协议按需求出现且可关闭 · verify=judge:vision-closed
  - 游戏入口可由 adb 直接打开 · unsupported=adb 无法打开微信/抖音小游戏(无平台 API),只能预置到宿主首页或人工扫码;这是真机自动化最脆弱的一环
  - 点击入口前确实处于宿主首页 · verify=judge:vision-closed
- 登录与授权
  - 授权框与登录结果的界面表现 · judge_only=系统授权框不提供方法获取与操作,自动化前需手工授权一次;之后只看界面结果
  - 授权框出现时不被当成游戏界面继续盲点 · verify=judge:vision-closed
  - 登录失败后的重试入口与文案 · verify=judge:vision-closed
  - 登录态在重启宿主后仍有效 · verify=evidence:state_report
- 热更与资源更新
  - 资源下载完成前不进入游戏(加载页/进度提示) · verify=judge:vision-closed
  - 更新中断后可续传或重试,不死循环 · verify=evidence:state_report
  - 更新失败有明确提示且不白屏 · verify=judge:vision-closed
  - 包版本号/资源版本可采集(与界面显示比对) · verify=action:adb_shell
- 核心循环(对局)
  - 关键路径可走通:进入→开局→操作→结算 · verify=judge:vision-closed
  - 帧率(janky 帧占比)与内存不退化 · verify=evidence:perf
  - 弱网下不卡死、不无限转圈 · verify=evidence:state_report
  - 切后台/来电中断后能恢复 · verify=action:adb_shell
  - 多指同时操作(双摇杆/边移动边瞄准) · unsupported=adb input 无法真正多指,帧级时序也做不了
- 结算与数值
  - 结算数值与期望一致(击杀×10 等) · verify=evidence:state_report
  - 结算页显示与需求一致 · verify=judge:vision-closed
  - 上限/边界(金币上限、负数、超上限截断) · verify=evidence:state_report
- 支付与发货
  - 重复支付/双倍结算不发生 · verify=evidence:state_report
  - 响应丢失后重试不重复发货 · verify=action:fault
  - 支付取消/失败后的状态与文案 · verify=judge:vision-closed
- 存档与重连
  - 断线重连不丢档 · verify=evidence:state_report
  - 重连收敛(会话恢复且无待处理操作) · verify=evidence:state_report
  - 进程被杀后重启不重放未确认订单 · verify=action:fault
- 异常与体验
  - 崩溃与 ANR · verify=evidence:logcat
  - 画面卡死(相邻截图逐字节相同) · verify=tool:scan_anomalies.py
  - UI 错乱/流程断裂/异常弹窗 · verify=judge:explore
  - 手感/打击感/观感 · manual=真机自动化判不了主观体验,只能人工玩一局
- 回归与放行
  - 跨轮界面差异(哪些界面变了) · verify=tool:compare_screens.py
  - 性能与启动耗时对比基线不退化 · verify=tool:scan_anomalies.py
  - 该跑的用例都跑了(P0/P1 无缺口) · verify=tool:regression_set.py
  - 本次缺陷清单与证据可交付 · verify=tool:file_bugs.py
  - 产物与证据里不带明文凭据 · verify=tool:check_secrets.py
  - 产品/研发/策划担心的每一条都有覆盖方式与结论(不许口头覆盖) · verify=tool:concerns.py

---

## 标注语法

每条叶子行尾用 ` · ` 分隔一段标注,五选一:

| 标注 | 含义 | 校验 |
|---|---|---|
| `verify=judge:<模式>` | 由判定轮判(模式取 `explore` / `vision-closed` / `replay` / `state-report`) | 模式必须存在;`replay` 目前未实现,会被标成缺口 |
| `verify=evidence:<类型>` | 靠证据 + 代码/判定轮判 | 类型必须在 `screenshot` / `trace` / `logcat` / `state_report` / `perf` / `crash` 里 |
| `verify=action:<动作>` | 靠执行轮的动作制造条件 | 动作必须在动作词汇表里(见 `4-conversion.md`) |
| `verify=tool:<脚本>` | 有专门的工具做这件事 | 脚本必须真实存在于 `scripts/` |
| `manual=<原因>` | 只能人工 | 必须写原因 |
| `unsupported=<原因>` | 平台不支持,做不到 | 必须写原因(并核对 `8-platform-limits.md`) |
| `judge_only=<原因>` | 平台只给看、不给编程控制 | 必须写原因 |

**没标注 = 缺口**:这条主干的验证路径还没定,`backbone_map.py` 会报出来。
