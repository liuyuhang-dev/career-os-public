# testflow：一条不说谎的测试流水线

> 这不是一个工具包，是一套**判定纪律**的完整实现：从需求理解到提 bug，每一步都规定了"什么算做完"。

## 为什么值得留着

大多数测试体系的问题是**报告不可信**：判不了的说"通过"，没跑的说"没问题"。
这套东西的全部设计都指向一件事：**让报告不会说谎**。

代价是：能力没补齐时报告会显得"不够绿"。那是真实状态，不是流程失败。

## 十六篇方法文档（`references/`）

按编号读，就是流水线的顺序：

| # | 文件 | 一句话 |
|---|---|---|
| 0 | `0-feasibility.md` | 可行性结论档案：分清哪些有官方文档支持、哪些是推断、哪些没验证 |
| 1 | `1-understanding.md` | 阶段 0：体验与理解。**杠杆最高的一步**——理解错了下游全错 |
| 2 | `2-requirements.md` | 阶段 1：需求拆解成可判定子句 |
| 3 | `3-cases.md` | 阶段 2：用例由 AI 生成、人复核；防 AI 写"自证型用例" |
| 4 | `4-conversion.md` | 阶段 3：断言转换。**转换是翻译，不是再创作** |
| 5 | `5-execution.md` | 阶段 5：真机执行底座只做两件事——截图 + 输入 |
| 6 | `6-triage.md` | 阶段 6：判定与提 bug。规定"谁能判什么" |
| 7 | `7-instrumentation.md` | 增量项：游戏内状态上报，把判定从"看画面"升级到"读状态" |
| 8 | `8-platform-limits.md` | 平台能力边界：每一条都是"做不到"，写下来避免承诺 |
| 9 | `9-lifecycle.md` | 全流程地图与缺口清单 |
| 10 | `10-upstreams.md` | 上游不止需求文档：配置表、CSV、构建配置、源码都能交叉 |
| 11 | `11-idempotency.md` | 幂等类缺陷：黑盒覆盖不了，但也不是只能人肉 review |
| 12 | `12-confidence.md` | 置信度：凭什么说质量是高的 |
| 13 | `13-intake.md` | 关注点从哪来——凭空想的不可信 |
| 14 | `14-modules.md` | 模块分类与二级节点展开：子句直接变关注点，粒度太粗 |
| 15 | `15-dimensions.md` | **边界与异常的完备性保证**：维度矩阵与三态纪律 |

## 用例集正文（`specs/`）

| 文件 | 内容 |
|---|---|
| `specs/baseline-suite.md` | 通用基线用例集（39 条，任何版本复用），863 行 |
| `specs/version-suite.md` | 版本专属用例集（版本验收 / 回归 / 冒烟 / 发版门禁），287 行 |

这两份是主流程文档里引用的「用例本体」，2026-11-16 补入。

## 关键模板（`assets/`）

| 文件 | 用途 |
|---|---|
| `check-dimensions.csv` | 12 维维度表（id / 问法 / 触发条件 / 示例） |
| `common-sense.json` | 21 条需求没写但必须成立的常识，带建议断言 |
| `concern-register-template.csv` | 关注点登记册（台账） |
| `understanding-template.md` | 体验理解模板 |
| `case-template.md` | 用例模板 |
| `backbone-map.md` | 主干地图 |

## 二十个脚本（`scripts/`）

| 类别 | 脚本 |
|---|---|
| 闸门 | `check_config.py`、`check_docs.py`、`check_secrets.py`、`validate_cases.py` |
| 维度与关注点 | `concerns.py`（生成矩阵 / 完备性检查 / 转登记册） |
| 执行与判定 | `run_case.py`、`judge.py`、`judge_live.py`、`compare_screens.py` |
| 流水线 | `pipeline.py`、`scaffold_cases.py`、`import_cases.py` |
| 回归 | `regression_set.py`、`file_bugs.py` |
| 辅助 | `backbone_map.py`、`coordmap.py`、`review_sheet.py`、`scan_anomalies.py`、`scan_idempotency.py` |

入口是 `SKILL.md`，交接状态在 `HANDOFF.md`。

## 三条不能破的规矩

1. **玩与判分离**——执行的主体和判定的主体不能是同一个。
2. **期望不能来自观察**——只能来自需求 / 不变量 / 配置 / 源间一致性。
3. **没证据不许判通过**——能力缺失记 `blocked`，平台不支持记 `skipped`，都不许记 `pass`。

## 已知边界

- 判定轮、状态上报、iOS 通道这三项没补齐之前，相关层只能返回 `blocked`。
- 视觉判定依赖外部端点；端点未配置时脚本退出码为 3，**不要读成"没问题"**。
- 真机执行慢，靠"按变更影响面选集"控制时长，不做全量每版本跑。

## 脱敏说明

入库前已把内网判定端点替换为 `<内网判定端点>`；其余内容为方法论与脚本，不含公司业务数据。
