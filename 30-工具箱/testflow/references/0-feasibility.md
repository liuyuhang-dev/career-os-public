# 0 · 可行性结论与依据

这份文档是**结论档案**,不是教程。目的是让方案讨论不再重复已经查证过的事实,也让人能一眼分清哪些是官方文档支持的、哪些是推断、哪些还没验证。

查证时间:2026-09。平台文档会变,结论需要定期复核(尤其抖音侧)。

## 一句话结论

Unity + 微信小游戏 + 抖音小游戏这个组合下,**客户端自动化没有捷径,只能自己注入 + 外部视觉驱动**;但真机测试完全可行,受限的是帧级时序和多指操作,不是整体可行性。

---

## 一、微信小游戏

### 有官方自动化框架,但 Unity 用不了

微信官方的小游戏自动化框架是 **MiniGameTest**(小游戏云测试),与 `miniprogram-automator` 是两套东西——后者只服务小程序,API 全是 `Page`/`Element` 页面树模型,小游戏没有页面树。

MiniGameTest 不看 DOM,而是读**引擎渲染节点树**。API 面:`findPath()` / `findImg()` / `select()` / `tap(x,y,isPer)` / `swipe()` / `waitForNode()` / `snapshot()` / `assertNodeSelector()` / `assertImgSelector()` / `runRecordCase()`。有 JS 和 Python 两套 SDK。

**硬前提**:节点树需要引擎按接入规范实现。官方明确列出只有 **Cocos Creator / Laya / Egret** 已接入,**Unity 不在列表**。

运行方式:本地模拟器 `cli auto --project <path> --auto-port <port>` + `-t ide`;云真机则上传 zip(根目录放测试入口)。

- 依据:https://developers.weixin.qq.com/minigame/dev/minigame-testtool/minigametest.html
- API:https://developers.weixin.qq.com/minigame/dev/minigame-testtool/api/javascript.html
- 前提(开服务端口):https://developers.weixin.qq.com/minigame/dev/minigame-testtool/setup.html

### 真机调试:能看,不能编程驱动

支持真机运行、Console 调试、Sources 断点;小游戏额外有 JS 上下文切换面板("Contexts")。

**但**:没有可编程的注入 API,没有截图能力(`wx.onUserCaptureScreen` 在设备上标为不支持)。真机调试 2.0 仅 iOS、需 Nightly 版工具、单设备、需高性能模式、Mac 侧需 libplist/libusbmuxd/libimobiledevice。

→ 只能人工调试。**作为自动化控制面不可用。**

- 依据:https://developers.weixin.qq.com/minigame/dev/devtools/remote-debug.html
- 2.0:https://developers.weixin.qq.com/minigame/dev/devtools/remote-debug-v2.html

### 云测:对 Unity 项目唯一可用的官方真机路径

三种模式:智能探索测试(无脚本,算法探索)、自定义测试(录制回放 / MiniGameTest 脚本)。

- 跑在**云真机**,和真实玩家设备环境相同,目标是体验版(对正式用户无影响)
- 报告:启动性能 / 运行性能 / 网络性能 / 兼容性 + 截图 + Console log + CPU profile
- 快速模式 10 分钟 / 完整模式 30 分钟
- 云测环境内 `GameGlobal.isTest === true`,可据此让游戏走测试分支
- 评测标准有独立的「Unity」档,说明 Unity 项目是被支持的(但走的是性能评测,不是节点树)

**免费额度与定价官方未公开。** 需用开发者账号在后台确认。

- 依据:https://developers.weixin.qq.com/minigame/dev/minigame-testtool/overview.html
- 任务设置:https://developers.weixin.qq.com/minigame/dev/minigame-testtool/task-setting.html

**GitHub 仓库 / 小程序代码注意**:小游戏代码片段上限 1MB,真机打开主要靠扫码。

---

## 二、抖音小游戏

### 没有任何官方自动化能力

- CLI `tt-minigame-ide-cli`(bin `tmg`)只有 `login / open / preview / upload / build-npm / version / set-config`。**不执行测试**,无本地执行能力 → CI 只能构建和上传。
- 真机调试有 Console / Sources / Profiles(**iOS 不支持 Profiles**),**无可编程 JS 注入 API,无截图**。
- **「高性能 / 高性能+」模式与真机调试互斥** → 生产渲染模式无法调试。
- 权限门槛:设备需在【开发设置-调试管理】白名单;抖音账号绑定**一次性且不可解绑**;测试号 AppID 需先绑定抖音账号。
- **无官方云测**。只有第三方(WeTest 云手机 / Testin / 优测)。

### 成熟度差距(量化)

| 包 | 平台 | 月下载 |
|---|---|---|
| `miniprogram-automator` | 微信官方 | 54,782 |
| `minigame-api-typings` | 微信小游戏 | 482,789 |
| `tt-minigame-ide-cli` | 抖音小游戏官方 CLI | 576 |
| `tt-ide-cli` | 抖音小程序 CLI | 5,651 |
| `douyin-devtools-mcp`(社区逆向) | 抖音 | 16 |

GitHub 上 `douyin minigame` 相关仓库共 9 个,自动化相关的都是实验性质。

### 但运行时架构与微信同源

抖音小游戏是 JS VM,**无 BOM、无 DOM**,只有 `tt` 系列 API;`tt.createCanvas()` 单画布;全局对象 `GameGlobal`;开放数据域机制与微信一致。

Cocos Creator / Laya / 团结引擎 / Godot 社区版都能一份代码同时导出微信 + 抖音。

**结论:代码可复用,调试桥不通——不要指望同一个测试 harness 同时驱动两端。**

- 依据:https://developer.open-douyin.com/docs/resource/zh-CN/mini-game/develop/guide/dev-guide/bytedance-mini-game
- CLI:.../mini-game/develop/developer-instrument/development-assistance/ide-cli
- 真机调试:.../mini-game/develop/developer-instrument/development-assistance/real-machine-adjust

---

## 三、行业共识:别把坐标视觉当主力

这一条是整个方案里最重要、也最容易被产品直觉推翻的结论。

**证据链:**

- Google Firebase 官方文档直言:*"For games built with game engines/frameworks and engines, such as Unity, Unreal, or OpenGL/Vulkan C++ code, standard automation tools cannot inspect or interact with UI elements."*
  https://firebase.google.com/docs/test-lab/android/game-loop
- Unity 官方自动化示例**明确劝阻图像识别**,建议把游戏内事件以 JSON 打到日志、由测试脚本解析;元素位置由游戏自己上报。
  https://github.com/UnityTech/GamesTestAutomationExample
- Firebase **Game Loop** 的设计最能说明问题:Test Lab 完全**不碰 UI**,只用 intent + **场景编号**启动游戏内预置逻辑,自己只做崩溃/超时监控和结果日志收集。
- **Lap** 论文(2507.09490)自述:*"current LLMs cannot visually perceive game environments"*、*"many non-text games lack APIs to provide textual descriptions of game states"* → 它的取胜做法是把游戏状态转成数字矩阵,而不是看图。
- **OSWorld**(2404.07972):人类完成率 **72.36%** vs 最强模型 **12.24%**,*"primarily struggling with GUI grounding"*。
- **modl.ai** 是纯视觉 + OCR 的盲测机器人,自述最适合 *"match, narrative, card or turn-based games"*,且 *"Very fast-paced or timing-critical gameplay isn't fully supported yet"*。
- 游戏测试综述(arXiv 2103.06431):开发者*"almost exclusively"*依赖人工测试;2202.12777 指出难点在**测试判定(oracle)**,不在输入生成。

**成熟做法(工作室真实在用的):**

| 工具 | 机制 |
|---|---|
| AltTester(Unity/Unreal) | 往构建里塞测试引擎,对象级定位 |
| Gamium(dogu) | 同上,README 说得很直白:*"put the gamium engine into the game build"* |
| 腾讯 GAutomator | Unity/UE4;iOS 侧用 WebDriverAgent |
| 网易 Airtest + **Poco** | Poco SDK 往 Unity/Cocos/UE 注入**可查询元素树**,游戏对象像 DOM 节点一样可定位 |

**这些的共同点:对象级、分辨率无关的定位与状态断言——这才是真正解决 canvas 问题的办法。**

### 但视觉仍然是必需的

以上结论不等于"不要用视觉"。真机执行底座只有视觉可用(平台不给 API)。而**探索性测试、崩溃发现、UI 错乱、流程断裂**这类目标恰恰只需要视觉,且不要求可复现。

所以正确理解是**分工**,不是替代:

- 视觉负责:**驱动**(点哪里)+ **画面健康**(白屏/花屏/遮挡)+ **探索发现**
- 不变量 / 回放 / 状态上报负责:**可复现的判定**

### 视觉方案的量化边界

- **适合**:回合制、卡牌、模拟经营、慢节奏、导航类、菜单流程
- **不适合**:快节奏动作/竞技(帧级时序,VLM 秒级 vs 游戏 60fps)、多指同时操作、密集小目标 UI
- **成本**:按每秒一个动作算,10 分钟对局 ≈ 600 次 VLM 调用
- **坐标系**:不存在"零成本通用"的坐标方案,必须按设备探测 + 归一化

---

## 四、坐标与像素:必然踩的两个坑

**iOS 是 points ≠ pixels。** Apple 明确说一个 point 不一定对应一个物理像素,`pixels = points × scaleFactor`(1.0 / 2.0 / 3.0)。触摸 API 用 points,截图是 pixels。差 2~3 倍。

**Android 通常 1:1**,tap 坐标与 `screencap` 都是物理像素(当前旋转下)。陷阱在:`wm size`/density 覆盖(兼容模式)、多屏 `-d`、状态栏/导航栏是否计入截图、旋转。

**正确做法(与平台无关):** 坐标一律存**归一化比例**,从实际截图尺寸算出像素;按设备/OS 版本校准;优先**锚点相对定位**(先定位一个 landmark,再按相对偏移点击),而不是绝对坐标。

**adb 输入的硬限制:**
- `input tap` 只注入一对 down/up MotionEvent:无压力、**无法真正多指同时操作**(边移动边瞄准、双摇杆、捏合)
- `input swipe` 用合成插值,很多手势识别器会拒绝
- 注入事件与渲染线程存在竞态:"tap 成功"不代表游戏注册了这次输入

→ **多指与帧级时序用例真机做不了,需要游戏内注入输入事件。**

---

## 五、弱网(本项目是弱网游戏,此项为必测)

| 平台 | 手段 | 可自动化 |
|---|---|---|
| Android | 设备侧 `tc`/netem(通常需 root);或代理(mitmproxy/Charles)限速 | 可脚本化,推荐 |
| iOS 真机 | **Network Link Conditioner 只对模拟器生效**;只能代理或 AP/路由器侧限速 | 部分,须标注为限制 |
| 云真机 | 多数云手机平台提供网络档位 | 依赖平台 |

**精度 Android 明显优于 iOS。这个差异必须在用例与报告里如实体现,不能假装两端等价。**

---

## 六、像素回归在游戏里的正确定位

- 渲染受 OS、版本、设置、硬件、电源影响;Playwright 官方说截图因浏览器与平台而异,需要按平台维护基线
- Chromatic 默认 `diffThreshold = 0.063`,用于平衡精度与抗锯齿假阳性
- **游戏里动态元素太多**,整屏 diff 必然假阳性

**正确用法:带遮罩、带阈值、限定静态页面(加载页/结算页/商城页)的发版冒烟信号**,用于白屏/黑屏/资源缺失/严重错位。**不是功能判定依据。** 对局中永远不要做整屏 diff。

**稳定化手段:** 固定随机种子、固定时间步长、固定分辨率与密度、同 GPU 驱动维护基线、关动画、遮罩动态区域(计时器、特效、视频面、广告位)、阈值化感知 diff。

---

## 七、云真机市场现状

| 服务 | 自动化框架 | 游戏相关 |
|---|---|---|
| Firebase Test Lab | Espresso/UIAutomator、XCTest、Robo、**Game Loop** | **唯一有游戏专用原语**(Game Loop) |
| 腾讯 WeTest | 云手机(ADB 模式、远程调试、截图日志)、**PerfDog**(FPS/卡顿)、GAutomator | **最偏游戏**:性能 + 云真机 + 游戏自动化 SDK |
| AWS Device Farm | Appium、Instrumentation、XCTest、fuzz | **无游戏支持** |
| BrowserStack | Appium、XCTest、App Live、Percy 视觉 diff | **无游戏感知**;游戏只能远程人工 |
| 阿里云 EMAS/MQC | —— | 现状无法从公开页面确认 |

**共同点:它们提供设备、安装、截图、日志、崩溃、视频和性能数据,以及一个"跑你脚本的传输层"。没有任何一家替你做游戏功能测试。**

`uiautomator dump` 对 canvas 游戏**结构性地无效**——游戏渲染进单个 SurfaceView/GLSurfaceView/CAMetalLayer,只有一个无子节点、无文本的不透明节点。即使 WebView 内画布也常有层级合并/断裂问题。

---

## 八、还没验证的事(不要当成已知)

1. **小游戏环境内自注入通道的可行性** —— 小游戏是 WebGL/wasm 跑在无 DOM/BOM 的 JS VM 里,AltTester/Gamium 依赖原生 socket,**很可能在小游戏环境内不可用**,只适用于原生构建与编辑器。可行路径是 `GameGlobal` 暴露 + 官方调试通道 + 自跑自检 + 结构化日志,**但这条没有官方文档背书**。
2. **爱思助手投屏能否反控** —— 仅确认其进程在运行,可控性未验证。
3. **微信云测对 Unity 小游戏的功能自动化覆盖到哪** —— 官方只在评测标准里列了 Unity,节点树明确不支持。
4. **微信云测免费额度与定价** —— 官方未公开。
5. **抖音是否有非公开的云测入口** —— 公开文档里没有,但不能排除控制台内路径。
6. **微信小游戏虚拟支付能否自动化测试** —— 官方未见沙箱/自动支付测试说明,两边结论都不成立。
7. **Android `wm size`/density 与 `input` 子命令的精确语义** —— 未取到一手文档,按工程实践处理。

---

## 九、由此确定的架构决策

| 决策 | 理由 |
|---|---|
| 真机为第一目标,编辑器无头为辅助 | 目标是真机表现,headless 只做快速反馈 |
| Android 为主通道,iOS 为次 | adb 能力完整(可编程截图/logcat/tc 弱网/脚本化);iOS 受投屏可控性制约 |
| 判定四模式按可复现性递进,而非单一手段 | 视觉够用于显示层,内部一致性需状态上报 |
| 期望来源三分,禁止观察行为 | 防把现有 bug 固化成期望 |
| 玩与判分离 | 防自己出题自己判卷 |
| 坐标只存归一化比例 | 防换机型全废 |
| 探索 → 回放固化 | 把不可复现的发现变成可复现的回归资产 |
| 未实现的能力返回 skipped 而非 pass | 能力缺失必须显式暴露 |
