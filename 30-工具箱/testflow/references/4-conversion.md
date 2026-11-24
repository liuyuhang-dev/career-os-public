# 4 · 断言转换(阶段 3)

把自然语言用例翻译成机器可读断言。**转换是翻译,不是再创作。**

## 硬纪律

1. **每条 `E-N` 必须有且仅有一条对应判定项。** 漏一条 → 校验器报 `MISSING_EXPECTATION_COVERAGE` 并拒绝通过。这是防"模型悄悄丢掉最难验证的那条期望"的唯一防线,而那条往往就是核心需求。

2. **不许出现孤儿判定项。** `source` 指向不存在的 `E-N` → `ORPHAN_CHECK` 报错。

3. **不许弱化判定。** 不得为了避免失败把 `eq: 423` 改成 `in: [200, 423]`。执行结果与期望不符时,由人决定改需求还是提 bug,**转换阶段无权自行放宽**。

4. **不许静默降级。** 复合期望("返回 423 且文案清晰")应回报要求拆源用例,**不得**只翻译一半,也**不得**整条降级成 `judgment` 把确定性的部分也变成主观判断。

5. **不确定就报错。** 出现"应当合理""提示友好"这类无法映射的表述 → 报 `BLOCKED`,列出该 `E-N`,不要猜。

## 三分类

每条判定项声明 `kind`:

| kind | 判定者 | 用途 |
|---|---|---|
| `assert` | 代码或独立判定轮 | 可机械判定的结果。**优先,能判就判** |
| `judgment` | 模型 + 证据 | 需要判断力(文案是否说明原因)。必须给封闭问题 |
| `manual` | 人 | 手感、打击感、真实弱网体感。一律 `pending_manual`,不计入通过率、不提 bug |

## 判定模式(仅 `assert` 用)

按可复现性递进。**选够用的最低档,不要无脑上最高档**——档位越高越慢越贵。

| `mode` | 何时用 | 可复现 | 需要的证据 |
|---|---|---|---|
| `vision-closed` | 显示层结果:元素是否出现、文案是否正确、画面是否异常 | 中 | 截图 |
| `state-report` | 内部一致性:数值精确性、存档、结算、重连收敛 | 最高 | 结构化状态 |
| `replay` | 已固化轨迹的回归 | 高 | 轨迹 + 截图 |

选择规则:

- 结果**在屏幕上可见**且能写成是/否问题 → `vision-closed`
- 结果**涉及内部数值/存档/一致性**,或"看起来对但可能存错了" → `state-report`
- 该用例已有录制轨迹,且只需确认没有回归 → `replay`
- 涉及**帧级时序或多指操作** → 真机做不了,标 `manual` 并写明原因

## 判定项 schema

```json
{ "source": "E1", "after": "S2", "kind": "assert", "mode": "vision-closed",
  "question": "屏幕底部是否出现带『立即结算』字样的按钮?",
  "region": [0.0, 0.78, 1.0, 1.0], "answer": "yes" }

{ "source": "E2", "after": "S2", "kind": "assert", "mode": "state-report",
  "path": "player.coin", "op": "eq", "value": 500 }

{ "source": "E3", "after": "S2", "kind": "judgment",
  "question": "断线提示是否说明了失败原因并提供重试入口?" }

{ "source": "E4", "kind": "manual", "reason": "打击感需人工确认" }
```

- `after`:这条判定观测哪个动作之后的状态 —— **`vision-closed` 与 `judgment` 必填**,而且必须指向用例里真实存在的 `PRE-N` / `S-N`。它是证据绑定的锚点:截图按 `after` 绑到判定项上,写错了不会报错,只会让证据永远绑不上、判定静静退化成 `blocked`(校验器会报 `CHECK_AFTER_UNKNOWN` / `VISION_NO_AFTER` / `JUDGMENT_NO_AFTER`)
- `region`:**归一化坐标** `[x0, y0, x1, y1]`,限定提问范围。**必填**——全屏提问会因无关元素造成误判

### 视觉问题必须是封闭问题

这是可复现性的前提。

| 差 | 好 |
|---|---|
| 结算页面正常吗? | 屏幕底部是否出现带『立即结算』字样的按钮? |
| 有没有报错? | 屏幕中央是否出现包含『网络异常』字样的弹窗? |
| UI 有没有问题? | 顶部金币数值是否显示为负数或含非数字字符? |

**文案必须逐字取自 `understanding/<game>.md` 的 UI 元素词汇表**,不要改写、翻译或补标点。

### `op` 取值

| op | 含义 | value |
|---|---|---|
| `eq` / `ne` | 等于 / 不等于 | 任意 |
| `gt` / `gte` / `lt` / `lte` | 数值比较 | 数值 |
| `in` | 属于集合 | 数组 |
| `contains` | 字符串或数组包含 | 任意 |
| `matches` | 正则 | 正则串 |
| `exists` | 字段存在且非 null | 不需要 |
| `nonempty_string` | 是字符串且非空 | 不需要 |
| `within_seconds` | 相对当前时间在 N 秒内(过期时间类) | 秒数 |
| `unique_by` | 是列表,且每个元素的指定字段互不重复(背包重复条目类不变量) | 字段名,如 `"id"` |

## 动作(action)词汇表

`preconditions` 与 `steps` 都是显式字典。坐标一律**归一化**。

```json
{"action": "launch_host", "host": "wechat"}
{"action": "open_entry", "entry": "minigame-recent", "appid": "wxXXXXXXXXXXXXXXXX"}
{"action": "tap", "target": {"norm": [0.5, 0.91]}, "note": "开始游戏按钮"}
{"action": "tap", "target": {"anchor": {"text": "开始游戏"}, "offset": [0, 0]}, "note": "锚点相对定位"}
{"action": "swipe", "from": [0.5, 0.8], "to": [0.5, 0.4], "duration_ms": 300}
{"action": "wait", "ms": 2000}
{"action": "wait_for", "question": "屏幕上是否出现带『开始游戏』字样的按钮?",
 "region": [0, 0.7, 1, 1], "timeout_ms": 20000}
{"action": "screenshot", "name": "after-start"}
{"action": "logcat", "name": "after-start", "clear_first": false}
{"action": "perf", "kind": "gfxinfo", "name": "after-fight", "reset": true}
{"action": "perf", "kind": "meminfo", "name": "after-fight"}
{"action": "network", "profile": "weak"}
{"action": "reset_network"}
{"action": "launch_app", "package": "com.tencent.mm"}
{"action": "adb_shell", "cmd": "..."}
{"action": "adb_shell", "cmd": "dumpsys package com.tencent.mm | grep versionName",
 "save_as": "pkg-version"}
{"action": "replay", "trace": "traces/exp-001.json"}
{"action": "fault", "kind": "drop_response", "after_step": "S1", "delay_ms": 100, "window_ms": 2000}
{"action": "fault", "kind": "kill_after_step", "after_step": "S2"}
{"action": "fault", "kind": "offline_during_step", "around_step": "S3", "duration_ms": 3000}
{"action": "fault", "kind": "replay_callback", "target": "PAY_CALLBACK", "times": 3}
```

### 故障注入(`fault`)

用于制造「真实但难以撞到」的时序条件。**只对有副作用的链路使用**,典型是支付、发货、结算、领取、存档。

| `kind` | 制造的场景 | 客户端能否独立注入 |
|---|---|---|
| `drop_response` | 请求发出后、响应到达前切断下行 → 客户端重试 | 能(需网络层注入或代理) |
| `delay_response` | 延迟响应直到客户端超时 | 能(同上) |
| `kill_after_step` | 杀掉客户端 → 未确认订单重启重放 | **能**(`am force-stop`) |
| `offline_during_step` | 制造本地待处理队列 | **能**(`svc wifi/data disable`) |
| `replay_callback` | 重复投递外部回调 | **不能,需服务端或代理** |
| `duplicate_request` | 重放同一请求(必须同请求 id) | 需游戏内注入或代理 |

**两条硬约束:**

1. **故障注入必须配 `state-report` 判定。** 故障后果在界面上通常不可见(重复扣款、重复发货、扣两次只发一次货),用视觉判定等于没判。校验器会报 `FAULT_WITHOUT_STATE_REPORT`。
2. **不许用「连续点击 N 次」冒充重复支付验证。** 那是伪覆盖,会制造"已覆盖"的虚假信心,比不测更糟。校验器会报 `PSEUDO_DUPLICATE_COVERAGE`。见 `references/11-idempotency.md`。

判定这类缺陷时必须用**精确等于单次值**,而不是「大于 0」——后者对重复支付完全无感(发两次货,增量是 2 倍,仍然大于 0)。

**优先用 `anchor` 而非 `norm`。** 锚点相对定位跨机型更稳:`norm` 在换分辨率/刘海屏后可能偏出按钮,`anchor` 先定位文字再按偏移点击,容错高得多。

## 弱网声明

弱网是必测项(尤其是弱网游戏),在用例里声明:

```json
"network": {
  "profile": "weak",
  "latency_ms": 800, "jitter_ms": 300, "loss_rate": 0.15,
  "disconnect_after_ms": 5000, "reconnect": true,
  "assert_convergence": true
}
```

`assert_convergence: true` 表示这条用例要断言**重连后状态收敛**——这是弱网游戏缺陷密度最高的地方:

- 断线重连后状态不一致 / 回退
- 离线期间的本地操作重放冲突
- 重复上报导致双倍结算

这类缺陷**视觉永远抓不到**(看起来是对的),必须用 `state-report` 判定。

**平台差异要如实标注**:Android 可用 `tc`/netem 精确限速;iOS 真机只能靠代理或 AP 侧限速(Network Link Conditioner 只对模拟器生效)。iOS 上的弱网用例应标 `notes: "iOS 侧为代理近似限速,精度低于 Android"`,不要假装两端等价。

## 确定性声明

涉及游戏内状态的用例应声明 `determinism`,否则接受抖动:

```json
"determinism": { "seed": 20260917, "fixed_timestep_ms": 16, "disable_animations": true }
```

`validate_cases.py` 对 `state-report` 判定但缺 `determinism` 的用例发**警告**(不阻塞),提示结果可能抖动。

## 输出

`test-artifacts/<feature>/03-cases/<用例ID>.json`,每条用例一个文件(便于局部重跑)。schema 见 `assets/derived-schema.json`。

转换完**先自己跑校验器**:

```bash
python3 "$SKILL_DIR/scripts/validate_cases.py" \
  --requirements test-artifacts/<f>/01-requirements.json \
  --specs specs/ --cases test-artifacts/<f>/03-cases/
```

不通过就修,通过了再交人复核。

## 无法转换时的回报格式

```
BLOCKED 转换无法完成

用例: REQ-003-C02
无法翻译的期望:
  - E4:「提示信息应当友好」
    原因: 「友好」不可判定,且未指明观测对象
    建议: 改写为「屏幕上出现包含『账号已锁定』字样的提示条」;
          或显式标为 judgment 并给出封闭问题
```
