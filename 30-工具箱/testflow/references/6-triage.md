# 6 · 判定与提 bug(阶段 6)

## 判定:谁能判什么

| 判定项 | 判定者 | 说明 |
|---|---|---|
| `assert` + `vision-closed` | **独立的判定轮**(不是执行轮) | 拿截图 + 封闭问题 + region,答是/否 |
| `assert` + `state-report` | 代码 | 读上报数据,按 op 机械比对 |
| `assert` + `replay` | 代码 | 轨迹比对 |
| `judgment` | 判定轮 | 需要判断力,必须引用证据 |
| `manual` | 人 | 一律 `pending_manual` |

**执行轮与判定轮必须是不同的调用。** 同一个视觉智能体既玩又判 = 自己出题自己答题自己判卷。

判定轮的输入**只有** `actual` + 证据 + 该判定项自身的期望,**不能看到执行轮的总结或结论**。

## 结果状态

判定项的 `verdict` 取值就这 8 个(代码常量 `validate_cases.VERDICTS`;`check_docs.py` 会拿本表与代码机械对账):

| 状态 | 含义 | 计入通过率 | 开 bug |
|---|---|---|---|
| `pass` | 判定通过,且证据齐全 | 是 | 否 |
| `fail` | `assert` 判定不通过,且证据已绑定 | 是 | **是** |
| `blocked` | **证据缺失、判定端点不可达,或前置条件不满足** | 否(单独统计) | 否 |
| `skipped` | 执行器未实现或平台不支持 | 否 | 否 |
| `pending_judgment` | 待判定轮(独立调用 `judge.py`)评估 | 否 | 否 |
| `pending_manual` | 待人工确认(如 `judgment` 判定项没声明期望答案) | 否 | 否 |
| `ok` | explore 用例:没发现异常 | 否(探索项单独统计) | 否 |
| `anomaly` | explore 用例:发现异常候选,由人复核 | 否(探索项单独统计) | 否 |

**两条铁律:**

1. **证据没采到 → 只能是 `blocked`,不许是 `pass`。** 这是防"没测出来就算通过"。
2. **`blocked` / `skipped` / `pending_*` 都不开 bug。** 它们反映的是我们自己的能力缺口,不是产品缺陷。开成 bug 会污染缺陷库。

`blocked` 和 `skipped` 的比例要单独统计并写进报告——**这是"这套测试实际覆盖了多少"的诚实指标**,比通过率重要。

## 判定轮的实现(`judge.py`)

`scripts/judge.py` 就是上面说的"独立的那一次调用"。执行轮(`run_case.py`)产出
`run-<ts>.judge-requests.json` 与截图;判定轮吃这两样,产出 verdict 与判定日志。

```bash
# 先预检证据齐不齐(不调端点、不花 token)
python3 "$SKILL_DIR/scripts/judge.py" --run test-artifacts/<f>/04-runs/run-<ts>.json --preflight

# 正式判定:读截图 + 封闭问题 -> 视觉端点 -> 严格 yes/no -> 回填 verdict
python3 "$SKILL_DIR/scripts/judge.py" --run test-artifacts/<f>/04-runs/run-<ts>.json
```

它是怎么守住三条规矩的:

| 情形 | judge.py 的行为 |
|---|---|
| 判定项绑定的截图文件不存在(计划里写了路径但没落盘) | `blocked`,并写明缺哪个文件 —— **不调用端点、不花 token、不做任何猜测** |
| 端点未配置 / 连不上 | **全部 `blocked`**,原因写明 `judge_endpoint_unreachable`;第一次连接失败即停止后续请求,不逐个重试 |
| 模型答得含糊("可能是吧""部分可见")或没有明确 YES/NO | `blocked`,并把原始响应留痕供人复核 |
| 模型答得出明确 yes/no | 与判定项声明的 `answer` 机械比对:一致 `pass`,不一致 `fail` |
| `judgment` 判定项没有声明期望答案 | 只产出 `observedAnswer` + 证据,verdict 停 `pending_manual` —— 期望不能来自观察(规矩二),由人确认 |
| explore 用例 | `pass`/`fail` 按设计换算成 `ok`/`anomaly`,不计入通过率;原始判定留在 `judgedBy.rawVerdict` 可追溯 |

其它落地细节:

- **按 `region` 真的裁剪像素再送**(`coordmap.crop_png_region`)。只把 region 写进提问文本、却把整屏发给模型,等于让无关元素参与判断。
- **不给模型看期望答案。** prompt 里只有封闭问题 + region,`answer` 由代码比对 —— 提前告诉模型"期望是 yes"会把判定变成附和。
- **不给模型看执行轮的结论。** prompt 不含计划 notes、不含其它判定项结果。
- 判定日志 `<runId>.judgment.jsonl` 记模型名、端点、原始响应、截图 sha256(原图与裁剪后各一份)、耗时、解析结果。
- run json 里回填 `actual.observedAnswer` 与 `judgedBy`,并把顶层 `judgment` 字段写成这次判定的摘要。
- 退出码:`0` = 至少判出一条 pass/fail;`1` = 一条都没判出来(全 blocked/skipped,值得在 CI 里被看见);`2` = 用法/路径错误。

**端点配置见 `assets/judge-config.json`**(OpenAI 兼容 `/chat/completions`)。**密钥不写进配置**:用 `api_key_env` 指向的环境变量。

### 真机自检:`judge_live.py`(换机器后第一件该跑的)

`judge.py --selftest` 用的是**本地桩端点**:它证明协议、严格解析、回填都对,但证明不了
"真实端点收不收图、答得对不对"。2026-09-20 之前判定轮**从未调用过真实视觉端点** —— 这是
整个 skill 最大的未验证面。现在用一条命令补上:

```bash
export TESTFLOW_JUDGE_ENDPOINT=http://<主机>:<端口>/v1     # Windows: setx 后重开终端
export TESTFLOW_JUDGE_MODEL=<模型名>
export TESTFLOW_JUDGE_API_KEY=<密钥>
python3 "$SKILL_DIR/scripts/judge_live.py"
```

它现场画一张**真值已知**的图(白底 + 左红圆 + 右蓝圆,无文字),跑四条判定:
左半有红圆(yes)、右半有蓝圆(yes)、图中有文字(no)、图中有绿色(**期望 yes,必须判成 fail**)。
最后一条是对照:只会说 yes 的判定轮等于没有判定。

退出码:`0` = 四条全部与真值一致;`1` = 有判定与真值不符(**不要采信这一轮的结论**);
`3` = 端点未配置(**不要读成"没问题"**)。

真机跑出来的三条结论(2026-09-20,内网端点 + `deepseek-flash`):

1. **链路是通的、且是对的。** 裁剪后的字节与判定日志里的 `sentSha256` 逐字节对得上;
   模型能看见裁剪区域里的细节(能读出"位于图标右侧""在「App」与「用户和访问」之间")。
2. **会推理的模型会先把 token 花在 `reasoning_content` 上。** 实测 `max_tokens=64` 时
   64 个 completion_tokens **全是推理**,`content` 返回空字符串 —— 模型其实已经得出结论,
   只是没来得及落笔。默认值因此从 256 提到 512;并且这种响应现在会被单独识别成
   「推理吃光 token 预算」、把该调 `max_tokens` 直接写进 `reason`,不再伪装成普通空响应。
3. **整幅纯色的合成图是分布外输入,模型会瞎答。** 实测把一整块纯蓝的裁剪图答成
   「整个画面为空白白色」(而同一张图的整幅版本它又能正确说出左右两色)。
   所以自检图不要改成纯色块;真实截图上的表现正常(4/4 与真值一致)。
   这条也提醒:**单条判定会错** —— 判定日志里的原始响应 + 截图哈希 + `--override` 人工复核
   是最后那道闸,别把判定结果当成终审判决。

端点与模型走环境变量,不写进 `assets/judge-config.json` —— 那是个随 skill 入库的文件,
而且内网地址换台机器就不通,写进去只会让人以为配好了。


## 什么才开 bug

**只有 `assert` 判定失败才开 bug。** 开单前逐条自检:

- [ ] 该判定项的 `kind` 是 `assert`(不是 judgment/manual)
- [ ] 证据齐全:截图或上报数据已落盘,且路径可访问
- [ ] **期望有合法来源**:`expect_source` 是 `requirement` 或 `invariant`
- [ ] **不是产品设计如此** —— 对照 `understanding/<game>.md` 的"已知异常"和设置/帮助文本
- [ ] **能稳定复现**,或明确标注为偶现(偶现要有失败次数/总次数)
**任何一条不满足,就不要开单,标 `blocked` 或回退去确认期望。** 尤其第 3、4 条:期望来源不合法或设计如此的"缺陷",是测试自己制造的噪音。

## bug 单

由 `file_bugs.py` 从运行结果生成,写到 `test-artifacts/<feature>/05-bugs/BUG-<n>.md`。必须包含:

```markdown
# BUG-001 结算页金币增量与击杀数不符

- **严重级**: P1
- **状态**: open
- **关联用例**: REQ-003-C01
- **关联需求**: R-3.2(结算后金币按击杀数 × 10 发放,上限 500)
- **判定项**: E2
- **期望来源**: requirement
- **判定模式**: state-report
- **复现性**: 稳定(3/3)
  <!-- 这一行由 file_bugs.py 自动填:把多轮 run 一起给它(--run r1.json --run r2.json …),
       它按 (用例, 判定项) 统计出「稳定(N/N)」「偶现(M/N)」「未知(只跑了 1 轮)」。
       只跑一轮时它只会写「未知」—— 单轮结果不许冒充稳定。 -->
- **测试机**: android-mid / 1080×2400 / dpi 480
- **宿主**: 微信 / AppID wxXXXX
- **运行**: run-20260917-142033

## 期望
结算页金币增量 = 击杀数 × 10,击杀 8 时增量为 80

## 实际
金币增量 40(上报 player.coinDelta = 40,击杀 8)

## 复现步骤
1. 前置: 账号金币 0,进入对局
2. S1: 完成一局,击杀 8 个
3. S2: 进入结算页
4. S3: 读取上报的 player.coinDelta

## 证据
- 截图: 04-runs/evidence/REQ-003-C01_S2_03.png
- 上报: 04-runs/evidence/REQ-003-C01_state.json
- 轨迹: 04-runs/evidence/REQ-003-C01_trace.json

## 备注
期望值来自需求 R-3.2;实际值来自游戏内状态上报,非视觉推断。
```

要点:

- **期望与期望来源必须写明** —— 让人能判断"这是需求要求,还是测试自己推的不变量"
- **实际值必须来自证据,不能是模型的转述**
- **复现步骤要能从用例原文重建**,前置条件带具体数值
- **测试机、宿主、运行 ID 必须记录**,坐标类与兼容性问题靠这个复盘

## 严重级

| 级别 | 判据 |
|---|---|
| P0 | 崩溃、卡死、丢档、货币/支付错误、无法登录或进入游戏 |
| P1 | 核心功能不可用、结算/数值错误、弱网下不可用 |
| P2 | 次要功能异常、UI 错乱但不影响操作 |
| P3 | 文案、视觉瑕疵 |

**金额、结算、存档类一律不低于 P1。**

## 证据要跟着单子走

加 `--bundle` 时,`file_bugs.py` 会把每条判定项的证据**拷进单子自己的目录**
(`05-bugs/BUG-00N.evidence/`),并写 `manifest.json`(来源路径 / 字节数 / sha256),
bug 单里引用的是这份拷贝。

为什么:单子发给研发、或换台机器打开,`04-runs/evidence/` 的绝对路径就断了;
拷贝之后单子与证据一起走,哈希还能证明"这份截图没被动过"。
拷不到的文件**不静默丢** —— 清单里记 `missing`,单子里保留原路径并标注"文件当前不存在"。

## 去重

同一缺陷可能被多条用例命中。开单前按「用例 + 判定项 + 实际值」查重;若是同一根因,合并到一张单并在"关联用例"里列全。

**跨用例的同根因合并要保守**:只在确认实际表现一致时合并,不要因为都在"结算"模块就合并。

## 报告

`file_bugs.py` 同时生成 `report.md`:

```markdown
# 测试报告 run-20260917-142033

- 需求: 对局结算与货币
- 测试机: android-mid / 微信 / 弱网(800ms, 15% 丢包)
- 用例总数: 12

## 结果
| 用例状态 | 数量 |
|---|---|
| executed | 8 |
| skipped | 1 |
| blocked | 3 |

| 判定结果 | 数量 |
|---|---|
| pass | 6 |
| fail | 2 |
| blocked | 1 |
| pending_judgment | 1 |
| pending_manual | 1 |
| skipped | 1 |

## 覆盖率(诚实指标)
- 需求子句覆盖: 8/10(未覆盖 R-3.4、R-5.1)
- 期望覆盖: 100%(三道闸门已通过)
- **实际执行率: 8/12 = 67%**(skipped 1 + blocked 1 + pending 2 不计入)

## 缺陷
- P1 × 1、P2 × 1 → 见 05-bugs/

## 稳定性(多轮)
- 3 轮: run-20260917-142033, run-20260917-150110, run-20260917-161240
- **多轮结果不一致的判定项 1 条**(偶现候选,开单时必须如实标注):
  - REQ-NET-C01 / E3 — 多轮结果不一致: fail → fail → pass

## 期望来源分布
- requirement: 9 条判定项
- invariant: 5 条判定项
- ⚠ 观察行为派生的期望: 0 条(合规)

## 平台限制影响
- iOS 弱网精度低于 Android,本报告结论仅适用于 Android
- 多指手势用例 2 条标 manual,未自动化
```

**"实际执行率"是必须报的指标。** 通过率高但执行率低的报告是误导性的。

**"稳定性"也是。** 只跑一轮的通过率不代表稳定 —— 报告会写明轮次;偶现项要人复核后再决定是否开单,不要为了凑数把 flaky 当稳定缺陷报出去。

## 复核失败的判定

若判定轮给出 `pass` 但证据不足以支撑,或有理由怀疑误判:

1. **不要直接改成 fail** —— 那等于用人的判断替换模型的判断,失去可追溯性
2. 标 `blocked`,写明"判定证据不足",**不开 bug**
3. 改进该判定项的 `question` 或 `region` 后重跑

复核有工具,不用手改 JSON:

```bash
# 先列出来看(list 会给每条判定的结论与理由)
python3 "$SKILL_DIR/scripts/judge.py" --run <run>.json --list

# 再改:--verdict/--by/--reason 缺一不可
python3 "$SKILL_DIR/scripts/judge.py" --run <run>.json   --override REQ-NET-C01/E3 --verdict blocked --by <你的名字>   --reason "判定证据不足:截图看不到底部区域"
```

改完之后 `judgedBy` 里会留下 `previousVerdict` / `overriddenBy` / `overriddenAt` /
`overrideReason`,**重跑 `file_bugs.py` 时这条复核会写进 bug 单**(`- **判定复核**: …`),
所以"谁把结论改成了什么"是可追溯的。改成 `blocked` 的判定项**不会开单** ——
复核能撤回一张单子,这正是它该有的能力。

**判定本身出错是常态**(视觉判定尤其)。报告里 `pending_judgment` 与 `blocked` 的比例偏高,说明判定项设计需要收紧,不是产品有问题。
