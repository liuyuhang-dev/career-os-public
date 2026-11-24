# 异常扫描 run-20260918-184904

- 生成时间: 2026-09-18 18:53:33 中国标准时间
- 运行模式: `plan-only`
- 扫描到的证据: 截图 0 张、logcat 0 份、其它 0 个
- 严重信号: 崩溃 **0**、白屏/黑屏/单色 **0**、画面卡死候选 **0**

> **这些是候选,不是判定,所以本报告不开 bug 单。** 崩溃是高置信信号;白屏与卡死要人复核(加载页、静态菜单都会误报)。要开单,请让用例里有一条对应的 `assert` 判定项,由 `file_bugs.py` 按判定失败开。

## 崩溃信号(高置信)
- 无

## 崩溃可疑信号(中置信,可能是应用自己的日志)
- 无

## 白屏 / 黑屏 / 整屏单色(中置信)
- 无

## 画面卡死候选(相邻截图逐字节相同)
- 无

## 证据读取失败
- 无

## 证据缺口(不是「没有异常」,是「无法判断」)

- `REQ-SETTLE-C01` / screenshot (file_missing) — 计划里的证据文件不存在: <工作区>\.agents\skills\testflow\examples\artifacts\game-demo\04-runs\evidence\REQ-SETTLE-C01_S3_settle.png
- `REQ-NET-C01` / screenshot (file_missing) — 计划里的证据文件不存在: <工作区>\.agents\skills\testflow\examples\artifacts\game-demo\04-runs\evidence\REQ-NET-C01_S6_reconnected.png
- `INV-COIN-C01` / screenshot (file_missing) — 计划里的证据文件不存在: <工作区>\.agents\skills\testflow\examples\artifacts\game-demo\04-runs\evidence\INV-COIN-C01_S3_shop-after-buy.png
- `INV-COIN-C01` / screenshot (no_evidence) — 视觉判定项 E1 没有可用截图,无法判断
- `INV-COIN-C01` / screenshot (no_evidence) — 视觉判定项 E2 没有可用截图,无法判断
- `INV-IDEM-C01` / screenshot (file_missing) — 计划里的证据文件不存在: <工作区>\.agents\skills\testflow\examples\artifacts\game-demo\04-runs\evidence\INV-IDEM-C01_S5_after-retry.png
- `EXP-COIN-C01` / screenshot (file_missing) — 计划里的证据文件不存在: <工作区>\.agents\skills\testflow\examples\artifacts\game-demo\04-runs\evidence\EXP-COIN-C01_S3_explore-shop.png
- `EXP-COIN-C01` / screenshot (no_evidence) — 视觉判定项 E2 没有可用截图,无法判断

## 扫描器判不了的(别拿它当结论)

- **内部状态一致性**(结算精确、存档一致、重复支付)→ 要 `state-report`,见 `references/7-instrumentation.md`
- **启动耗时** → 需要客户端打点,未实现
- **多指手势、帧级时序** → 真机做不了,见 `references/8-platform-limits.md`
- **界面文案与元素是否存在** → 那是判定轮(`judge.py`)的活
