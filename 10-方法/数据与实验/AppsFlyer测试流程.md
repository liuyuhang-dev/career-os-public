# AppsFlyer 接入测试流程

> 适用：海外发行产品上线前，验证 AppsFlyer SDK 是否接入成功。
> 目标：确认自然量安装、非自然量安装、应用内事件三条链路都能正确归因。

## 一、测试目的

AppsFlyer 是三方数据追踪工具。产品上线前必须验证 SDK 接入，否则上线后拿不到转化数据。需要覆盖三类：

1. 自然量安装测试
2. 非自然量安装测试
3. 事件测试

## 二、测试前准备：添加测试设备

先把测试设备的 ID 加入 AppsFlyer 白名单（找平台管理员操作）：

| 平台 | 可用的设备 ID（任选一个） |
|---|---|
| Android | AID、FIRE AID、OAID、IMEI、Android ID |
| iOS | IDFA、IDFV |

## 三、测试方式

### 3.1 测试链接

#### 3.1.1 创建测试链接

Android：

```
https://app.appsflyer.com/<app_id>?pid=Test&c=Test&advertising_id=<GAID>
```

iOS：

```
https://app.appsflyer.com/<app_id>?pid=Test&c=Test&idfa=<IDFA>
```

注意事项：

- Android 的 `app_id` 是包名。
- iOS 的 `app_id` 是 App Store 的 App ID，数字前要加字母 `id`。
- `pid`、`c` 可以自定义，但**不要用真实渠道名**。
- `GAID`、`IDFA`、`IDFV` 换成测试设备的实际值。

示例：

```
https://app.appsflyer.com/com.my.app?pid=Test&c=Test&advertising_id=12345f9*-eba*-421*-876*-7654ac67876*
https://app.appsflyer.com/id123456789?pid=Test&c=Test&idfa=1A2B****9128-4***-123*-04E***654321
```

#### 3.1.2 测试流程

1. 在测试设备上点击上面生成的测试链接。
2. 保持链接不中断，接着安装 App；无论产品是否上架、是否跳转，都必须先点链接。
3. 安装后打开 App，正常体验。

- 测非自然安装：打开 App 后停留一段时间即可。
- 测事件：打开 App 后触发对应事件即可。

### 3.2 使用 AppsFlyer SDK 测试工具

#### 3.2.1 非自然安装测试

1. 登录 AppsFlyer → 选择测试产品 → Settings → SDK → SDK Integration Tests。
2. 选择 **Non-organic install** → Run test。
3. 选择测试设备。
4. 点 **Other**，扫码安装产品。
5. 页面显示成功即通过。

#### 3.2.2 事件测试

1. 登录 AppsFlyer → 选择测试产品 → Settings → SDK → SDK Integration Tests。
2. 选择 **In-app events** → Run test → 选择设备。
3. 打开 App → 点击 “I'm ready to trigger an event” → 在 App 内触发事件。
4. 页面显示成功即通过。

## 四、数据查看

### 4.1 非自然安装

在面板查看归因结果。`appsflyer_sdk_test_int` 是 SDK 测试工具的渠道归因名；`Test`、`X` 是测试链接里自定义的 `pid`。

### 4.2 事件数据

已上架产品事件量较大时，按事件维度查：

1. 登录 AppsFlyer → 选择 App → Analyze → Overview → Events。
2. 选择测试渠道，在事件列表里找到目标事件。

## 五、判定标准

| 测试项 | 通过标准 |
|---|---|
| 自然量安装 | 安装后能在面板看到自然量归因 |
| 非自然量安装 | 点击测试链接后安装，能看到对应 `pid` 的归因 |
| 事件 | 触发事件后，在 Events 里能看到该事件及参数 |

> 常见失败原因：没先点链接就安装、设备 ID 没加白、`pid` 用了真实渠道名、iOS 的 App ID 没加 `id` 前缀。
