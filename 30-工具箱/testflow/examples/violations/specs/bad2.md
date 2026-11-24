# 用例: 期望来源写成代码实现

- **ID**: INV-CODE-C01
- **来源**: code
- **类型**: device-android
- **优先级**: P1
- **判定模式**: state-report

## 前置条件

- PRE-1: 无

## 操作步骤

- S1: 进入商城购买道具

## 期望结果

- E1: 上报的 player.coinDelta 等于 -100

# 用例: 用连续点击模拟重复支付

- **ID**: INV-PSEUDO-C01
- **来源**: invariant
- **类型**: device-android
- **优先级**: P0
- **判定模式**: vision-closed

## 前置条件

- PRE-1: 账号金币 1000

## 操作步骤

- S1: 进入商城
- S2: 连续点击购买按钮 10 次
- S3: 截图

## 期望结果

- E1: 界面是否正常显示,重复支付没有发生

# 用例: 故障注入但只有视觉判定

- **ID**: INV-FAULTVIS-C01
- **来源**: invariant
- **类型**: device-android
- **优先级**: P0
- **判定模式**: vision-closed

## 前置条件

- PRE-1: 账号金币 1000

## 操作步骤

- S1: 进入商城点击购买
- S2: 切断下行网络 2 秒

## 期望结果

- E1: 顶部金币数值显示为整数

# 用例: 一致性用例缺一致性引用

- **ID**: INV-CONSIST-C01
- **来源**: consistency
- **类型**: device-android
- **优先级**: P1
- **判定模式**: state-report

## 前置条件

- PRE-1: 无

## 操作步骤

- S1: 读取结算上报

## 期望结果

- E1: 上报的 order.coinDelta 等于配置值与击杀数的乘积
