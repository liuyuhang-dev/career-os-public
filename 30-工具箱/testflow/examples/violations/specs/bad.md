# 用例: 故意违规的用例之一(期望来源非法 + 孤儿判定项)

- **ID**: BAD-OBSERVED-C01
- **来源**: observed
- **类型**: device-android
- **优先级**: P1
- **判定模式**: vision-closed

## 前置条件

- PRE-1: 无

## 操作步骤

- S1: 进入商城

## 期望结果

- E1: 顶部金币数值显示为整数
- E2: 商城页面显示正常

# 用例: 故意违规的用例之二(缺 req_refs + 漏覆盖期望 + 引用不存在的需求)

- **ID**: BAD-PARTIAL-C01
- **来源**: requirement
- **类型**: device-android
- **优先级**: P2
- **判定模式**: vision-closed

## 前置条件

- PRE-1: 无

## 操作步骤

- S1: 进入商城

## 期望结果

- E1: 顶部金币数值显示为整数
- E2: 购买后金币正确扣减
