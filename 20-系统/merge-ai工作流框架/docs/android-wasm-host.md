# Android wasm 宿主：wasmi 2.0 解释器（无 JIT、无编译、无预编译）

Android 包用 wasm 宿主运行逻辑层时的启动与运行期数据、根因与做法。过程日志不入库，这里只留结论。

## 结论（2026-10-28，当前）

`axiom` 的 wasm 运行时**统一换成 wasmi 2.0 纯解释器**：不生成也不执行原生机器码，不申请可执行内存，
不做 AOT 编译，也不需要任何编译缓存或预编译产物——启动时把标准 `.wasm` 解析/翻译成内部 IR 就开跑。

- 宿主侧编译/预编译这一段**彻底消失**：`main.wasm` 解析实测 48 ms（13.3 MB）/ **60 ms**（16.9 MB，当前产物）
  的纯翻译开销（macOS release），设备端 `ffi_wasm_create` 全程 **0.61 s**（含读 APK、注入配置、建实例）。
- 首页时间从 wasmtime 时代的 18–29 s 掉到 **6.4 s**（原生模式 4.0 s）。
- 逻辑每步耗时比 Cranelift 慢约 3 倍，但**仍在 16.7 ms 预算内**，棋盘拖拽合成时主线程保持 60 FPS。
- `libaxiom` 各平台都瘦了一圈（去掉 Cranelift 的机器码生成器）：

  | 平台 | wasmtime 版 | wasmi 版 |
  | --- | --- | --- |
  | Windows x86_64 `axiom.dll` | 22.9 MB | **9.12 MB** |
  | Android arm64-v8a `libaxiom.so` | 17.9 MB | **8.05 MB**（APK 内 strip 后 11.3 → 4.83 MB） |
  | Android armeabi-v7a `libaxiom.so` | 12.1 MB | **5.72 MB** |
  | Linux x86_64 `libaxiom.so` | 14.8 MB | **5.12 MB** |
  | macOS ARM64 `libaxiom.dylib` | 14.7 MB | **6.28 MB** |
  | iOS xcframework `libaxiom.a` | ~43.3 MB | ~43.3 MB（iOS 本来就是 wasmi，仅重建） |

设备：OnePlus PGKM10 / Android 15 / arm64-v8a / Unity 6000.0.62f1。

## 实测对比（同一台设备，同一份 `main.wasm`）

| 指标 | 原生 `libapp.so` | wasmtime Cranelift | wasmtime Pulley | **wasmi 2.0** |
| --- | --- | --- | --- | --- |
| wasm 就绪（`ffi_wasm_create`） | 无此段 | 17.5 s | 21.6 s | **0.61 s** |
| `app connected (Startup received)` | 0.66 s | ~1.4 s | — | **1.37 s** |
| `page loaded: Home` | 4.03 s | 22.1 s | 29.1 s | **6.4–6.9 s** |
| `page loaded: GameMerge` | 15.43 s | 37.1 s | 39.1 s | 36.3 s（冷）/ 约 +8 s（点开后） |
| 逻辑每步（首页） | 0（进程内） | 1.3–1.6 ms | ≈11.7 ms | **4.9–5.3 ms** |
| 逻辑每步（棋盘） | 0（进程内） | 3.5–3.9 ms | — | **10.2–10.6 ms** |
| 主线程帧率 | 60 | 60.2 | 60.2 | **60.2** |
| APK 内 `libaxiom.so` | n/a | 11.3 MB | 11.3 MB | **4.83 MB** |

说明：

- 「逻辑每步」是 wasm 宿主工作线程里的 `ffi_wasm_update` 墙钟时间（宿主每 5 s 打一行 `wasm loop avg=...`）。
  工作线程固定 1/60 推进，主线程不受其阻塞；只有每步 < 16.7 ms 时逻辑 TPS 才能跟满 60。
- GameMerge 页在冷启动下由首页按钮点开，prefab/图集首次加载约 8–12 s，这段是 Unity 侧（Addressables）
  的耗时，与 wasm 引擎无关，四种运行方式基本一致。
- Pulley 不是免编译的解释器，它只是 Cranelift 的可移植字节码目标，仍要完整编译且更慢，已随
  wasmtime 一起移除。

## wasmi 为什么不需要编译

wasmi 只有「解析 + 翻译成内部 IR」一步，没有机器码生成、没有可执行内存。对 13 MB、几乎全是代码段的
模块，这一步是线性的字节扫描，毫秒级；wasmtime 走 Cranelift 则是为每个函数生成并优化机器码，秒级。
所以「编译缓存」「预编译 `.cwasm`」「Pulley」这些为编译耗时做的优化都没有存在意义了。

wasmi 2.0 不支持的部分我们也没用到：`main.wasm` 不含 SIMD/GC/多内存等特性，实际只导入两个
wasm-bindgen 函数（`__wbg_now_*`、`__wbindgen_init_externref_table`），宿主的 linker 已经实现。
若以后 wasm 侧启用 SIMD，需要在 `Cargo.toml` 的 wasmi 依赖上打开 `simd` feature，否则会在解析期报错。

## 改动（`axiom-rs`）

- `Cargo.toml`：删除 `wasmtime` 依赖（含 `cranelift` / `pulley` 配置）；`wasmi = "2.0"` 从 iOS 专属依赖
  提升为全平台通用依赖（`default-features = false, features = ["std", "validate", "auto-dispatch"]`）；
  `wasm` feature 去掉 `dep:wasmtime`。
- `src/wasm/runtime/mod.rs`：不再按平台 `#[path]` 分支，统一走 `wasmi.rs`；`Engine` / `Module` 只在
  `httpd` feature 下再导出。
- 删除 `src/wasm/runtime/wasmtime.rs`（含磁盘编译缓存、`AXIOM_WASM_ENGINE` 引擎切换）。
- `src/httpd/session.rs`：`Module::from_binary` → 运行时抽象的 `compile()`，引擎取 `crate::wasm::engine`。
- `scripts/build-unity.sh` 当年那套原生插件构建脚本已随 wasm 化移除；现在只在需要 Native 时用
  `scripts/build-native-plugins.sh`（`npm run build:native[:平台]`）本地生成。

宿主侧其它改动（`shared/log.rs` 的 logcat 回退、`AXIOM_LOG_FRAME` / `AXIOM_LOG_VERBOSE`）保留，
见下节。

## 日志可见性（顺带修的坑）

Android 上 native 的 `println!` 不会进 logcat，而 `axiom` 的 `log!` 在日志文件未初始化时就回退到
`println!`——之前**完全看不到 native 侧日志**。现在回退改为写 logcat（tag `Axiom`）：

```bash
adb logcat -s Axiom:V Unity:V | grep -E "Axiom|BootProbe"
```

## 坑

1. **不能开 `[profile.release] strip = true`**。它会剥掉 rustc 写进 wasm 的 `target_features` 自定义段，
   wasm-bindgen 于是改走 externref 变换，产物 import `__wbindgen_externref_xform__::*`，宿主 linker 没有
   这些导入，表现为首页永远出不来（黑屏）：

   ```
   ffi_wasm_create: instantiate failed: unknown import:
   `__wbindgen_externref_xform__::__wbindgen_externref_table_grow` has not been defined
   ```

   判断方法：`wasm-opt --print app_bg.wasm | grep '^ (import'`，正常应只有 `./app_bg.js` 的
   `__wbindgen_init_externref_table`。

2. **`[profile.release]` 不能写进 `main/Cargo.toml`**：它是全局的，会把原生插件一起降成 `opt-level="z"`。
   wasm 的体积优先只在 `scripts/build-wasm.mjs` 里用环境变量施加（`CARGO_PROFILE_RELEASE_OPT_LEVEL=z` 等）。

3. **wasm 宿主必须在工作线程跑**（`axiom-unity` 的 `AxiomWasmServer`）：主线程只做输入/输出队列收发，
   逻辑固定 1/60 推进。单线程版本会让主线程被 `ffi_wasm_update` 拖慢，且逻辑速度随渲染帧率漂移。

## 开关与排障

| 环境变量 | 作用 |
| --- | --- |
| `AXIOM_LOG_FRAME=true` | 恢复 `ffi_wasm_update` 每帧日志 |
| `AXIOM_LOG_VERBOSE=true` | 打印每条 inbound/outbound/落盘日志 |

宿主每 5 s 打一行工作线程统计，可直接读效率：

```
[Axiom] wasm loop avg=10.54ms/step in=15 out=26 pending in=0 out=0 t=60.0s
```

`pending in/out` 是输入/输出队列积压，连续拖拽合成时峰值 ≤ 7 条即正常。

## 现状：wasm 宿主是默认运行方式

- `app-welcome` 的 `Welcome.clientType` 默认 `AxiomClientType.Wasm`，`app/Assets/Scenes/Welcome.unity`
  里也是 `clientType: 3`（`Wasm`，`wasmAppId: main`、`wasmVersion: 0.1.0`）。逻辑层以 `main.wasm`
  形式加载，宿主用 wasmi 解释执行，跑在独立工作线程。
- 切回原生：把 `clientType` 改成 `2`（`AxiomClientType.Native`），先本地生成逻辑层原生插件
  `npm run build:native:android`（`scripts/build-native-plugins.sh`，产物落在 `app-unity/Plugins/<平台>`），
  再打包；首屏数据见文末「原生模式实测」。`Auto`（0）**不是**原生，它会落到 WebSocket 传输（debug 用）。
- **`app-unity/Plugins/**` 不入库**：wasm 是默认运行方式，原生库只在需要 Native 时本地生成，
  `.gitignore` 已忽略该目录，`app.unity` 包里也不再带原生库（CI 的 pack job 会显式清掉残留）。
- **配置路径两套并存**：原生读 `StreamingAssets/config.json`（`ffi_app_create` → `dataDir/{版本}.json` →
  `assetsDir/config.json`），wasm 宿主读 `StreamingAssets/main/config.json`（`assetsDir/{appid}/config.json`）。
  所以根目录 `config.json` 保留，`axiom.csv.json` 的 `outDirs` 同时写两处。
- `StreamingAssets` 里的 `main.wasm`(16.9 MB) + `main/config.json`(3.9 MB) 只在 wasm 模式下用得到；
  wasm 是默认，所以两者都要随包发布（改 `package.json` 的 `outDirs` 与 `scripts/build-wasm.mjs` 时注意）。

### 切回原生时踩到的两个坑（已修）

1. **`AxiomNativeServer` 必须绑 `libapp`，不能绑 `libaxiom`**。
   `axiom-unity` 的 wasm 提交（38620e6）把 `AxiomNativeServer` 的 `[DllImport("app")]` 全部改成了
   `[DllImport("axiom")]`，但 `axiom.core` 自带的 `libaxiom` 只服务 wasm 宿主：它导出的
   `ffi_app_*` 是**没有注册任何业务模块**的通用实现。表现是启动很快、`app connected (Startup received)`
   正常打印，但之后一条 Spawn/Event/Data 都没有，首页只剩静态背景。
   已在 `axiom.core` **1.0.46** 修回（`AxiomNativeServer` → `DllImport("app")`，`AxiomWasmServer` 仍是 `"axiom"`）。
   排查手法：`/sdcard/Android/data/<pkg>/files/axiom.log` + `trace/*.njson`（正常一次启动有几百条消息，
   坏的时候只有 1 条 `Startup`）。

2. **Native 模式下 `app-unity/Plugins/**` 的 `.meta` 必须是 `PluginImporter`**。
   二进制不入库（`npm run build:native:<平台>` 本地生成），但 Unity 若把它当普通文件导入，
   打包时会**静默丢掉** `libapp.so`（APK 里没有 `lib/arm64-v8a/libapp.so`，装机后首页空白）。
   现在这件事由 `app-unity/Editor/AppUiEditor/NativePluginCheck.cs` 兜住，不用手工改：
   - 编辑器加载时（`InitializeOnLoadMethod`）就把已存在的库钉成「只勾目标平台 + 对应 CPU」
     （`Any Platform` 必须先关，否则单平台开关会被忽略、保存回去还是 Any Platform）；
   - 打包前（`IPreprocessBuildWithReport`）再校验一次，导入类型不是 `PluginImporter` 直接
     `BuildFailedException`，不再静默跳过；
   - `scripts/build-app-android.sh` 打完 APK 后按 APK 里出现的 ABI 断言
     `lib/<abi>/libapp.so` 存在，缺了就让 job 失败（`REQUIRE_LIBAPP=0` 可跳过）。
   曾经有连续几个 tag/手动产物的 APK 里没有这个库而构建全绿（28106001/28107001/28108001），
   就是导入设置这一层出的问题。

### 原生模式实测（同一台设备）

| 阶段 | 时间 |
| --- | --- |
| `AxiomClient.Reconnect returned` | 0.52 s |
| `app connected (Startup received)` | 0.66 s |
| `page loaded: Home` | 4.03 s |
| `page loaded: GameMerge`（棋盘首次加载） | 15.43 s |

## 历史：wasmtime 的编译缓存路线（已废弃）

早期（wasmtime + Cranelift）慢的根因定位：启动时间全花在 `ffi_wasm_create` 的 JIT 编译上，
Unity / Addressables / 热更资源都无关。当时的缓解手段是给 `compile()` 加磁盘缓存
（`Module::serialize` 写 `<data_dir>/wasm-cache/<hash>.cwasm`，下次 `deserialize`），并给 wasm 做体积
优化（16.9 MB → 13.3 MB），把冷启动 18.95 s 压到 14.87 s、热启动 0.69 s。

换成 wasmi 之后这些都不需要了：首次启动直接 0.61 s，比当时的「热启动命中缓存」还快，
而且少了 31.9 MB 的缓存产物与失效回退逻辑。`AXIOM_WASM_CACHE` / `AXIOM_WASM_CACHE_DIR` /
`AXIOM_WASM_ENGINE` 三个环境变量随之删除。

定位方法留档（换成别的运行时仍适用）：在启动链路打点（`[BootProbe]`，时间轴统一用
`Time.realtimeSinceStartup`）——`Addressables.Initialize` → `AxiomClient.Reconnect` →
`wasm app connected (Startup received)` → `page loaded: Home`，再用 `adb logcat` 毫秒时间戳与 native
日志对齐，确认哪一段是阻塞调用。
