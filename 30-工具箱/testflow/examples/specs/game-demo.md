# 用例: 结算页金币增量等于击杀数乘 10

- **ID**: REQ-SETTLE-C01
- **来源**: requirement
- **关联需求**: R-3.1
- **类型**: device-android
- **优先级**: P1
- **判定模式**: state-report

## 前置条件

- PRE-1: 账号金币为 0,等级 12,进入主界面
- PRE-2: 固定随机种子 20260917,固定逻辑帧步长 16ms,关闭动画

## 操作步骤

- S1: 在主界面点击「开始游戏」
- S2: 完成一局,击杀数达到 8
- S3: 进入结算页,读取客户端上报的结算数据

## 期望结果

- E1: 上报的 player.coinDelta 等于 80(击杀数 8 × 10)
- E2: 上报的 player.killCount 等于 8

# 用例: 击杀数不含召唤物

- **ID**: REQ-SETTLE-C02
- **来源**: requirement
- **关联需求**: R-3.2
- **类型**: device-ios
- **优先级**: P2
- **判定模式**: state-report

## 前置条件

- PRE-1: 使用带召唤技能的角色进入对局

## 操作步骤

- S1: 完成一局,本体击杀 5 个、召唤物击杀 3 个
- S2: 读取结算上报

## 期望结果

- E1: 上报的 player.killCount 等于 5,不包含召唤物击杀

# 用例: 弱网断线重连后状态收敛

- **ID**: REQ-NET-C01
- **来源**: requirement
- **关联需求**: R-3.3
- **类型**: device-android
- **优先级**: P0
- **判定模式**: state-report

## 前置条件

- PRE-1: 账号金币 500,进入对局并完成一次击杀

## 操作步骤

- S1: 对局中触发断网 5 秒
- S2: 恢复网络,等待客户端上报重连收敛完成

## 期望结果

- E1: 上报的 session.state 为 connected
- E2: 上报的 pendingOps 为空数组(离线操作已全部应用或丢弃并达成一致)
- E3: 上报的 player.coin 与断线前一致,为 500

## 弱网条件

- profile: weak
- latency_ms: 800
- loss_rate: 0.15
- disconnect_after_ms: 5000
- assert_convergence: true

## 确定性

- seed: 20260917
- fixed_timestep_ms: 16

# 用例: 金币余额不变负

- **ID**: INV-COIN-C01
- **来源**: invariant
- **类型**: device-android
- **优先级**: P1
- **判定模式**: vision-closed

## 前置条件

- PRE-1: 账号金币为 0

## 操作步骤

- S1: 进入商城
- S2: 对任意标价道具连续点击购买 10 次
- S3: 截图记录顶部金币显示

## 期望结果

- E1: 顶部金币数值显示为不含「-」号且不含非数字字符的整数
- E2: 购买失败时出现包含「金币不足」字样的提示

# 用例: 背包不出现重复条目

- **ID**: INV-BAG-C01
- **来源**: invariant
- **类型**: device-android
- **优先级**: P2
- **判定模式**: state-report

## 前置条件

- PRE-1: 背包内有 3 种道具

## 操作步骤

- S1: 连续领取两次同一封邮件附件
- S2: 读取客户端上报的背包数据

## 期望结果

- E1: 上报的 player.bag 中不存在相同 id 的两行条目

# 用例: 支付响应丢失后的重试不得导致重复扣款与重复发货

- **ID**: INV-IDEM-C01
- **来源**: invariant
- **类型**: device-android
- **优先级**: P0
- **判定模式**: state-report

## 前置条件

- PRE-1: 账号金币 1000,背包无道具 A,使用固定测试商品(价格 100 金币,发放道具 A × 1)

## 操作步骤

- S1: 进入商城,点击购买该商品
- S2: 在支付请求发出后 100ms 切断下行网络,持续 2000ms(模拟响应丢失,客户端会超时重试)
- S3: 恢复网络,等待客户端重试与状态收敛
- S4: 重启客户端后读取状态上报

## 期望结果

- E1: 上报的 order.chargeCount 等于 1,不得为 2
- E2: 上报的 order.coinDelta 精确等于 -100(单次扣款),不是 -200
- E3: 上报的 player.itemA 数量精确等于 1,不是 2
- E4: 上报的 orders 中不存在同一 orderId 的两条记录

## 弱网条件

- profile: flaky
- latency_ms: 800
- loss_rate: 0.15
- reconnect: true
- assert_convergence: true

## 确定性

- seed: 20260917
- fixed_timestep_ms: 16

# 用例: 探索商城购买流程中的异常

- **ID**: EXP-COIN-C01
- **来源**: invariant
- **类型**: device-android
- **优先级**: P2
- **判定模式**: explore

## 前置条件

- PRE-1: 账号金币 500,网络正常

## 操作步骤

- S1: 进入商城
- S2: 随机点击若干商品与购买按钮
- S3: 截图记录当前画面

## 期望结果

- E1: 上报的 session.reconnects 等于 0(探索过程中不应发生重连)
- E2: 顶部金币数值是否显示为不含非数字字符的整数

## 确定性

- seed: 20260917
- fixed_timestep_ms: 16
