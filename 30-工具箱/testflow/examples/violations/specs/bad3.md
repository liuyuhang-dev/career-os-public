# 用例: 故意违规的用例之三(after 指向不存在的步骤 + vision-closed 缺 after)

- **ID**: BAD-AFTER-C01
- **来源**: requirement
- **类型**: device-android
- **优先级**: P2
- **判定模式**: vision-closed

## 前置条件

- PRE-1: 账号金币为 0

## 操作步骤

- S1: 进入商城
- S2: 购买一次

## 期望结果

- E1: 顶部金币数值显示为整数
- E2: 屏幕中央出现购买成功提示
