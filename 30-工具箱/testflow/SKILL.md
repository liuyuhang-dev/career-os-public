---
name: testflow
description: >
  AI-driven testing pipeline for mobile games (Unity + 微信小游戏 + 抖音小游戏) where the
  real test target is a physical phone, not an API. Use whenever the user wants to test a
  game, write or review test cases, explore a game to understand it, run regression on a real
  device, or file bugs from a test run — even if they only say "帮我测一下这个游戏" or
  "跑一轮测试". Covers: structured exploration of a game to produce an understanding doc,
  requirement decomposition, AI-generated natural-language test cases, conversion to
  machine-readable assertions, human review sheets, real-device execution over adb or
  iOS screen mirroring, and bug filing. Designed for canvas games with no DOM and no
  accessibility tree, so it uses visual judgement only where it is reproducible and
  routes everything else through invariants, replay, or in-game state reporting.
when_to_use: >
  Use when testing a mobile game, mini-game, or Unity build on a real device; when writing,
  reviewing, or auditing game test cases; when exploring an unfamiliar game to document its
  systems; when converting test cases into executable assertions; when running a regression
  pass or an exploratory bug hunt on a phone; or when triaging failed cases into bug reports.
license: Proprietary. Internal team asset.
metadata:
  author: liuyuhang
  version: "1.0"
---

# testflow

真机优先的手机游戏测试流水线。被测对象是 canvas 游戏,跑在微信/抖音宿主里,**没有 DOM、没有可访问性树**,平台也不给可编程控制 API。这个 skill 的全部设计都是围绕这个约束展开的。

> **换了机器继续做,或要判断这套东西现在能不能用,先读 `HANDOFF.md`** —— 里面有当前状态、已知缺陷、未验证的说法、机器相关事项和下一步优先级。

## 先读这一段:三条不可违背的规矩

违反其中任何一条,整套结果就不可信,后面的产物全部作废。

**规矩一:玩与判分离。** 驱动真机的执行者只输出 `actual`(截图证据 + 原始观测值),**永远不输出"通过/失败"**。判定由代码(封闭断言、回放比对、不变量检查)或独立的一轮判定完成。同一个视觉智能体既玩又判,就是自己出题、自己答题、自己判卷,是 AI 测试最大的失败模式。

**规矩二:期望不能来自观察。** 观察到的行为是事实,不是应然判断。"购买后扣了 50 金币"是事实;"应该扣 100"只能来自需求或通用不变量。若一边看游戏一边写期望,就会把现有 bug 固化成期望,从此永远测不出来。**探索阶段只产出事实描述与 bug 候选,不产出期望。**

**规矩三:没证据不许判通过。** 证据没采到 → 状态只能是 `blocked`。未实现的能力 → `skipped: executor not implemented`。**绝不伪装成 pass。**

## 流水线

```
0  体验与理解   AI 结构化遍历真机游戏 → 理解文档 + bug 候选     ← 人复核(杠杆最高)
1  需求拆解     AI → 需求子句 + 验收标准
2  用例生成     AI → 自然语言用例(期望来源:需求 或 通用不变量)
3  断言转换     AI → 机器可读断言
4  复核         人 逐条批准/驳回(看复核单)                     ← 唯一人工关卡
5  执行         真机 + 判定模式路由
6  判定与提bug  玩判分离的落地
```

阶段 0 与探索测试是同一趟体验的两种产出,一次遍历同时拿到理解文档和 bug 候选。

## 产物契约

```
<project>/
├── specs/<feature>.md                      # 自然语言用例(权威源,人复核对象)
├── understanding/<game>.md                 # 游戏理解(观察所得,人复核)
├── requirements/<feature>.md               # 需求原文(人提供)
└── test-artifacts/<feature>/
    ├── 01-requirements.json                # 需求子句(阶段 1 产出)
    ├── 03-cases/<用例ID>.json              # 派生断言(阶段 3 产出)
    ├── 04-runs/run-<ts>.json               # 原始观测值,不含判定
    ├── 04-runs/evidence/                   # 截图、轨迹、日志
    ├── 05-bugs/BUG-<n>.md
    ├── review-sheet.md                     # 复核单
    ├── anomalies.md                        # 异常扫描候选(崩溃/白屏/卡死,人复核)
    └── report.md
```

**编号即契约。** 自然语言用例里的 `PRE-N / S-N / E-N` 编号是断言回溯的锚点,派生断言必须带 `source: "E3"` 指回原文,需求用例必须带 `req_refs` 指回需求子句。三道闸门因此退化成集合比对,完全确定,不靠模型自觉。

## 三道闸门

`scripts/validate_cases.py` 强制校验,不通过就进不了执行阶段。

| 闸门 | 校验 | 防的问题 |
|---|---|---|
| 需求覆盖率 | 每条需求子句 → ≥1 条用例 | 悄悄漏测整条需求 |
| 期望覆盖率 | 每条 `E-N` → ≥1 条断言 | 悄悄丢掉最难验证的那条期望 |
| 复核状态 | `review.state == approved` | 未复核用例被执行、被当回归基线 |

## 期望来源

| 来源 | `expect_source` | 可信度 | 依赖 |
|---|---|---|---|
| 需求文档 | `requirement` | 最高 | 需求文本,必须带 `req_refs` |
| 配置表 | `config` | 高 | 配置表与字段说明,带 `config_refs` |
| 通用正确性不变量 | `invariant` | 中高 | 领域知识 + 游戏理解,**不需需求** |
| 源间一致性 | `consistency` | 高 | 至少两个源,带 `consistency_refs` |
| 源码实现 | —— | **禁止** | 代码只回答"测哪里",不回答"应该是什么" |
| 观察到的运行时行为 | —— | **禁止** | 校验器直接报错 |

**`config` 与 `consistency` 见 `references/10-upstreams.md`。** 项目里除需求文档外还有配置表、CSV、构建配置、源码,把它们当上游做交叉能把测试从纯黑盒扩成三层(静态自洽 → 实现一致 → 运行时符合),其中最便宜的静态层可在出包前跑,不依赖设备。

通用不变量(货币不能变负、数值不显示 NaN、道具不超上限、断线重连不丢档、同一订单不重复结算、界面不白屏/文字不截断、点击必有响应)不需要需求文档就能写用例。**所以即使暂时没有需求文档,也能立即产出有价值的用例集。**

## 判定模式:按可复现性递进

真机执行底座只有一件事:**截图 + 输入**。判定不挂在执行上。

| 模式 | 用途 | 可复现 | 代价 | 今天可用 |
|---|---|---|---|---|
| `explore` 视觉探索 | 找 bug:崩溃、卡死、UI 错乱、流程断裂。**不判通过/失败,只报异常** | 不要求(只要发现率) | 低 | 是(有确定性信号辅助,见下) |
| `vision-closed` 封闭视觉断言 | 回归:固定区域 + 固定是/否问题 | 中 | 低 | 是(需配视觉端点,见 `assets/judge-config.json`) |
| `replay` 录放回放 | 回归:固化动作轨迹后重放 | 高 | 中,需录一次 | **否 —— 未实现**(判定返回 `blocked`;轨迹录制/回放引擎都没有) |
| `state-report` 状态上报 | 存档一致性、结算精确、重连收敛、双倍结算 | 最高 | 需客户端配合 | **需客户端**(代码侧判定已实现,但没有上报数据时只能 `blocked`) |

**`replay` 那格别信错**:它写在设计里,但代码没有 —— 用 `replay` 判定的用例会返回 `blocked`。想现在就要跨轮回归,用 `compare_screens.py`(见 `references/5-execution.md`),不要指望 replay。

**视觉判定对"显示层正确性"是够用的**——那本来就是人审的标准。它判不了的是"看起来对但实际存错了"这一类内部不一致缺陷,那才需要 `state-report`。

视觉问题**必须是是/否封闭问题**并限定 `region`,例如"屏幕底部是否出现带『结算』字样的按钮?"。禁止"看看有没有问题"这类开放提问——开放提问不可复现。

**崩溃、白屏黑屏、画面卡死不该靠模型判。** `scripts/scan_anomalies.py` 直接从证据里扫确定性信号:logcat 里的崩溃栈与 ANR、截图是不是整屏单色、同用例相邻截图是不是逐字节相同(卡死)。这样 `explore` 的 bug 候选有客观依据,而不是"模型说好像有问题"。**这些发现是候选,由人复核,扫描器不开 bug 单** —— 要开单仍由 `file_bugs.py` 按 `assert` 判定失败来做。

`explore` 场景跑通的轨迹应能一键固化为 `replay` 用例,这是把探索成果变成长期资产的机制。

## 执行器路由

```
device-android     adb:截图/坐标输入/logcat/tc 弱网/性能        ← 主通道,能力完整
device-ios         爱思助手投屏 + computer-use 像素控制          ← 可控性待验证
device-cloud       微信云测 / 第三方云真机                       ← 接口位
unity-headless     编辑器无头逻辑回归(快速反馈辅助层)            ← 参数生成
backend-api        后端接口(弱网主客户端,权重低)               ← 接口位
manual             手感、真实弱网体验、真实支付                  ← 显式记录,不自动判
```

未实现的层一律返回 `skipped: executor not implemented`。

## 读哪个 reference

按当前所处阶段读,**不要一次全读**。

| 你的处境 | 读这个 |
|---|---|
| 刚开始,需要判断这个游戏能被测到什么程度 / 要跟人解释方案 | `references/0-feasibility.md` |
| 要第一次接触一个游戏、摸清它的系统与界面 | `references/1-understanding.md` |
| 拿到需求文档,要拆成可验收的条目 | `references/2-requirements.md` |
| 要根据需求或通用不变量写用例 | `references/3-cases.md` |
| 要把用例转成机器可读断言、选判定模式 | `references/4-conversion.md` |
| 要驱动真机、采集证据、造弱网、固化回放 | `references/5-execution.md` |
| 跑完一轮,要判定并提 bug | `references/6-triage.md` |
| 判定不可复现,想加游戏内状态上报 | `references/7-instrumentation.md` |
| 微信/抖音/iOS/Android 各能测到什么,需要做预期管理 | `references/8-platform-limits.md` |
| **要跟团队对齐全流程/能承诺什么/排优先级,或问"上线回归和玩家探索怎么做"、"还缺什么"** | `references/9-lifecycle.md` |
| **项目里有配置表/CSV/源码,想把它们也当上游做交叉验证,或问"能不能不只做黑盒"** | `references/10-upstreams.md` |
| **要测重复支付/重复发货/重复结算这类幂等与并发缺陷,或问"这种只能 code review 的问题能不能自动化"** | `references/11-idempotency.md` |
| **要回答"产品/研发担心的事我们覆盖到了吗"、要给人置信度、要汇报"质量到底怎么样"** | `references/12-confidence.md` |
| **要问"关注的这些点是从哪儿来的"、要从需求/配置/代码/缺陷单里机械解析出关注点、要给关注点做归纳** | `references/13-intake.md` |
| **要从上游生成一级/二级节点(模块分类、把一条需求展开成边界/异常/反向)、要定模块归属** | `references/14-modules.md` |
| **要让策划觉得"我想到的边界和异常你都考虑到了"、要机械保证不漏**(需求子句 × 维度矩阵) | `references/15-dimensions.md` |

## 工具

全部纯 Python 标准库,零依赖。用 `python3` 显式调用,先解析一次 skill 目录:

```bash
# 脚本按自身位置定位 skill 目录(Path(__file__)),装在哪里都能跑。常见两种:
#   项目级  <项目>/.agents/skills/testflow   ← 随项目走,推荐
#   用户级  ~/.agents/skills/testflow
# 注意:同名 skill 用户级优先于项目级,两处都装时只有用户级那份会生效。
SKILL_DIR="$HOME/.agents/skills/testflow"     # ← 改成你实际安装的位置

# 用例骨架(自然语言用例 → 派生用例 JSON 的起点:每条 E 都有判定项占位,绝不覆盖已有文件)
python3 "$SKILL_DIR/scripts/scaffold_cases.py" --specs specs/   --out test-artifacts/<f>/03-cases/

# 已有用例表(Excel/CSV)直接导入:测试同学手里的表不用重抄一遍
#   前置条件/执行步骤/预期结果 的编号行 -> PRE-N / S-N / E-N
#   「测试方法」一列 -> 判定通道(手动/视觉/打点/配置/确定性信号)
#   备注里的「需求 M-05」「关注点 C44」-> req_refs 与 concerns(台账才能机械核对)
#   步骤里写「故障注入:kill_after_step」-> 生成真的 fault 动作(fault 表见 validate_cases.py)
python3 "$SKILL_DIR/scripts/import_cases.py" --csv 用例表.csv \
  --out-specs specs/ --out-cases test-artifacts/<f>/03-cases/ \
  --requirements test-artifacts/<f>/01-requirements.json --feature <特性名> \
  --link-out test-artifacts/<f>/02-用例/关注点-用例.json

# 三道闸门(schema + 需求覆盖 + 期望覆盖 + 复核状态 + 期望来源合法性)
python3 "$SKILL_DIR/scripts/validate_cases.py" \
  --requirements test-artifacts/<f>/01-requirements.json \
  --specs specs/ \
  --cases test-artifacts/<f>/03-cases/

# 生成复核单(需求原文 → 用例 → 断言 并排 + 来源标注)
python3 "$SKILL_DIR/scripts/review_sheet.py" \
  --requirements test-artifacts/<f>/01-requirements.json \
  --specs specs/ --cases test-artifacts/<f>/03-cases/ \
  --out test-artifacts/<f>/review-sheet.md

# 应用复核决定(写回用例的 review 字段)
python3 "$SKILL_DIR/scripts/review_sheet.py" --cases test-artifacts/<f>/03-cases/ \
  --approve INV-COIN-C01 --by <你的名字>

# 归一化坐标 → 设备/窗口像素(含 iOS points≠pixels 与投屏黑边)
python3 "$SKILL_DIR/scripts/coordmap.py" --profile android-mid --selftest

# 生成执行计划(默认只出计划与命令,**不执行**)
python3 "$SKILL_DIR/scripts/run_case.py" --case test-artifacts/<f>/03-cases/XXX.json \
  --out test-artifacts/<f>/04-runs/

# 判定轮(独立调用:读截图 + 封闭问题 → 严格 yes/no → 回填 verdict,见 references/6-triage.md)
python3 "$SKILL_DIR/scripts/judge.py" --run test-artifacts/<f>/04-runs/run-<ts>.json --preflight
python3 "$SKILL_DIR/scripts/judge.py" --run test-artifacts/<f>/04-runs/run-<ts>.json
# 判定轮真机自检(换机器/换端点后第一件该跑的:真值已知的合成图,验证端点收不收图、答得对不对)
#   端点/模型/密钥走环境变量 TESTFLOW_JUDGE_ENDPOINT / TESTFLOW_JUDGE_MODEL / TESTFLOW_JUDGE_API_KEY
python3 "$SKILL_DIR/scripts/judge_live.py"
# 人工复核判定结论(改结论必须给复核人与原因,留痕并写进 bug 单)
python3 "$SKILL_DIR/scripts/judge.py" --run test-artifacts/<f>/04-runs/run-<ts>.json --list

# 关注点覆盖(产品/研发/策划担心的事 → 覆盖方式 → 结论;不许口头覆盖,缺口必须有出口)
#   从上游机械解析候选(需求子句/用例期望/配置列/代码模块/缺陷单),人只做确认
python3 "$SKILL_DIR/scripts/concerns.py" --register test-artifacts/<f>/concerns.csv   --intake requirements:test-artifacts/<f>/01-requirements.json --intake specs:specs/   --intake config:configs/fields.json --intake code:src/ --intake bugs:test-artifacts/<f>/05-bugs/   --feature <特性名> --intake-out test-artifacts/<f>/concerns-candidates.csv
#   完备性矩阵:需求子句 × 12 个维度(边界/异常/重复/中断/权限/状态/跨端/配置/时间/一致性…)
#   每格只有三种状态:有产出 / 不适用(写明原因)/ 未判断(不允许 —— 检查器点名)
python3 "$SKILL_DIR/scripts/concerns.py"   --intake requirements:test-artifacts/<f>/01-requirements.json --feature <模块>   --matrix-out test-artifacts/<f>/matrix.csv
#   常识别表:从需求里认出实体(金币/体力/物品/结算/存档/操作ID…)-> 套常识不变量
#   (不得为负 / 不重复生效 / 不回退 / 不出 NaN / 点击必有响应 …) —— 你没写但这是常识的那一半
python3 "$SKILL_DIR/scripts/concerns.py"   --intake requirements:test-artifacts/<f>/01-requirements.json --feature <模块>   --common-sense-out test-artifacts/<f>/common-sense.csv
python3 "$SKILL_DIR/scripts/concerns.py" --matrix-check test-artifacts/<f>/matrix.csv
python3 "$SKILL_DIR/scripts/concerns.py"   --matrix-to-register test-artifacts/<f>/matrix.csv > test-artifacts/<f>/concerns.csv
#   只要"展开工作单"(一级节点 + 待 AI 展开成二级节点的提示,规则见 references/14-modules.md)
python3 "$SKILL_DIR/scripts/concerns.py"   --intake requirements:test-artifacts/<f>/01-requirements.json --feature <特性名>   --expand-out test-artifacts/<f>/worklist.csv
python3 "$SKILL_DIR/scripts/concerns.py" --register test-artifacts/<f>/concerns.csv   --runs test-artifacts/<f>/04-runs --bugs test-artifacts/<f>/05-bugs/   --out test-artifacts/<f>/置信度结论.md --brief-out test-artifacts/<f>/给你关心的事_答复.md
# 给登记册发稳定编号(用例侧才能引用"我覆盖的是哪条关注点";编号只往后发,不挪老号)
python3 "$SKILL_DIR/scripts/concerns.py" --register test-artifacts/<f>/concerns.csv --assign-ids
# 台账 ↔ 用例 互相核对:登记册里写的用例是否真的存在;哪些关注点没人管
python3 "$SKILL_DIR/scripts/concerns.py" --register test-artifacts/<f>/concerns.csv \
  --cases test-artifacts/<f>/03-cases --out test-artifacts/<f>/置信度结论.md
# 封板:出信心分级 + 放行判断 + 与上次封板对比 + 归档快照(放行要留痕)
python3 "$SKILL_DIR/scripts/concerns.py" --register test-artifacts/<f>/concerns.csv   --runs test-artifacts/<f>/04-runs --freeze v1.4.4 --out test-artifacts/<f>/封板结论_v1.4.4.md

# 测试主干导图(主干—叶子—验证路径三者对齐;叶子反推主干缺什么)
python3 "$SKILL_DIR/scripts/backbone_map.py" --out test-artifacts/<f>/backbone.md

# 回归集管理(已复核清单 + 最近结论/稳定性 + 覆盖缺口;选择器可直接出用例 ID)
python3 "$SKILL_DIR/scripts/regression_set.py" --cases test-artifacts/<f>/03-cases   --runs-dir test-artifacts/<f>/04-runs --select smoke --out test-artifacts/<f>/regression.md

# 流水线:一条命令跑完能离线跑的部分(闸门→静态交叉→复核单→执行→判定→扫描→提bug→自检)
# 默认 plan-only;退出码 3 表示「没有确定性发现但有无法判断的部分」,不要读成通过
python3 "$SKILL_DIR/scripts/pipeline.py" --artifacts test-artifacts/<f>/ \
  --config configs/item.csv --spec-config configs/fields.json --root .

# 截图比对(跨轮/跨机型的确定性视觉回归:配对同一用例同一步骤,算差异比例与区域)
python3 "$SKILL_DIR/scripts/compare_screens.py" --save baselines/<机型>/shots   --cur test-artifacts/<f>/04-runs/run-<ts>.json
python3 "$SKILL_DIR/scripts/compare_screens.py" --base baselines/<机型>/shots   --cur test-artifacts/<f>/04-runs/run-<ts>.json --diff-dir test-artifacts/<f>/04-runs/diffs

# 异常扫描(崩溃/白屏黑屏/画面卡死 —— 确定性信号,不需要模型;产出候选报告,不开单)
python3 "$SKILL_DIR/scripts/scan_anomalies.py" --run test-artifacts/<f>/04-runs/run-<ts>.json

# 从运行结果提 bug(只对 assert 失败开单;多轮 run 一起给,自动算复现性)
# --bundle 把证据拷进 05-bugs/BUG-00N.evidence/(含 manifest 与 sha256,发给研发不断链)
python3 "$SKILL_DIR/scripts/file_bugs.py" --run test-artifacts/<f>/04-runs/run-<ts>.json \
  --out test-artifacts/<f>/05-bugs/ --bundle

# 幂等防护缺失扫描(支付/发货/结算等副作用入口,不需真机)
python3 "$SKILL_DIR/scripts/scan_idempotency.py" --src <源码目录> \
  --out test-artifacts/<f>/idempotency-scan.md

# 配置表静态交叉(L1:悬空引用/重复主键/必填空值/类型范围/枚举越界/孤立行;零设备)
python3 "$SKILL_DIR/scripts/check_config.py" --dump-spec-template configs/fields.json \
  --config configs/item.csv --config configs/shop.csv      # 先生成字段说明模板,人工补范围与外键
python3 "$SKILL_DIR/scripts/check_config.py" \
  --config configs/item.csv --config configs/shop.csv --spec configs/fields.json \
  --out test-artifacts/<f>/config-check.md

# 密钥卫生检查(文本里的凭据模式 + 敏感文件是否被 git 跟踪;报告只给打码片段)
python3 "$SKILL_DIR/scripts/check_secrets.py" --path . --fail-on tracked

# 文档-实现对账(交付前必跑:文档里声称的能力,代码里是否真的做了)
python3 "$SKILL_DIR/scripts/check_docs.py"

# 全部自测 + 违规回归 + + 对账,一条命令(**跨平台,Windows 上也用它**)
python3 "$SKILL_DIR/selftest.py"
```

**交付前跑 `selftest.py`。** 这个项目里出现过的错误全部属于"写了但没验证"一类,所以它是必跑项,不是可选项。`selftest.py` 是 Python 写的,在 macOS / Linux / Windows 上行为一致(`selftest.sh` 只是个 POSIX 包装)。

`run_case.py` **默认 plan-only,不碰设备**。要真正执行需显式加 `--execute`,且要求 adb 可用。这个默认值是故意的:执行真机是有副作用的动作。

**判定轮(`judge.py`)是另一次独立调用**,不挂在执行上:它读判定请求 + 截图,按 `region` 裁剪后送视觉端点,只接受明确的 yes/no。证据缺失、端点不可达、模型答得含糊,一律 `blocked` —— **绝不猜、绝不伪装成 pass**。端点配置见 `assets/judge-config.json`(密钥走环境变量,不写进文件)。

## 常见误解

- **"真机测试只能做粗略冒烟"** —— 错。视觉能走完登录→开局→对局→结算整条关键路径,`vision-closed` 断言可以判显示层正确性。受限的是帧级时序和多指操作。
- **"没有平台 API 就测不了"** —— 错。投屏 + 屏幕控制就是完整控制面,不需要任何平台配合。
- **"先玩一遍再写用例会污染用例"** —— 半错。体验能提供名词和地图(界面叫什么、从哪进去、有哪些元素),这些是写用例的前提;但体验不提供期望。区分清楚就不污染。
- **"AI 自己写用例自己判就行了"** —— 这是最容易犯的错,违反规矩一。

## 已知硬限制(不要承诺做不到的事)

- Android `input tap` 无法真正多指同时操作;`input swipe` 插值可能被手势识别器拒绝 → 多指与帧级时序用例真机做不了
- iOS 真机造弱网只能靠代理或 AP 侧限速(**这条待查证** —— 原断言是"Network Link Conditioner 只对模拟器生效",但没有来源,Apple 文档里也查不到该功能页;真机是否可用未确认,见 `references/8-platform-limits.md` 文首)
- 抖音「高性能/高性能+」渲染模式与调试器互斥,该模式问题只能靠第三方云真机 + 视觉冒烟
- 微信 MiniGameTest 节点树只支持 Cocos/Laya/Egret,**Unity 不在列表**
- 抖音小游戏**没有任何官方自动化能力**(CLI 只能 preview/upload)
- 真实支付无法自动化
