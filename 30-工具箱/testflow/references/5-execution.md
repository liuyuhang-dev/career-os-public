# 5 · 执行(阶段 5)

## 真机执行底座只有一个职责:截图 + 输入

平台不给可编程控制 API,所以执行层不依赖任何平台能力:

| 通道 | 截图 | 输入 | 额外 |
|---|---|---|---|
| **device-android**(adb) | `adb exec-out screencap -p` | `adb shell input tap/swipe` | logcat、`tc` 弱网、性能、可脚本化 |
| **device-ios**(投屏) | computer-use 截图 | computer-use 坐标点击 | 无 a11y 树、无 logcat |

**`device-android` 是主通道。** adb 的能力完胜投屏:可编程截图落盘、可抓 logcat、可脚本化批量跑、弱网可精确构造。

`device-ios` 的投屏可控性**尚未验证**,实现前先确认(见文末)。

## 坐标纪律(真机测试最容易翻车处)

1. **用例里只存归一化比例(0..1),禁止硬编码像素。** 像素在换机型、换分辨率、换投屏窗口尺寸后全部失效。

2. **必须先探测设备逻辑分辨率与画面实际矩形。**
   - Android:tap 坐标与截图同为物理像素(当前旋转下),1:1 通常成立。陷阱:`wm size`/density 覆盖、多屏 `-d`、状态栏/导航栏是否计入截图、旋转
   - iOS:**points ≠ pixels**,`pixels = points × scaleFactor`(1/2/3 倍)。触摸 API 用 points,截图是 pixels
   - 投屏窗口:必须探测设备画面在窗口内的**实际矩形**,处理缩放与黑边

3. **优先锚点相对定位而非绝对坐标。** 先定位 landmark 再按相对偏移点击,跨机型容错高得多。

4. **探测结果要落盘并写进报告。** 每次运行的设备分辨率、dpi、截图尺寸、窗口映射都要记录,否则坐标类失败无法复盘。

用 `scripts/coordmap.py` 做换算,不要手算:

```bash
python3 "$SKILL_DIR/scripts/coordmap.py" --profile android-mid --norm 0.5,0.91
python3 "$SKILL_DIR/scripts/coordmap.py" --profile iphone-14 --norm 0.5,0.91 --window-rect 0,0,480,1040
python3 "$SKILL_DIR/scripts/coordmap.py" --profile android-mid --selftest
```

## 输入能力的硬限制(必须提前接受)

`adb shell input tap` 只注入一对 down/up MotionEvent:

- **无压力值**
- **无法真正多指同时操作** —— 边移动边瞄准、双摇杆、捏合手势做不到
- `input swipe` 用合成插值,很多手势识别器会拒绝
- 注入事件与渲染线程存在竞态:**"tap 成功"不代表游戏注册了这次输入**

→ **多指与帧级时序用例真机做不了。** 这类用例标 `kind: "manual"` 写明原因,或走游戏内注入输入事件(见 `7-instrumentation.md`)。不要假装它们能自动测。

替代方案排序(需要真多指时):Appium W3C Actions > 游戏内注入输入事件 > `sendevent`(需 root)。**真机坐标注入不是万能替代。**

## 弱网怎么造

| 平台 | 手段 | 可自动化 | 精度 |
|---|---|---|---|
| Android | `adb shell tc` / netem 设备侧限速丢包(**通常需 root**) | 可脚本化 | 高 |
| Android | 代理(mitmproxy/Charles)限速 | 可脚本化 | 中 |
| iOS 真机 | 代理限速 / AP 或路由器侧限速 | 部分 | 中低 |
| iOS 真机 | ~~Network Link Conditioner~~ **只对模拟器生效** | 不适用 | —— |

用例里的 `network` 块执行时映射为上述手段。**iOS 侧的精度差异必须写进报告**,不能与 Android 结论混为一谈。

## 证据采集:证据决定判定能不能信

**没有证据 → 判定只能是 `blocked`,不许是 `pass`。** 这条由执行器与判定器共同强制。

每次运行采集:

| 证据 | Android | iOS 投屏 |
|---|---|---|
| 截图序列 | `exec-out screencap -p` 落盘,按步骤命名(动作 `screenshot`) | computer-use 截图 |
| 动作轨迹 | 实际执行的坐标与时刻(供固化回放) | 同 |
| 结构化日志 | logcat(动作 `logcat` 落盘;可按 tag 过滤) | 无 |
| 性能数据 | `dumpsys gfxinfo` / `dumpsys meminfo`(动作 `perf`)—— **宿主进程级**,见下 | 无 |
| 任意 adb 输出 | 动作 `adb_shell` 加 `save_as` → 落成 `.txt` 证据(包版本号、机型信息、系统设置…) | 无 |
| 崩溃 | logcat 崩溃栈(由 `scan_anomalies.py` 扫) | 无 |
| 状态上报 | HTTP 采集端(见 `7-instrumentation.md`) | 同(跨平台) |

**`perf` 动作量到的是宿主进程,不是小游戏自己。** 小游戏跑在微信/抖音进程里,
`dumpsys gfxinfo com.tencent.mm` 给的是宿主进程的帧数据(总帧数、janky 帧比例、
分位帧耗时),`dumpsys meminfo` 给的是宿主的 TOTAL PSS。**跨机型、跨版本对比仍然成立,
但别拿它当"小游戏的帧率"汇报。** 想要小游戏自己的数据,得靠客户端打点。

截图命名要能回溯到步骤:`<用例ID>_<步骤ID>_<序>.png`,如 `INV-COIN-C01_S2_03.png`。
logcat 同理:`<用例ID>_<步骤ID>_<名称>.txt`。

**入口是最脆弱的一环,但不再是"黑箱第一步"。** `adb` 打不开微信/抖音小游戏(无平台 API),
入口必须人工配合(预置到宿主首页或扫码)。所以 `open_entry` 动作会**顺带生成一条判定请求**:
"屏幕上是否已处于微信/抖音首页(可见底部导航栏)?" —— 入口这一步自动化不了,但**能标出从哪一步
开始不可信**:该判定答否,后面的步骤与证据都不算数(判定轮会给 blocked,而不是默默继续)。

**声明的证据类型必须真有采集动作。** `run_case.py` 会核对用例的 `evidence` 列表与实际动作:
声明了 `logcat` 却没有 `logcat` 动作,结果里会写进 `evidenceGaps`(kind=`no_action`);
`perf`/`crash`/`trace` 目前**没有采集通路**,声明了就是能力缺口(kind=`no_collector`)。
缺口会进扫描报告,表现为"**无法判断**" —— 而不是"没有异常"。

## 玩与判分离(落地方式)

`run_case.py` 的实际分工:

1. **生成执行计划** —— 把用例翻译成设备命令序列。默认**只出计划不执行**(`--execute` 才真跑)
2. **执行并采集 `actual`** —— 原始观测值,不含判定。**永不输出通过/失败**
3. **生成判定请求** —— 把每个 `assert` 判定项转成判定任务:
   - `vision-closed` → 写出「截图路径 + 封闭问题 + region」,交给**独立的判定轮**
   - `state-report` → 写出「观测路径 + op + value」+ 上报数据路径,由代码判定
   - `replay` → 轨迹比对,由代码判定
   - `judgment` → 写出问题 + 证据,交判定轮
   - `manual` → 直接标 `pending_manual`

判定轮与执行轮**必须是不同的调用**。同一个视觉智能体既玩又判,就是自己出题自己答题自己判卷。

判定轮就是 `scripts/judge.py`:吃 `run-<ts>.judge-requests.json` + 截图,按 `region` 裁剪后送视觉端点,只接受明确的 yes/no,把 verdict 回填进 run json 并写判定日志。它的输入**不含执行轮的结论**(prompt 里没有计划 notes、没有其它判定项结果),也不含判定项声明的期望答案 —— 期望由代码比对,免得模型顺着期望附和。细节见 `references/6-triage.md` 的「判定轮的实现」。

## 探索模式(`judge: "explore"`)

**不判通过/失败,只报异常。** 这类用例的产出是 bug 候选,不是 pass/fail。

- 判定模式与回归用例分开执行、分开统计
- 不参与通过率
- 目标指标是**发现率**和**误报率**,不是通过率
- 跑法:按界面地图系统遍历 + 随机操作混合;记录崩溃、卡死、白屏、无响应、异常弹窗、UI 错乱

**别让"发现"只依赖模型印象。** 崩溃、白屏/黑屏、画面卡死都有确定性信号,由
`scripts/scan_anomalies.py` 从证据里扫,不需要模型:

| 异常 | 判据(确定性) | 置信度 |
|---|---|---|
| 崩溃 / ANR | logcat 里的 `FATAL EXCEPTION`、`ANR in`、`Fatal signal N (SIG...)`、tombstone 等 | 高(系统层报的) |
| 白屏 / 黑屏 / 整屏单色 | 截图抽样像素里 ≥98% 接近纯白或纯黑,或 ≥99.5% 是同一个颜色 | 中 —— 加载页、白底弹窗会误报,要人复核 |
| 画面卡死 | 同一用例相邻截图**逐字节完全相同**,且落盘间隔 ≥ `--frozen-min-secs` | 中 —— 静态菜单/暂停界面也会这样 |
| 启动耗时 | logcat 里系统自己打的 `ActivityTaskManager: Displayed <组件>: +1s234ms`(老版本是 `ActivityManager: Fully drawn`) | 高(数字确定)—— 超 `--slow-start-ms` 记慢启动;**不做客户端改造就能采** |
| janky 帧比例 | `dumpsys gfxinfo` 里的 `Janky frames: 150 (12.50%)` 与分位帧耗时(p50/p90/p95/p99) | 高(数字确定)—— 超 `--janky-pct-max`(默认 10%)记候选;**量的是宿主进程** |
| 内存 | `dumpsys meminfo` 里的 `TOTAL PSS`(换算成 MB) | 高(数字确定)—— 阈值默认关(`--mem-max-mb` 打开);**量的是宿主进程** |

**启动耗时与性能都能做基线对比**(对应 `9-lifecycle.md` 的"启动耗时不超过基线 120%"):

```bash
# 第一次:量下来存成基线(键是「指标:对象」,如 launch_ms:com.tencent.mm/.ui.LauncherUI)
python3 "$SKILL_DIR/scripts/scan_anomalies.py" --run <run>.json --save-baseline baselines/android-mid.json
# 之后每轮:与基线比,超过基线 ×(1+tolerance) 就是退化候选
python3 "$SKILL_DIR/scripts/scan_anomalies.py" --run <run>.json --baseline baselines/android-mid.json
```

**基线要按机型存档**(不同档位的启动耗时与帧率不可比),换机型就重新建一次基线。
单次采样会受设备状态影响,**判退化前多轮取中位数**;基线里存的正是中位数。

要采到这些信号,用例动作里得有 `screenshot` 与 `logcat` 动作(见 `4-conversion.md` 的动作词汇表)。
**声明了 logcat 却没有采集动作,报告里会出现"证据缺口:无法判断"** —— 那不是"没有异常",是没得判。

扫描器输出 `anomalies.md` 与 `--json`,**发现是候选,不开 bug 单**;要开单得有用例里的
`assert` 判定项(比如"不崩溃"这类不变量),由 `file_bugs.py` 按判定失败开。

## 跨轮界面回归(确定性,不需要模型)

`compare_screens.py`:同一用例同一步骤的截图按**文件名主干**配成对,算差异比例、
归一化变化区域与 6×6 分布,并可写出差异图(未变化处压暗、变化区块标红)。

```bash
# 首次:把这一轮的截图存成基线(按机型存档)
python3 "$SKILL_DIR/scripts/compare_screens.py" --save baselines/android-mid/shots --cur <run>.json
# 之后每轮:与基线比
python3 "$SKILL_DIR/scripts/compare_screens.py" --base baselines/android-mid/shots --cur <run>.json   --diff-dir test-artifacts/<f>/04-runs/diffs --out test-artifacts/<f>/screen-diff.md
```

**差异是候选,不是缺陷。** 倒计时、特效、随机数、服务器时间本来就会让画面变 ——
所以报告给的是"变了多少、变在哪",由人判断该不该变。工具不开 bug 单。

- 退出码 `3` = **无法比对**(基线里有的这轮没采到 / 读不了图),别读成"没差异"
- 尺寸不一致会单独报:那通常意味着机型/分辨率或投屏窗口变了,先确认该不该比
- 这是当前**唯一**可用的跨轮视觉回归手段(`replay` 判定模式仍未实现)

## 探索 → 回放固化
探索跑通的轨迹应能固化成 `replay` 用例,这是把**不可复现的发现**变成**可复现的回归资产**的机制。

```
捕获轨迹(步骤 + 归一化坐标 + 时刻 + 截图)
  → 写入 traces/<case-id>.json
  → 生成用例骨架,judge 设为 "replay"
  → 人复核后进入回归集
```

固化时要**剥离不确定性**:随机操作要替换为确定步骤,等待要改成 `wait_for`(等条件而非等固定时长)。

## 执行前检查清单

- [ ] 三道闸门全过(`validate_cases.py` 退出码 0)
- [ ] 目标用例 `review.state == "approved"`(未批准 → `blocked: not_reviewed`)
- [ ] 设备连接正常、电量充足、已登录宿主账号
- [ ] 设备分辨率/dpi 已探测并记录
- [ ] 游戏已能手动拉起(入口可用)
- [ ] 宿主通知/来电/系统弹窗已关闭或已授权(否则会污染截图与输入)
- [ ] 弱网用例:限速手段已就位并验证生效

## iOS 通道前置验证(实现前必做)

爱思助手的投屏**是否支持反控尚未验证**。验证步骤:

1. 授权 computer-use 的**辅助功能**与**屏幕录制**:
   `/Users/liuyuhang/.zcode/computer-use/ZCode Computer Use.app`
2. 截图看投屏窗口结构,确认设备画面实际矩形(是否有黑边、缩放比)
3. 试**一次极小范围的点击**(点空白区,不触发业务),看设备是否有可见响应
4. 结论:
   - 可控 → 实现 `device-ios` 执行器
   - 只能看 → 转 WebDriverAgent(需签名配置)或放弃 iOS 自动化通道,**不要假装可用**

## 绝不伪装成 pass

未实现或不可用的能力一律:

```json
{ "status": "skipped", "reason": "executor not implemented: device-ios" }
```

这条同时是**能力缺失的显式暴露机制**——报告里 `skipped` 的比例就是"这套东西实际覆盖了多少"的诚实指标。
