# iOS 打包：Unity 参数、签名与 CI 任务

iOS 产物一律从 Unity 的 macOS 版导出 Xcode 工程，再由 Xcode 出 IPA / 上架。过程日志不入库，这里只留结论与可复用的参数口径。

## 1. 工程内的打包参数（`app/ProjectSettings/ProjectSettings.asset`）

| 项 | 值 | 来源 |
| --- | --- | --- |
| Bundle Identifier | `com.example.game` | App Store Connect App ID `<ASC_APP_ID>`（App ID Name = Example Game） |
| Team ID | `<APPLE_TEAM_ID>` | <COMPANY_LEGAL_NAME> |
| 签名方式 | 手动分发签名：`appleEnableAutomaticSigning: 0` + `iOSManualSigningProvisioningProfileID: 5881a81f-7f73-4066-8b4a-43f71d71b325` + `iOSManualSigningProvisioningProfileType: 2`（Distribution） | 固定 profile「Example Game」的 App Store 描述文件，CI 与本地产物签同一份 |
| 最低系统版本 | iOS 15.0（`iOSTargetOSVersionString`） | 工程设置及 CI / 本地导出入口固定为 15.0 |
| 架构 / SDK | Device SDK、arm64 | iOS 只支持 IL2CPP + arm64 |
| 显示名 | `productName: Example Game` | 图标下显示的应用名 |

Android 包名同步为 `com.example.game`（原 `com.example.game`），两端用同一 bundle id 便于配置与埋点对照。**这是换包**：装了旧 `com.example.game` 的机器不会把它当升级，内部测试机器需卸载重装。

## 2. 打包入口

| 入口 | 用途 |
| --- | --- |
| `Axiom/Build iOS` 菜单 | 本地导出到 `app/Build/iOS/<bundle>-<version>-<buildCode>`，只在 macOS 编辑器里出现（其他平台点了给提示） |
| `AppBuild.IOS`（`-executeMethod`） | CI / 命令行入口，参数全走环境变量，结束时 `EditorApplication.Exit` |
| `scripts/build-app-ios.sh` | 定位 Unity、对齐工程版本、校验 iOS 模块、导出、打 zip（或出 IPA） |
| `ios` CI 任务 | 手动触发（`when: manual`），tag `mac-unity`，先编原生库与 wasm |

`scripts/lib-unity-build.sh` 是 Android / iOS 两个脚本的公共部分（Unity 定位、工程版本对齐、缓存清理），行为与原先 Android 脚本内的一致。Unity 生成目录（`Library` / `Temp` / `Logs` / `.utmp`）就地留在工程目录，不再软链到其他盘。

## 3. 环境变量

| 变量 | 说明 |
| --- | --- |
| `UNITY_EDITOR` | Unity 可执行文件路径（CI 必填） |
| `DL_DIR` | 产物根目录；`IOS_DL_DIR` 可覆盖产物目录；日志优先写入 `APK_DL_DIR`，未设置时使用 `LOG_DL_DIR`，再回退到产物目录 |
| `IOS_BUNDLE_ID` | 覆盖 Bundle Identifier（缺省取工程设置） |
| `APP_VERSION` / `BUILD_CODE` | `CFBundleShortVersionString` / `CFBundleVersion`（缺省取工程设置 / `CI_PIPELINE_ID`） |
| `IOS_TEAM_ID` | 覆盖 Team ID（缺省取工程设置） |
| `IOS_SIGNING` | `manual`（缺省，取工程设置）/ `automatic`；给了 `IOS_PROVISIONING_PROFILE` 也按 manual 走 |
| `IOS_PROVISIONING_PROFILE` / `IOS_PROVISIONING_PROFILE_TYPE` | 手动签名的 profile 与类型（`development` / `distribution`）；缺省取工程设置（当前是 Distribution 的 App Store profile）。显式传 profile 而未给类型时按 `development` |
| `IOS_EXPORT_PATH` | Xcode 工程导出目录，缺省 `app/Build/iOS/<bundle>-<version>-<buildCode>` |
| `IOS_EXPORT_IPA` + `IOS_EXPORT_OPTIONS_PLIST` | 置 1 时导出后再 `xcodebuild archive` + `-exportArchive` 直接出 IPA |
| `IOS_DERIVED_DATA_DIR` | archive 的 DerivedData 目录，缺省 `~/Library/Developer/Xcode/DerivedData/merge-ios-<name>`（必须是本机 APFS，不能放 exFAT 构建盘） |
| `IOS_UPLOAD` | 置 1 时把 IPA 上传到 App Store Connect（处理后进 TestFlight），会自动打开 IPA 导出；只上传、不自动提交审核 |
| `ASC_KEY_ID` / `ASC_ISSUER_ID` / `ASC_API_KEY_PATH` | App Store Connect API Key（推荐）。`ASC_API_KEY_PATH` 指向 `.p8`，脚本会复制到 `~/.appstoreconnect/private_keys/AuthKey_<KEY_ID>.p8` |
| `ASC_USERNAME` / `ASC_APP_PASSWORD` | 回退方案：Apple ID + App 专用密码（需两个都给；API Key 存在时优先用 API Key） |
| `CLEAN_UNITY_CACHE` | 置 1 时先删掉 `app/Library` 与 `app/Packages/packages-lock.json` 再打包（默认 0，复用缓存；仅 CI 生效） |

## 4. 产物

- 默认：`{IOS_DL_DIR}/{bundle}-{version}-{buildCode}-xcodeproj.zip`（解压后用 Xcode 打开 `Unity-iPhone.xcodeproj`）
- `IOS_EXPORT_IPA=1`：`{IOS_DL_DIR}/{bundle}-{version}-{buildCode}.ipa`
- `IOS_UPLOAD=1`：先出 `...ipa`，校验通过后上传到 App Store Connect（TestFlight 里可见；不提交审核）
- 日志：`{LOG_DL_DIR}/{bundle}-{version}-{buildCode}.log`（Unity、xcodebuild、altool 共用）

脚本在交付前会核对：`Unity-iPhone.xcodeproj` 存在、导出的 `Info.plist` 里 `CFBundleIdentifier` 与本次包名一致（防止复用导出目录拿到上一轮的 plist）、日志里没有 Addressables 构建失败。Addressables 资源（`Data/aa/settings.json`）缺失只告警；逻辑层原生库由 Unity 构建后处理严格校验，缺库、缺少真机 arm64 slice 或工程未引用时立即失败。iOS 仅支持 Native，不允许用 `AXIOM_NATIVE_PLUGIN=skip` 绕过最终校验。

## 5. 签名

- **当前配置：手动分发签名**。`app/ProjectSettings/ProjectSettings.asset` 里固定了 App Store 描述文件：

  | 项 | 值 |
  | --- | --- |
  | Profile 名称 / App ID 名 | Example Game |
  | App ID | `<ASC_APP_ID>` |
  | Team | <COMPANY_LEGAL_NAME> |
  | Profile UUID | `5881a81f-7f73-4066-8b4a-43f71d71b325` |
  | 类型 | Distribution（App Store，带 `beta-reports-active`） |
  | 创建 / 到期 | 2026-10-29 / **2027-08-07** |

  脚本和 `AppBuild.IOS` 在 `IOS_SIGNING` / `IOS_PROVISIONING_PROFILE` 都没设置时直接读工程设置，所以 CI 与本地菜单签的是同一份 profile，不需要在 CI/CD 变量里再存一份 UUID。打包机需装好该 profile（`~/Library/MobileDevice/Provisioning Profiles/5881a81f-7f73-4066-8b4a-43f71d71b325.mobileprovision`）与对应 Apple Distribution 证书；`xcodebuild` 那侧仍带 `-allowProvisioningUpdates`，缺证书时可由登录团队账号的 Xcode 补齐。
- **回到自动签名**：`IOS_SIGNING=automatic`（或在工程里把 `appleEnableAutomaticSigning` 改回 1、清空 profile 字段）。此时要求打包机在 Xcode 里登录 <COMPANY_LEGAL_NAME> 团队的 Apple 账号；脚本会清空残留的 profile id，避免 Xcode 工程同时「自动管理」和「写死 profile」。
- **换 profile**：只改工程设置（或传 `IOS_PROVISIONING_PROFILE` + `IOS_PROVISIONING_PROFILE_TYPE`）即可，脚本不另存副本。profile 到期前要在 Apple Developer 重新生成 App Store 描述文件并更新工程设置，否则 `xcodebuild archive` 会因 profile 过期失败。
- 自动签名与手动签名互斥：走自动签名时 `ConfigureIosSigning` / 脚本会清空残留的 profile id，避免 Xcode 工程同时「自动管理」和「写死 profile」。

## 6. 自动上传到 App Store Connect / TestFlight

`IOS_UPLOAD=1` 时，IPA 出来后脚本按顺序跑：

1. `xcrun altool --validate-app -f <ipa> -t ios <凭据>` —— 先校验，签名不对 / build 号重复 / 出口合规缺失都在这一步报出来；
2. `xcrun altool --upload-app -f <ipa> -t ios <凭据>` —— 上传到 App Store Connect，处理完在 TestFlight 里可见。

任一步失败都会让 job 失败（不会静默产出「构建成功但没传上去」）。**只上传，不自动提交审核**：上架信息（截图、隐私、分级、出口合规问卷）仍需人工在 App Store Connect 里走。

凭据按变量存在情况自动选，优先级：API Key → Apple ID + App 专用密码。

### 6.1 API Key（推荐）

在 App Store Connect → 用户和访问 → Integrations → App Store Connect API 建 Key（角色至少 App 管理 / 开发者中的上传权限），拿到 Key ID、Issuer ID 和一次性下载的 `.p8`：

```bash
IOS_UPLOAD=1 \
ASC_KEY_ID=XXXXXXXXXX \
ASC_ISSUER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
ASC_API_KEY_PATH=/path/to/AuthKey_XXXXXXXXXX.p8 \
IOS_EXPORT_OPTIONS_PLIST=/path/to/ExportOptions.plist \
scripts/build-app-ios.sh
```

CI 里把 `.p8` 存成 File 型变量（或 base64）写回 `ASC_API_KEY_PATH`；脚本负责放进 `~/.appstoreconnect/private_keys/` 并 `chmod 600`。

### 6.2 Apple ID + App 专用密码（回退）

`ASC_USERNAME` + `ASC_APP_PASSWORD`（[Apple ID 管理](https://appleid.apple.com) → 登录与安全 → App 专用密码）。不放 API Key 时才生效。

### 6.3 上传的前提

- **分发签名**：`ExportOptions.plist` 的 `method` 必须是 `app-store`（Xcode 15.3 之前）或 `app-store-connect`。脚本上传前会读 `method`，是 `development` / `ad-hoc` 直接报错退出——自动签名的开发描述文件能出 IPA 但传不上去，要走 `IOS_SIGNING=manual` + distribution profile，或让打包机上的账号能自动签发 Apple Distribution 证书。
- **build 号递增**：`CFBundleVersion` 必须比上一次上传的大，脚本默认用 `CI_PIPELINE_ID` 当 `BUILD_CODE`，已满足。
- **App Store Connect 里已有这个 App**：只有 App ID（`<ASC_APP_ID>`）不够，商店里要存在 bundle id 匹配的 App 记录。
- **出口合规**：CI 入口和本地 iOS 导出菜单在 Unity 构建成功后自动向 `Info.plist` 写入布尔值 `ITSAppUsesNonExemptEncryption=false`，声明应用不使用非豁免加密，避免 TestFlight 重复要求填写此项。若未来引入非豁免加密，需要同步更新声明及相应合规资料。
- 打包机需要装 Xcode 命令行工具：脚本用 `xcrun altool` 上传，`xcrun --find altool` 找不到就直接报错（新版 Xcode 若移除 altool，需改用 Transporter / `xcrun iTMSTransporter`）。

## 7. 已知限制

- iOS 导出只能在 macOS + 装了 iOS Build Support 的 Unity 上跑；Windows 编辑器里 `Axiom/Build iOS` 只给提示。
- 未设置 `IOS_EXPORT_OPTIONS_PLIST` 时，脚本通过 Python 3 在导出目录的 `build/ExportOptions.plist` 自动生成配置；显式指定路径时沿用该文件，路径不存在则报错。自动配置读取 `IOS_TEAM_ID`（缺省取工程 Team ID）、签名方式与描述文件，手动签名会生成 Bundle ID → Profile 的 `provisioningProfiles` 映射。`IOS_EXPORT_METHOD` 可覆盖导出方式；缺省上传或 distribution 使用 `app-store-connect`，其他使用 `development`。Ad Hoc 分发需显式指定导出方式。证书、私钥与描述文件仍需预先安装在 runner 上。
- 真机默认 Native（`Welcome.clientType = AutoPlatform`），iOS 包需要 `npm run build:native:ios` 生成的 `app-unity/Plugins/iOS/libapp.xcframework`；该目录不入库。
- 自动上传没有真实凭据可测，CI 未覆盖真实 altool 调用；脚本自测用假 `xcodebuild` / `xcrun` 覆盖了「先校验后上传、凭据优先级、缺凭据、开发签名被拦、上传失败即 job 失败」这几条闸门（`scripts/tests/test_ios_packaging.py`）。

CI 日志目录优先级：`APK_DL_DIR` → `LOG_DL_DIR` → iOS 产物目录（`IOS_DL_DIR` / `DL_DIR`）。Android 同样优先将日志写入 `APK_DL_DIR`，避免已有 `LOG_DL_DIR` 指向 npm 目录时日志散落。

Unity iOS 导出后递归检查 `Frameworks/` 下的 `libapp.xcframework`，读取其 Info.plist 校验非模拟器 arm64 slice 的非空库文件，并确认 PBX 工程引用。Shell 不再使用 `find -maxdepth 2` 判断，避免漏掉正常的 `Frameworks/app.unity/Plugins/iOS/` 路径。缺失通过 `BuildFailedException` 终止 CI、菜单和 Unity Build Settings 构建。

## iOS 应用图标

iOS 使用 `Assets/AppIcon/APP_Icon_iOS_1024.png`（1024×1024、不含 alpha、完整方形背景），原 512 图保留。PlayerSettings 的 iPhone/iPad/App Store 槽位引用该资源；CI 和本地导出菜单也会重新配置图标，禁用 alpha、纹理压缩及 mipmap。导出后检查 Any Appearance 1024 PNG，缺失则直接终止，避免上传时报 Apple 91111。

## 临时 CI：只导出、检查 IPA，禁止上传

> 历史开关已恢复：`ios` job 现在缺省 `IOS_UPLOAD=1`，即出 IPA 并上传到 App Store Connect / TestFlight（只上传，不提交审核）。若要回到「只出包、不上传」，把项目 CI/CD 变量 `IOS_UPLOAD` 设成 `0` 即可。

job 在 script 中固定 `IOS_EXPORT_IPA=1`、`IOS_VERIFY_PACKAGE=1`、`IOS_MIN_VERSION=15.0`，`IOS_UPLOAD` 取项目变量，未设置时按 `1` 走。上传前会先检查凭据（`ASC_KEY_ID` + `ASC_ISSUER_ID` + `ASC_API_KEY_PATH`，或 `ASC_USERNAME` + `ASC_APP_PASSWORD`），缺凭据在长构建之前就失败，不会白跑一轮 Unity。IPA 导出配置的 destination 必须是 export（自动配置已写入）；自定义配置若为 upload 且 `IOS_UPLOAD=0` 则直接失败。

导出后通过 `scripts/verify-ios-package.py` 检查包名/版本/构建号、最低系统版本、出口加密声明、arm64 主程序与 UnityFramework、Native 导出符号、配置 JSON、WASM 文件头和 Addressables catalog/bundle。任一失败使 job 失败，不上传。`libapp` 是静态库，最终包内由 Native 符号确认链接结果，不要求存在独立 framework。可对本地 `.ipa` 或 `.app` 单独运行校验；旧包如需单独检查结构，可显式传 `--min-ios 13.0`。结构检查不代替真机启动验收。

## iOS 排除 libaxiom

`libaxiom` 与业务库 `libapp` 都定义 `ffi_app_*`。旧包可能链接到通用宿主的空 `ffi_app_initialize`，跳过游戏模块注册。iOS NativePluginCheck 构建前排除所有 libaxiom 插件；导出后禁止残留文件与 PBX 引用。上游 AxiomCore 对 iOS Player 固定 Native 并裁掉 Wasm P/Invoke，Editor 与其他平台保留 Wasm；兼容类型在误用时抛出明确的不支持异常。上游修复已随 `axiom.core 1.0.59` 发布，本项目使用该版本，不能只关闭旧版本插件导入。

IPA 校验额外拒绝 `ffi_wasm_*` 导出及仅含 `ret` 的 `ffi_app_initialize`，从二进制层阻止错误链接通用宿主。

Unity 可能把 `Plugins/Android/libaxiom-*.so` 当普通文件复制进 iOS `Libraries`。这类 ELF 不等于 iOS 静态宿主。导出后仅清理明确位于 `Libraries/**/Plugins/Android/`、具有 ELF 文件头且未被 PBX 引用的 `.so` 副本；源 Android 文件不动。文件被工程引用或内容类型异常时仍阻断。清理后继续严格禁止任何残留 libaxiom 文件及工程引用，并打印具体路径。

CI archive 用独立的 DerivedData（缺省 `~/Library/Developer/Xcode/DerivedData/merge-ios-<bundle>-<version>-<buildCode>`，可用 `IOS_DERIVED_DATA_DIR` 覆盖），每次 archive 前清理，防止复用全局 Xcode 旧 `libGameAssembly.a`。**它不能放在 exFAT 的构建盘上**：`/Volumes/ExDiskB` 这类卷上 Xcode 的 `ProcessXCFramework` 复制 `libapp.a` 到 `Release-iphoneos` 会报 `couldn't be copied because you don't have permission to access "Release-iphoneos"`，`xcactivitylog` 也写不出来（`Operation not permitted`），见 job #20147 / #20159；出包后脚本会删掉该目录。本地重导出工程后若遇到已从 IL2CPP 源码删除的符号仍报 undefined，应先清理该 Xcode 工程的 Build 缓存再构建，不要重新引入已排除的 libaxiom。
