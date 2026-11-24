# ai-pipeline 工作约定

## 打包
- 触发词「打包」= 两件事都做：(1) 自动读取最新 tag 并 +1（如 v1.0.15 → v1.0.16），把 tag 推到 origin，触发 GitLab `pack` 流水线；(2) 顺手触发 `android` 任务（手动 job，`play`）出 APK。无需用户指定版本号。
- 不必等 main 的 MR 合并：可直接在当前/修复提交上打 tag。产物仅供内部/个人使用，不作为正式发布。
- 打完回报：
  - npm 包：`app.unity`、`app.welcome`（registry `http://<IP>/dl/npm`）
  - Android APK：`http://<IP>/dl/apk/<bundle>-<version>-<buildCode>.apk`（`com.example.game` / `0.1.0` / buildCode = pipeline id）
  - iOS：手动 job `ios`（macOS runner）缺省出 `dl/<bundle>-<version>-<buildCode>.ipa` 并上传到 App Store Connect / TestFlight（`IOS_UPLOAD` 缺省 1，设成 0 只出包不上传；不自动提交审核，凭据 `ASC_KEY_ID`/`ASC_ISSUER_ID`/`ASC_API_KEY_PATH` 优先，回退 `ASC_USERNAME`/`ASC_APP_PASSWORD`，缺凭据在长构建前就失败）。参数与签名见 `docs/ios-build.md`
- git 合并冲突时以远端为准（`-X theirs`）—— **仅限打包流程本身**。业务代码的提交 / MR 合并**绝不适用**：冲突必须人工拍板（见 `.github/agents/GIT.agent.md`），整文件取任一侧会静默删掉只在本分支存在的逻辑（编译不报错）。
- 参考：`.gitlab-ci.yml`（`pack` 仅 `v*` tag 触发；`android` / `ios` 手动、允许失败）。

## 分支与提交
- `main` 为集成主干。修改默认在当前任务分支提交；开始前确认当前分支及其用途，不自动切换到个人分支。用户明确指定分支时，以用户要求为准。

## MR 审查与合并（强制）
- 创建、审查、解决冲突、批准或合并 MR，必须执行 [MR 审查与合并规则](docs/mr-review-and-merge.md)，并在 MR 描述或评审中填写对应证据；模板见 `.gitlab/merge_request_templates/Default.md`。
- **冲突解决后必须双向核对**：既检查任务分支改动是否保留，也检查最新主干已有修复是否被覆盖。必须审查「合并结果相对目标主干」的差异，不能只看 MR 的普通 diff、冲突块或编译结果。检查范围包含双方同时修改但 Git 自动合并的文件。
- 主干已有修复被删除、退回旧行为或无法说明其去向时，**停止推送/批准/合并**；逐项给出原 MR/issue、旧行为保护、合并后实现位置与回归结果。确需改规或撤销修复时，必须先获明确业务确认，不能以“采用某一侧”代替语义确认。
- 审查绑定源分支 SHA 和目标主干 SHA；任一变化后旧批准不能直接沿用，必须复核新增差异及受影响的回归。`can_be_merged`、`skipped`、需求文档合并、分支测试通过均不等于主干验收通过。

## 过程数据不入库
- 定义：一次任务的验证留痕属于过程数据 —— 验收/核对报告、探针输出（`probe*.json`、`probe-*.txt`、运行日志）、知识库回写底稿（`update-plan.json` / `update-results.json`）、临时截图录屏、抓包与导出文件。
- 规则：一律不提交、不进 MR、不放进 `docs/`。写在本地 `reports/<YYYY-MM-DD>-<主题>/`（或直接放临时目录），该目录已由 `.gitignore` 忽略。
- 需要长期保留的只有「结论」：设计取舍、验收口径、踩坑结论写进 `docs/` 或知识库，过程与日志不要搬进仓库。
- 如果发现仓库里已经有过程数据（如历史上的 `reports/`），处理方式是删文件 + 在 `.gitignore` 补规则，不要再往里追加内容。
