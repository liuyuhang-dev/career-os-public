# 交接文档

**这份文档是给"换了机器继续做"的人看的。** 先读这一份,再读 `SKILL.md`。

---

## 一、这是什么,怎么装到新机器

AI 驱动的手机游戏测试流水线 skill。纯 Python 标准库,零第三方依赖,不需要 node/npm。

```bash
# 项目级(随项目走,推荐)  <项目>/.agents/skills/
# 用户级(所有项目可用)    ~/.agents/skills/
mkdir -p ~/.agents/skills
tar xzf testflow-<日期>.tar.gz -C ~/.agents/skills/

# 验证装好了
python3 ~/.agents/skills/testflow/selftest.py
```

> **同名 skill 用户级优先于项目级。** 两处都装时只有用户级那份会生效,项目级会被遮蔽。
> 想用项目里那份,必须把用户级的删掉 —— 只是复制一份进项目等于没装。

脚本按自身位置(`Path(__file__)`)定位 skill 目录,**不依赖安装路径**,装哪都能跑。

要求:Python 3.10+(在 3.12.10 上验证过)。**没有其他依赖**。

`selftest.py` 应该全绿。如果有失败项,先看第三节"已知缺陷",再看是不是环境问题。

### Windows

| 事项 | Windows 上怎么做 |
|---|---|
| 安装位置 | 项目级 `<项目>\.agents\skills\testflow\`;用户级 `%USERPROFILE%\.agents\skills\testflow\` |
| 解包 | **用 Windows 自带的 `C:\Windows\System32\tar.exe`(bsdtar)**,即 `& "$env:SystemRoot\System32\tar.exe" -xzf ... -C ...`。Git Bash / WSL / MSYS2 里的 GNU tar 会把 `C:\...` 当成远程主机名,报 `Cannot connect to C: resolve failed` 后退出 2(此时目录是空的,没有残留,可放心重来) |
| 解包后清理 | 在 Mac 上打的包会混进 `._*`(AppleDouble 元数据,每个恰好 163 字节)。解到 Windows 后它们变成真实文件,`selftest.py` 会拿它们当源码和 JSON 读,报一片 `SyntaxError: source code string cannot contain null bytes` 和 `UnicodeDecodeError: 0xa3`,并连带污染四个走目录扫描的脚本。清理:`find . -name '._*' -delete`。**打包端设 `COPYFILE_DISABLE=1` 可根治** |
| 自测 | `python selftest.py` —— **用 `.py` 而不是 `.sh`**;`selftest.sh` 在 Windows 上不一定能跑,它只是个 POSIX 包装 |
| Python 命令 | 可能是 `python` 而不是 `python3` |
| adb | 是 `adb.exe`,而且未必在 PATH。`run_case.py` 会自动查找 `adb` / `adb.exe`,也可以用 `--adb "C:\path\to\adb.exe"` 指定 |
| 截图采集 | **已改为由 Python 自己写字节,不走 shell 重定向** —— PowerShell 的 `>` 会做文本转换,会把 PNG 二进制写坏,而且不报错。现在还会校验 PNG 签名,写坏会被明确报出来 |
| `device-ios` | **macOS 专属**(依赖爱思助手投屏)。Windows 上要走别的 iOS 投屏工具,或暂时放弃该通道 |
| 主通道 | `device-android` + adb 在 Windows 上完全可用,和 macOS 无差别 |

框架本体(契约、三道闸门、扫描器、坐标换算、提 bug)与平台无关,Windows 上同样可用。

---

## 二、当前状态:什么是真的能用的,什么是桩

### 完整实现并验证过的

| 工具 | 做什么 | 验证方式 |
|---|---|---|
| `validate_cases.py` | 三道闸门(需求覆盖率 / 期望覆盖率 / 复核状态)+ 两条红线(禁 `code`/`observed` 作期望来源)+ 幂等护栏 | 违规 fixture 全部被拦住(8 类错误、多个警告) |
| `review_sheet.py` | 复核单(需求→用例→判定项并排 + 来源标注)+ 批准/驳回写回 | 写回与驳回必须给原因都验证过 |
| `coordmap.py` | 归一化坐标 ↔ 设备/窗口像素;iOS points≠pixels;投屏黑边;iPhone/iPad/Android 8 个机型档案;**按 `region` 裁剪截图并回写 PNG**(判定轮送图用) | 自测覆盖 8 机型 + **PNG 各格式**(RGB/RGBA/调色板 1-8bit/16bit/灰度/灰度+alpha)+ 交错与非 PNG 明确报错 + 裁剪(灰度+alpha→RGBA / 越界夹紧 / 最小 1 像素 / 拒绝非法输入) |
| `judge.py` | **判定轮**:读判定请求 + 截图 → 按 region 裁剪 → 调视觉端点 → 严格解析 yes/no → 回填 verdict + 写判定日志 | 自测:15 例严格解析(含糊/自相矛盾/答不出全判 blocked)+ 本地桩端点端到端(YES→pass、NO→fail、含糊→blocked、explore→ok/anomaly、judgment 无期望→pending_manual)+ 输出一个一次性 HTTP 桩端点验证协议;**2026-09-20 已接真实视觉端点跑通**(见下) |
| `scan_anomalies.py` | **异常扫描(确定性信号)**:logcat 崩溃/ANR、白屏黑屏/整屏单色、相邻截图逐字节相同(卡死)、启动耗时、janky 帧比例、内存 PSS;**启动耗时与性能可建基线做退化对比**。产出候选报告,**不开 bug 单** | 自测 43 项:崩溃与 ANR 识别且不误报(含 `FATALITY` 假阳性)、白/黑/单色/正常画面、卡死含间隔门槛、时长解析(含 `m` 吞 `ms` 的回归用例)、gfxinfo/meminfo 解析与阈值、基线退化键不互相覆盖、证据缺口如实报;端到端跑通 |
| `file_bugs.py` | 只对 assert 失败开单;证据绑定到判定项;多轮自动算复现性;`--bundle` 把证据拷进单子目录(含 manifest 与 sha256);报告含实际执行率/稳定性/证据缺口 | 端到端跑通,4 张 bug 单;打包、多轮复现性、人工复核痕迹都有 selftest 覆盖 |
| `scan_idempotency.py` | 幂等防护缺失扫描(幂等键/状态守卫/回调去重/重放去重) | fixture 上正确报出 3 个缺失,对照组未被误报,无副作用的入口被过滤 |
| `concerns.py` | **关注点覆盖(置信度)**:产品/研发/策划担心的事逐条对上覆盖方式与结论;五档结论(含"已覆盖并发现缺陷");**先按模块(父节点)归纳再列叶子** + 按来源分组 + 缺口在前 + 判定强度分布;**依据要拿 run 去核对**(blocked/fail/找不到/过期/无证据都会被打回);缺口必须有出口与期限,期限过期报警;盲区类别机械点名;`--gate-only` 放行判断;**`--intake` 从五条上游机械解析候选**(需求子句/用例期望/配置列/代码模块/缺陷单,带父节点与上游引用);`--suggest` 从 run/异常/配置交叉生成候选;`--brief-out` 出"给策划的答复"(测了没/结果/有没有 bug 三件事);**`--matrix-out/--matrix-check/--matrix-to-register`:需求子句 × 12 个维度(边界/异常/重复/中断/权限/状态/跨端/配置/时间/一致性…)的完备性矩阵 —— 每格要么有产出、要么写明不适用原因,「未判断」会被点名,这是『让策划觉得想全了』的机械保证**;**`--common-sense-out` 出常识别表(从需求里认出实体→套常识不变量:资产不得为负/不重复生效/存档不回退/不出 NaN/点击必有响应…,与矩阵同一套纪律)**;**`--expand-out` 出二级节点展开工作单(AI 按规则把一条需求拆成正向/边界/异常/反向)**;**`--bugs` 把 bug 单号与严重级带进答复,并拦截「说发现缺陷却没单」**;`--freeze <版本>` 封板:放行判断 + 与上次封板对比(关了哪些缺口/又开了哪些) + 归档快照;高风险缺口(资损/掉档类)优先列;趋势记录看缺口收敛还是变多 | 自测 94 项:样例册自洽、口头覆盖/矛盾/无出口/无期限/来源非法各造一例都抓到、依据核对 pass/pending/fail/找不到/过期/无证据 六种、缺陷不许藏在 ✅(defect_hidden)、门禁三条件、五条上游解析各一条、父节点归纳、信心分级三档、封板对比与归档、候选生成与去重、退出码 0/1/2/3 |
| `backbone_map.py` | **测试主干导图**:主干—叶子—验证路径三者对齐的机械检查。未标注的叶子、引用了不存在的判定模式/证据/动作/脚本、做不到却没写原因、空主干、没被任何叶子用到的工具都会被挑出来;可渲染 Mermaid 导图。**随附 `assets/backbone-map.md` 是 mini-game 测试主干的完整版**(9 主干 × 33 叶子) | 自测 15 项 + 吃狗粮(0 缺口);反推主干已产出两条:GUI 产物凭据卫生独立成叶、入口那条叶子拆出「点击前确认处于宿主首页」并落到 `run_case.open_entry` 的判定请求里 |
| `regression_set.py` | **回归集管理**:已复核用例清单(按优先级)+ 最近结论 + 稳定性(按轮数,单轮写未知)+ 覆盖缺口(P0/P1 未复核、从未跑过、上轮无结论、陈旧);选择器 `smoke`/`regression`/`P0,P1` 直接出用例 ID | 自测 15 项:稳定性口径、三类缺口、选择器、无运行历史时不报干净 |
| `check_secrets.py` | **密钥卫生检查**:文本里的凭据模式(OpenAI/AWS/GitHub/Slack/JWT/私钥块/赋值式密钥)+ 敏感文件(.p8/.env/credentials.json…)是否**被 git 跟踪**。报告只给打码片段(前 4 后 2),自己不会成为新的泄露渠道;`--fail-on tracked` 供 CI 只对「已入库」的泄露失败 | 自测 11 项:各类凭据与敏感文件、散文不误报、打码不泄露完整值、allow 名单、被跟踪的敏感文件标高危;**在本仓库上实测**:已入库 0 条,但工作区有真凭据(见下) |
| `scaffold_cases.py` | **用例骨架生成**:自然语言用例 → 派生用例 JSON 的起点。机械字段一次填好、**每条 E 都有判定项占位**(不会漏掉最难验的那条)、`after` 指向最后一步、draft 状态;绝不覆盖已有文件 | 自测 16 项:E 覆盖无洞、字段照抄 spec、after 合法、不覆盖已有文件、骨架仍被闸门拦住 |
| `compare_screens.py` | **跨轮截图比对(确定性视觉回归)**:按文件名主干配对,算差异比例/归一化变化区域/6×6 分布,可写差异图;`--save` 建基线 | 自测 12 项:配对、一致、局部变化的比例与区域、尺寸变化单独报、差异图可解码、缺配对算缺口、抗噪(单通道 ≤12 的微差不计);**未在真机截图上跑过** |
| `pipeline.py` | **流水线编排(CI 入口)**:闸门→静态交叉→复核单→执行→判定预检→判定轮→异常扫描→提 bug→文档对账,一条命令跑完;把每步的诚实状态汇总成一句话结论,`blocked`/未配置一律算「无法判断」而不是通过 | 自测 20 项:闸门优先且失败即停、plan-only 默认、判定未配置与证据缺失都记为无法判断、产品侧缺陷与流水线故障分开(status=found)、退出码 0/1/2/3 |
| `check_config.py` | **配置表静态交叉(L1)**:悬空引用/重复主键/必填空/类型范围/枚举越界/孤立行 + 字段说明模板生成 | 自测 23 项:7 类缺陷各造一个都抓到、干净表为 0、字段说明不全走「未检查」且退出码 3、BOM 与 JSON 表、模板推断(标识符不误判枚举、脏值列按多数判类型);**未在真实配置表上跑过** |
| `check_docs.py` | 文档-实现对账 | 通过(91 项自检里的一项;含 6-triage 的 verdict 词汇表与「开 bug 的结果」对账) |
| `selftest.sh` | 一条命令跑完全部自测 + 违规回归 + 对账 | 131 项全绿 |

### 只跑过 plan-only,从未接触真实设备的

| 能力 | 现状 |
|---|---|
| `device-android` 执行器 | **生成了真实的 adb 命令,但从未执行过。命令正确性未验证。** |
| `--execute` 模式 | 实现了,但没跑过 |
| 判定轮接真实视觉端点 | ✅ **已跑通**(2026-09-20)。这台机器能连到内网端点 `http://<内网判定端点>/v1`(model=`deepseek-flash`),真实截图 4/4 判定与真值一致(含一条故意设的 fail);新增 `scripts/judge_live.py` 把这件事变成一条可重跑的命令(见 `references/6-triage.md`)。另发现:会推理的模型会先把 token 花在 `reasoning_content` 上,`max_tokens` 太小会返回空 content —— 默认已从 256 提到 512,且这种情况会被单独识别并写进 reason。 |

**这是最大的未验证面。** 第一次接真机时,预期会有命令需要调整。

### 还是桩的

| 能力 | 行为 |
|---|---|
| `device-ios` / `device-cloud` / `unity-headless` / `backend-api` | 返回 `skipped: executor not implemented` |
| `replay` 判定模式 | 返回 `blocked`(需要先实现轨迹录制/回放) |
| **帧级无响应 / 小游戏自身帧率** | 异常扫描**不覆盖**这两项:帧级无响应要帧时间戳;`gfxinfo`/`meminfo` 量到的是**宿主进程**级数据,不是小游戏自己的。别把它们当成"小游戏性能已覆盖"。 |
| `perf` / `crash` / `trace` 证据类型 | schema 里有,**没有采集通路**。用例声明了它们,run 结果里会多出一条 `evidenceGaps`(kind=`no_collector`),扫描报告里体现为"无法判断" |
| ~~配置表静态交叉~~ | **已实现**(`check_config.py`):悬空引用(表间 + 资源目录)、重复主键、必填空值、类型/范围、枚举越界、孤立行;`--dump-spec-template` 能从真表生成起步字段说明。**还没有在真实配置表上跑过** —— 项目里目前没有游戏配置表 |
| CI 集成 / 回归集管理 / 性能基线 | **前三项都已实现**:CI 入口是 `pipeline.py`;回归集管理是 `regression_set.py`(已复核清单 + 最近结论 + 稳定性 + 覆盖缺口 + smoke/regression 选择器);性能基线是 `scan_anomalies.py --save-baseline/--baseline`。**缺的是真机上的那一步**(定时触发、多机型矩阵) |

**未实现的能力一律返回 `skipped` / `blocked`,绝不伪装成 pass。** 报告里的"实际执行率"就是靠这个保持诚实的,不要为了好看改掉它。

### 判定轮(`judge.py`)的边界:能用,但别把它当"AI 判卷"

判定轮已经实现,是把 `pending_judgment` 变成结论的唯一通道。它的设计边界要记清楚:

- **它只判显示层。** 内部数值、存档、结算精确性还是得走 `state-report`(需客户端配合)。
- **它的输入不含期望答案。** prompt 只有封闭问题 + region;`answer` 由代码比对。提前告诉模型期望,判定就变成附和。
- **它看不到执行轮的结论。** prompt 里没有计划 notes、没有别的判定项结果 —— 否则等于把答案抄给它。
- **`judgment` 判定项没有声明期望答案时,它只产出观测结论 + `pending_manual`**,不替你判。期望不能来自观察(规矩二)。
- **含糊一律 `blocked`。** 模型答"可能是吧"不会变成 pass。判定轮产出的 `blocked` 偏多,说明判定项的 `question`/`region` 需要收紧,不是产品有问题。
- **判定结果可以复核。** 每条判定的原始响应、模型名、截图 sha256(原图 / 裁剪后)都写在 `<runId>.judgment.jsonl` 里;截图哈希对得上,就能人肉重判。

---

## 三、本次修的缺陷(以及为什么必须先修再打包)

这些是会**静默出错**的,带上新机器会让人分不清是工具问题还是环境问题。

| 缺陷 | 症状 | 修法 |
|---|---|---|
| **证据归属错** | `find_evidence` 按 `{用例ID}_{判定项}*` 匹配,但截图名是 `{用例ID}_{步骤ID}_{名称}.png`,所以永远命中不到,退化成"该用例的全部截图"。**一张关于 E1 的 bug 单可能挂着和 E1 无关的截图** | 截图路径按步骤记录进 `evidenceIndex`;判定项按 `after` 步骤精确绑定;绑定不到就明确写"未定位",并且**拒绝开单** |
| **explore 模式文档与代码矛盾** | 四处文档写"不判通过/失败、不参与通过率、不开单",代码却一视同仁判 pass/fail 并开单 | explore 用例的判定项改为产出 `ok`/`anomaly`;不计入通过率;只进"探索发现(异常候选)"一节,由人复核 |
| **PNG 读取失败被静默降级** | 读不了就只在 stderr 打一行警告,然后**偷偷按宽高比推算** —— 坐标会基于一个猜出来的矩形,而使用者几乎不会注意到 | 尺寸探测只读 IHDR 头(实测 **0.014 毫秒**,18MB 文件);读取失败**直接报错退出**,要降级必须显式加 `--allow-fit-fallback` |
| **PNG 格式覆盖不足** | 只支持 8bit;palette / 16bit / 交错全部失败。实测这台机器上有个 16bit 的真实 iPhone 截图直接读不了 | 补 palette(1/2/4/8bit,含 tRNS)与 16bit(RGB/RGBA/灰度/灰度+alpha);交错仍不支持但**明确报错**;自测cover 所有格式 |
| **`assert_convergence` 是装饰品** | 被校验、被写进文档,但没有任何代码消费它。弱网收敛是弱网游戏最该测的东西 | 执行时生成"等待收敛信号"的判定请求;**并新增校验规则**:声明了 `assert_convergence` 但没有任何 state-report 断言 → 报错 `CONVERGENCE_WITHOUT_ASSERTION` |
| **`determinism` 是装饰品** | 同样没人消费 | **明确标注为未实现**(schema + 校验提示)。不做假接线:目前它只是元数据,无法真正固定随机性 |

修完之后 `selftest.sh` 新增了两项**判定不变量**检查,防止这类问题复发:
- explore 用例不得产出 `pass`/`fail`
- 任何 `fail` 必须绑定到证据

---

## 四、未验证的说法:不要当事实用

原先三条是**凭记忆写的,没有引用来源**。2026-09-18 在新机器上查了一轮,结果如下(明细与证据写在 `references/8-platform-limits.md` 文首):

| 说法 | 现状 |
|---|---|
| 「`adb shell svc wifi disable` / `svc data disable` 可直接用于故障注入」 | ✅ **已查证成立,且不需要 root**。AOSP master 里 `frameworks/base/cmds/svc/svc.sh` 就是兼容翻译层:`svc wifi enable/disable` → `cmd wifi set-wifi-enabled enabled/disabled`、`svc data` → `cmd phone data`;原 `WifiCommand` 在 2019-11-21 的 "Migrate WifiCommand to WifiShellCommand" 提交里移除,`Svc.java` 留着注释。国产 ROM 首次用需实测 |
| **「iOS 真机不能用 Network Link Conditioner,只能靠代理近似限速」** | ❌ **仍未查证**。Apple 的 Xcode 文档集里没有该功能页面(查过文档索引与两页设备文档),既没证实也没证伪。**这条是整张 iOS 弱网能力表的支柱** —— 5 分钟就能定性:插一台 iPhone 连 Xcode 开开发者模式,看「设置 → 开发者」里有没有「网络链路调节器」 |
| 「`glm-4.5v` 可作独立判定模型」 | ❌ **仍未查证**。智谱文档站是 JS 渲染,静态抓取 0 命中;本次会话没有可用的搜索引擎。直接查控制台模型列表,或拿密钥打一次带图片的 `/v1/chat/completions` |

**查证方法备注(下次别再撞墙)**:本机 provider 不支持 `WebSearch`;WebFetch 抓不到搜索引擎(DuckDuckGo/Bing/Mojeek 全是人机验证),Apple 与智谱的文档站都是 JS 渲染。**能用的路子**是直接抓源文件 —— `raw.githubusercontent.com`(AOSP 镜像)与 `android.googlesource.com/...?format=TEXT`(base64)都通,Apple 的 DocC JSON(`developer.apple.com/tutorials/data/documentation/<page>.json`)也通。

**第一条优先级最高**,它决定 iOS 通道值不值得投入。

---

## 五、机器相关事项(跨机器必看)

| 事项 | 说明 |
|---|---|
| **视觉判定端点** | **已实测可用(2026-09-20)**:`http://<内网判定端点>/v1` + `deepseek-flash`(含图片输入),从这台机器可直连。端点/模型/密钥**走环境变量** `TESTFLOW_JUDGE_ENDPOINT` / `TESTFLOW_JUDGE_MODEL` / `TESTFLOW_JUDGE_API_KEY`,不写进随 skill 入库的 `assets/judge-config.json`。换机器后先跑 `python scripts/judge_live.py`(真值已知的合成图,四条判定):退出码 0 = 一致、1 = 与真值不符(不要采信结论)、3 = 端点未配置(**不要读成"没问题"**)。接真实端点前先 `judge.py --preflight` 看证据齐不齐 |
| **判定轮密钥** | 走环境变量 `TESTFLOW_JUDGE_API_KEY`(Windows: `setx TESTFLOW_JUDGE_API_KEY "..."` 之后重开终端)。**不要把密钥写进 `judge-config.json`** —— 那个文件随 skill 入库 |
| **密钥位置** | 模型密钥在 `~/.zcode/v2/config.json` 与 `provider_config.json`,是**机器级**的,不随包走。另有一个可用的远程备选是 BigModel `glm-4.5v`(见第四节) |
| **`device-ios` 依赖爱思助手** | macOS 专属。旧机器上实测:爱思助手进程在运行,但 `computer-use` 的辅助功能与屏幕录制权限都是 denied,**投屏能否反控尚未验证** |
| **adb** | 旧机器上没装。`device-android` 的所有 adb 命令都未在真机执行过 |
| **平台** | 全部脚本纯 Python,不依赖 `sips` / ImageMagick / node,Linux 上应同样可跑。只有 `device-ios` 那条路是 macOS 专属 |
---

## 六、下一步优先级

**不需要任何外部条件就能做的(按价值排序)**

1. **拿两张真配置表跑一次静态交叉** —— 工具已实现(`check_config.py`),缺的只是真表:先 `--dump-spec-template` 生成字段说明,人工补范围与外键,再跑一遍。这是出包前就能出缺陷清单的一层。设计见 `references/10-upstreams.md`。
2. ~~判定轮接真实端点~~ ✅ **已完成**(2026-09-20):真机自检 `scripts/judge_live.py` 一条命令可重跑。**下一步该做的是让判定轮接上真实截图生产流**:拿几张真机截图(或历史截图)走一遍 `run_case.py --execute` → `judge.py`,看 region 划分与问题措辞在真实画面上是否需要调 —— 合成图与真实截图的差别已经实测过(整幅纯色图模型会瞎答)。
3. **异常扫描的下一步**:多机型基线比对(同一用例在不同机型上的截图差异)、启动耗时(需客户端打点)。**先别急着加检测项** —— 现在三类信号都还没在真机上跑过,先按第 4 项接真机。

**需要外部条件的**

4. 真机纵向切片 —— 需要授权 + Android 机 + adb + **游戏入口方案的决定**(adb 无法直接打开微信/抖音小游戏,这是真机自动化最脆弱的一环)。注意:真机纵向切片要出结论,必须先有第 3 项(判定端点),否则截图采了也判不了。
5. 状态上报打通 —— 需要客户端配合。接口约定已写在 `references/7-instrumentation.md`,可直接给客户端同学。**先做 20 行探针**验证小游戏环境内 `wx.request` 能否 POST 到局域网 IP
6. 回放引擎 → 回归集 → 上线门禁 → 线上反馈回流

> **判定轮做完之后补上的一课**:文档里的 `verdict` 词汇表原本写的是 `passed`/`failed`,代码里一直是 `pass`/`fail`;
> 这种漂移没有任何机制能发现,直到 `check_docs.py` 增加了"6-triage 的结果状态表 ↔ `validate_cases.VERDICTS`"的对账。
> 以后加状态,先加常量再加表。

---

## 七、交付前必跑

```bash
bash selftest.sh
```

**这不是可选项。** 这个项目里出现过的所有错误都属于同一类 —— **"写了但没验证"**:声称的算子集合与实际不符、声称的失败模式没实测、文档描述的代码行为与实际不符、声明式字段没人消费。共同点是**缺验证动作**,不是设计错误。

`check_docs.py`(对账)就是针对这个模式加的机制:它机械比对
- 文档里提到的脚本是否存在、是否有孤儿脚本
- `derived-schema.json` 的枚举 与 `validate_cases.py` 的常量是否一致(op / action / fault kind / expect_source / judge / kind / type / priority / review state / evidence)
- `4-conversion.md` 的表格 与 代码常量是否一致
- `6-triage.md` 的结果状态表(verdict 词汇表 + 谁会开 bug)与 `validate_cases.VERDICTS` / `BUG_OPENING_VERDICTS` 是否一致
- SKILL.md 的 CLI 示例引用的路径是否存在

**每次改完文档或代码都跑它。** 它已经抓到过真问题(`unique_by` 文档说"现有算子"、`check_docs.py` 自己是孤儿脚本、verdict 写成 `passed`/`failed` 而代码里是 `pass`/`fail`)。

---

## 八、读文档的顺序

| 你想 | 读 |
|---|---|
| 上手用它 | `SKILL.md`(产物契约、三道闸门、判定模式、工具用法) |
| 理解为什么这么设计 | `references/0-feasibility.md`(可行性与依据,带官方文档链接) |
| 判断能测到什么、不能测什么 | `references/8-platform-limits.md`(**先读文首的三条未查证警告**) |
| 想多源上游(配置表/源码/交叉验证) | `references/10-upstreams.md` |
| 要测重复支付这类缺陷 | `references/11-idempotency.md` |
| 要跟团队对齐全流程与缺口 | `references/9-lifecycle.md` |

`references/` 里每份文档都被 `SKILL.md` 的路由表引用(对账会检查孤儿文档)。
