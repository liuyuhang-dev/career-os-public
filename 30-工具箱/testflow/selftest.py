#!/usr/bin/env python3
"""testflow 自测与回归(跨平台)。

一条命令跑完:语法检查 -> 工具自测 -> 干净用例应通过 -> 违规用例应被拦住
-> 扫描器行为 -> 文档-实现对账 -> 端到端链路 + 判定不变量。

**全绿时会顺手同步 HANDOFF.md 里的「自测 N 项」数字**(实测值覆盖旧数字):
这类数字每次加测试都要手改,手改会错 —— 与其信手,不如让实测结果自己写进去。
红了不同步(红的数字本身就是信号)。同步后记得把 HANDOFF.md 一起提交。

**为什么用 Python 而不是 bash**:Windows 上不一定有 bash,而 PowerShell 的
`>` 重定向会做文本转换(会把二进制写坏)。这个脚本在 macOS / Linux / Windows
上行为一致。

退出码 0 = 全绿;非 0 = 有项目断了(会打印是哪一项)。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
PY = sys.executable
EX = SKILL_DIR / "examples"
DEMO = EX / "artifacts" / "game-demo"

sys.path.insert(0, str(SKILL_DIR / "scripts"))


class Runner:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def ok(self, name: str) -> None:
        print(f"  ok   {name}")
        self.passed += 1

    def bad(self, name: str, detail: str = "") -> None:
        print(f"  FAIL {name}")
        if detail:
            for line in detail.strip().splitlines()[-12:]:
                print(f"       {line}")
        self.failed.append(name)

    def cmd(self, name: str, want: int, *argv) -> subprocess.CompletedProcess:
        r = subprocess.run([str(a) for a in argv], capture_output=True,
                           text=True, cwd=str(SKILL_DIR))
        if r.returncode == want:
            self.ok(name)
        else:
            self.bad(name, f"期望退出码 {want},实际 {r.returncode}\n"
                           f"{r.stdout}\n{r.stderr}")
        return r

    def truth(self, name: str, cond: bool, detail: str = "") -> None:
        if cond:
            self.ok(name)
        else:
            self.bad(name, detail)


def sync_doc_counts(total: int, per_script: dict, docs_count: int | None) -> list[str]:
    """把文档里「自测 N 项 / N 项全绿 / N 项自检」的数字同步成本次实测值。

    为什么值得做:这类数字每次加测试都要手改,而手改会错 —— 我自己就写错过一次
    (把 78 写成 77)。文档里的数字一旦与实测不符,整份交接文档的可信度就掉了。
    **只在全绿时调用**:红的数字本身就是信号,不该被覆盖。
    """
    doc = Path(__file__).resolve().parent / "HANDOFF.md"
    # 读写都用 newline="":原样保留文件自己的行尾。默认的 universal newline 会把
    # CRLF 读成 LF、再用 newline="\n" 写回 LF —— 于是"跑一次全绿的 selftest"
    # 就悄悄把这份文档改成 LF,而这棵树的约定是 CRLF,且 git(autocrlf) 不显示
    # 这类改动,肉眼也看不出来。(踩过:改完一轮行尾核对才发现。)
    with doc.open(encoding="utf-8", newline="") as fh:
        before = fh.read()
    text = before
    changes: list[str] = []

    def sub(pattern: str, label: str, value: int) -> None:
        """把匹配到的数字换成 value。

        只替换**捕获组 1 与 2 之间的那个数字**,用分组拼新串 ——
        早先版本直接把替换模板当字符串塞进去,结果 `.replace()` 不解析 \\g<n>,
        把整段前缀连同行内容一起换成了占位符,文档被我写坏过一次。
        """
        nonlocal text
        m = re.search(pattern, text)
        if not m:
            changes.append(f"(没找到:{label},跳过)")
            return
        new_text = m.group(1) + str(value) + m.group(2)
        if m.group(0) != new_text:
            text = text[:m.start()] + new_text + text[m.end():]
            changes.append(f"{label}: {m.group(0).strip()[-20:]} -> "
                           f"{new_text.strip()[-20:]}")

    for script, count in sorted(per_script.items()):
        sub(rf"(\| `{re.escape(script)}` \|[^\n]*?\| 自测 )\d+( 项)",
            f"{script} 自测项数", count)
    if docs_count is not None:
        sub(r"(通过\()\d+( 项自检)", "check_docs 检查项数", docs_count)
    sub(r"(\| `selftest.sh` \|[^\n]*?\| )\d+( 项全绿)", "selftest 总项数", total)

    if text != before:
        with doc.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    return changes


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    if not (SKILL_DIR / "SKILL.md").exists():
        print(f"错误: 找不到 {SKILL_DIR / 'SKILL.md'}", file=sys.stderr)
        return 2

    r = Runner()
    print(f"skill 目录: {SKILL_DIR}")
    print(f"Python: {PY}")
    print(f"平台: {sys.platform}")

    # ---------------------------------------------------------- 1 语法
    section("1. 语法与 JSON 有效性")
    for f in sorted((SKILL_DIR / "scripts").glob("*.py")):
        r.cmd(f"py_compile {f.name}", 0, PY, "-m", "py_compile", f)
    # 清理 py_compile 产物
    for pc in SKILL_DIR.rglob("__pycache__"):
        shutil.rmtree(pc, ignore_errors=True)

    for f in sorted(SKILL_DIR.rglob("*.json")):
        if "__pycache__" in f.parts or "04-runs" in f.parts:
            continue
        r.cmd(f"json {f.name}", 0, PY, "-c",
              "import json,sys;json.load(open(sys.argv[1],encoding='utf-8'))", f)

    # ---------------------------------------------------------- 2 工具自测
    section("2. 工具自测")
    per_script: dict[str, int] = {}

    def cmd_count(script: str, name: str, want: int, *argv):
        """跑自测并记下它报了几项 ok —— 文档里的「自测 N 项」由这里同步。"""
        res = r.cmd(name, want, *argv)
        per_script[script] = sum(1 for ln in res.stdout.splitlines()
                                 if ln.startswith("  ok"))
        return res

    cmd_count("coordmap.py", "coordmap 自测(机型/坐标/PNG 格式全覆盖)", 0,
              PY, SKILL_DIR / "scripts" / "coordmap.py", "--selftest")
    cmd_count("judge.py", "judge 自测(严格 yes/no / 证据缺失 / 端点不可达 / explore 换算)",
              0, PY, SKILL_DIR / "scripts" / "judge.py", "--selftest")
    cmd_count("scan_anomalies.py", "scan_anomalies 自测(崩溃/白屏黑屏/卡死/证据缺口)",
              0, PY, SKILL_DIR / "scripts" / "scan_anomalies.py", "--selftest")
    cmd_count("check_config.py", "check_config 自测(悬空引用/主键/类型/范围/枚举/模板生成)",
              0, PY, SKILL_DIR / "scripts" / "check_config.py", "--selftest")
    cmd_count("pipeline.py", "pipeline 自测(闸门优先/无法判断不被读成通过/退出码语义)",
              0, PY, SKILL_DIR / "scripts" / "pipeline.py", "--selftest")
    cmd_count("compare_screens.py", "compare_screens 自测(配对/变化区域/尺寸变化/抗噪)",
              0, PY, SKILL_DIR / "scripts" / "compare_screens.py", "--selftest")
    cmd_count("scaffold_cases.py", "scaffold_cases 自测(期望覆盖无洞/不覆盖已有/draft 拦住)",
              0, PY, SKILL_DIR / "scripts" / "scaffold_cases.py", "--selftest")
    cmd_count("check_secrets.py", "check_secrets 自测(凭据模式/打码不泄露/敏感文件是否入库)",
              0, PY, SKILL_DIR / "scripts" / "check_secrets.py", "--selftest")
    cmd_count("regression_set.py",
              "regression_set 自测(稳定性/缺口/选择器/无运行不报干净)", 0,
              PY, SKILL_DIR / "scripts" / "regression_set.py", "--selftest")
    cmd_count("concerns.py",
              "concerns 自测(口头覆盖拦截/缺口出口/来源校验/结论页结构)", 0,
              PY, SKILL_DIR / "scripts" / "concerns.py", "--selftest")
    cmd_count("backbone_map.py",
              "backbone_map 自测(叶子标注/未知路径/空主干/未安置工具)", 0,
              PY, SKILL_DIR / "scripts" / "backbone_map.py", "--selftest")
    # 吃自己的狗粮:随附的主干导图必须与能力对得上(每条叶子都有可执行的验证路径)
    r.cmd("backbone_map: 随附导图无缺口", 0,
          PY, SKILL_DIR / "scripts" / "backbone_map.py", "--quiet")
    # 样例关注点登记册:格式必须自洽(有缺口(row 级)但不该有格式问题)
    concerns_json = Path(tempfile.mkdtemp()) / "concerns.json"
    r.cmd("concerns: 样例登记册(有未覆盖,退出码 1)", 1, PY,
          SKILL_DIR / "scripts" / "concerns.py", "--register",
          EX / "concerns-sample.csv", "--json", concerns_json, "--quiet")
    cj = json.loads(concerns_json.read_text(encoding="utf-8"))
    errs = [f for f in cj["findings"] if f.get("level") == "error"]
    r.truth("样例登记册无错误级问题(无口头覆盖/无缺出口/依据成立)",
            errs == [], str(errs))
    r.truth("样例登记册能被扫出盲区类别(账号/合规/性能未登记)",
            any(f["kind"] == "blind_spot" for f in cj["findings"]),
            str([f["kind"] for f in cj["findings"]]))
    r.truth("样例登记册含产品关心的那条(负金币)",
            any("金币" in row["关注点"] and row["来源"] == "产品"
                for row in cj["rows"]), str([r_["关注点"] for r_ in cj["rows"]][:3]))
    # 吃自己的狗粮:skill 目录自身不该有凭据命中(夹具一律运行时拼装)
    r.cmd("check_secrets: skill 目录自身干净", 0,
          PY, SKILL_DIR / "scripts" / "check_secrets.py", "--path", SKILL_DIR, "--quiet")

    # ---------------------------------------------------------- 3 闸门
    section("3. 三道闸门")
    r.cmd("干净用例应通过", 0, PY, SKILL_DIR / "scripts" / "validate_cases.py",
          "--requirements", DEMO / "01-requirements.json",
          "--specs", EX / "specs", "--cases", DEMO / "03-cases")
    r.cmd("违规用例应被拦住", 1, PY, SKILL_DIR / "scripts" / "validate_cases.py",
          "--specs", EX / "violations" / "specs",
          "--cases", EX / "violations" / "03-cases")
    # 断言具体错误码真的被触发 —— 只看退出码的话,规则被删掉也照样"通过"
    v = r.cmd("违规用例应被拦住(复核错误码)", 1, PY,
              SKILL_DIR / "scripts" / "validate_cases.py",
              "--specs", EX / "violations" / "specs",
              "--cases", EX / "violations" / "03-cases")
    for code in ("ILLEGAL_EXPECT_SOURCE", "MISSING_EXPECTATION_COVERAGE",
                 "MISSING_REQ_REFS", "VISION_NO_REGION", "ORPHAN_CHECK",
                 "PSEUDO_DUPLICATE_COVERAGE", "FAULT_WITHOUT_STATE_REPORT",
                 "CHECK_AFTER_UNKNOWN", "VISION_NO_AFTER",
                 "SPEC_VAGUE_EXPECTATION", "SPEC_SUBJECTIVE_EXPECTATION"):
        r.truth(f"违规回归覆盖错误码 {code}", code in v.stdout,
                f"{code} 未出现在校验输出里")
    r.cmd("严格模式也应拦住", 1, PY, SKILL_DIR / "scripts" / "validate_cases.py",
          "--specs", EX / "violations" / "specs",
          "--cases", EX / "violations" / "03-cases", "--strict-warnings")

    # ---------------------------------------------------------- 4 扫描器
    section("4. 幂等防护扫描器")
    import scan_idempotency as SI  # noqa: E402

    tmp = Path(tempfile.mkdtemp())
    try:
        r.cmd("扫描 fixture 应成功", 0, PY, SKILL_DIR / "scripts" / "scan_idempotency.py",
              "--src", EX / "fixtures", "--out", tmp / "scan.md",
              "--json", tmp / "scan.json")
        data = json.loads((tmp / "scan.json").read_text(encoding="utf-8"))
        names = sorted(e["name"] for e in data["entries_with_missing"])
        r.truth(
            "扫描结果精确性(3 个缺防护 / 对照组未误报 / 无副作用被过滤)",
            len(data["entries"]) == 4
            and names == ["BuyItem", "ClaimMailReward", "OnPayCallback"],
            f"实际: entries={len(data['entries'])} missing={names}",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------------------------------------------------------- 5 复核单
    section("5. 复核单")
    tmp = Path(tempfile.mkdtemp())
    try:
        r.cmd("复核单生成", 0, PY, SKILL_DIR / "scripts" / "review_sheet.py",
              "--requirements", DEMO / "01-requirements.json",
              "--specs", EX / "specs", "--cases", DEMO / "03-cases",
              "--out", tmp / "sheet.md")
        r.cmd("驳回不给原因应被拒绝", 2, PY, SKILL_DIR / "scripts" / "review_sheet.py",
              "--cases", DEMO / "03-cases", "--reject", "INV-BAG-C01", "--by", "selftest")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------------------------------------------------------- 6 端到端
    section("6. 端到端链路")
    run_dir = DEMO / "04-runs"
    for p in run_dir.glob("run-*.json"):
        p.unlink()
    for p in (DEMO / "05-bugs").glob("*.md"):
        p.unlink()
    (DEMO / "report.md").unlink(missing_ok=True)

    r.cmd("执行(plan-only)", 0, PY, SKILL_DIR / "scripts" / "run_case.py",
          "--case", DEMO / "03-cases" / "REQ-SETTLE-C01.json",
          "--case", DEMO / "03-cases" / "REQ-SETTLE-C02.json",
          "--case", DEMO / "03-cases" / "REQ-NET-C01.json",
          "--case", DEMO / "03-cases" / "INV-COIN-C01.json",
          "--case", DEMO / "03-cases" / "INV-BAG-C01.json",
          "--case", DEMO / "03-cases" / "INV-IDEM-C01.json",
          "--case", DEMO / "03-cases" / "EXP-COIN-C01.json",
          "--out", run_dir,
          "--state", run_dir / "evidence" / "game-demo_state.json")

    runs = sorted((p for p in run_dir.glob("run-*.json") if "judge" not in p.name),
                  key=lambda p: p.stat().st_mtime)
    if not runs:
        r.bad("run 结果产出", "没有产生 run-*.json")
    else:
        run_json = runs[-1]
        r.cmd("提 bug", 0, PY, SKILL_DIR / "scripts" / "file_bugs.py",
              "--run", run_json, "--out", DEMO / "05-bugs",
              "--specs", EX / "specs", "--cases", DEMO / "03-cases",
              "--requirements", DEMO / "01-requirements.json")

        # 关键不变量:每条 fail 必须绑定证据;explore 不得产出 pass/fail
        payload = json.loads(run_json.read_text(encoding="utf-8"))
        problems: list[str] = []
        for case in payload.get("cases") or []:
            for chk in case.get("checks") or []:
                v = chk.get("verdict")
                if case.get("exploreMode") and v in ("pass", "fail"):
                    problems.append(f"{case['id']}/{chk['source']}: explore 产出了 {v}")
                if v == "fail" and not (chk.get("evidence") or []):
                    problems.append(f"{case['id']}/{chk['source']}: fail 但未绑定证据")
        r.truth("判定不变量(证据绑定 / explore 不产出 pass-fail)",
                not problems, "; ".join(problems))

        # 入口这一步自动化不了,但必须能标出"从哪一步开始不可信"
        reqs_demo = payload.get("judgeRequests") or []
        r.truth("open_entry 生成「宿主首页是否可见」判定请求(入口不可信要能标出来)",
                any("已处于" in (q.get("question") or "")
                    and "首页" in (q.get("question") or "") for q in reqs_demo),
                str([q.get("question") for q in reqs_demo][:3]))

        # bug 单里不应出现"无证据"分支
        no_ev = [p.name for p in (DEMO / "05-bugs").glob("*.md")
                 if "无证据** —— " in p.read_text(encoding="utf-8")]
        r.truth("bug 单证据齐全(无『无证据』分支)", not no_ev, str(no_ev))

        # 报告要把"证据缺口"讲清楚:缺口不是"没有异常",是没东西可判
        rep_text = (DEMO / "report.md").read_text(encoding="utf-8")
        r.truth("报告含『证据缺口』一节并列出具体的缺采集动作",
                "## 证据缺口" in rep_text
                and any("no_action" in ln for ln in rep_text.splitlines()
                        if "logcat" in ln),
                rep_text[-300:])

        # 证据打包:单子发给研发时证据不能断链
        tmp_b = Path(tempfile.mkdtemp())
        try:
            r.cmd("提 bug(带证据打包)", 0, PY, SKILL_DIR / "scripts" / "file_bugs.py",
                  "--run", run_json, "--out", tmp_b / "bugs", "--bundle",
                  "--specs", EX / "specs", "--cases", DEMO / "03-cases",
                  "--requirements", DEMO / "01-requirements.json")
            evdir = tmp_b / "bugs" / "BUG-001.evidence"
            man = json.loads((evdir / "manifest.json").read_text(encoding="utf-8"))
            r.truth("证据拷进 BUG-00N.evidence 并写清单", evdir.exists()
               and man["files"] and man["totalBytes"] > 0, str(man))
            first = evdir / man["files"][0]["file"]
            r.truth("清单哈希与文件一致", first.exists() and hashlib.sha256(
                first.read_bytes()).hexdigest() == man["files"][0]["sha256"],
               str(man["files"][0]))
            bug1 = (tmp_b / "bugs" / "BUG-001.md").read_text(encoding="utf-8")
            r.truth("bug 单引用打包后的相对路径(不是 04-runs 的绝对路径)",
               "BUG-001.evidence" in bug1 and "04-runs" not in bug1.split("## 证据")[1],
               bug1.split("## 证据")[1][:160])
            r.truth("打包时不静默丢文件", man["missing"] == [], str(man["missing"]))
            r.cmd("提 bug(不带打包)", 0, PY, SKILL_DIR / "scripts" / "file_bugs.py",
                  "--run", run_json, "--out", tmp_b / "bugs_plain",
                  "--specs", EX / "specs", "--cases", DEMO / "03-cases")
            r.truth("默认不打包(不改变原有行为)",
               not list((tmp_b / "bugs_plain").glob("*.evidence")))
        finally:
            shutil.rmtree(tmp_b, ignore_errors=True)

        # 报告要串起异常扫描报告(在临时目录里验证指针,不依赖 demo 目录状态)
        tmp_r = Path(tempfile.mkdtemp())
        try:
            (tmp_r / "anomalies.md").write_text("# 异常扫描(桩)\n", encoding="utf-8")
            r.cmd("提 bug(同目录存在异常报告时)", 0, PY,
                  SKILL_DIR / "scripts" / "file_bugs.py",
                  "--run", run_json, "--out", tmp_r / "05-bugs",
                  "--specs", EX / "specs", "--cases", DEMO / "03-cases")
            rep2 = (tmp_r / "report.md").read_text(encoding="utf-8")
            r.truth("报告指向异常扫描报告(候选不当判定)",
                    "anomalies.md" in rep2 and "候选" in rep2, rep2[-200:])
        finally:
            shutil.rmtree(tmp_r, ignore_errors=True)

        # after 指向前置条件时,前置的截图也要能绑上证据(观测顺序 = 前置 + 步骤)
        tmp_pre = Path(tempfile.mkdtemp())
        try:
            case = {
                "id": "INV-PREBIND-C01",
                "spec": "specs/inv-coin.md",
                "title": "前置截图绑定",
                "type": "device-android",
                "priority": "P2",
                "expect_source": "invariant",
                "req_refs": [],
                "judge": "vision-closed",
                "review": {"state": "approved", "by": "selftest"},
                "device": {"profile": "android-mid", "host": "wechat"},
                "preconditions": [
                    {"id": "PRE-1", "action": "launch_host"},
                    {"id": "PRE-2", "action": "screenshot", "name": "home"},
                ],
                "steps": [
                    {"id": "S1", "action": "wait", "ms": 100},
                    {"id": "S2", "action": "adb_shell",
                     "cmd": "dumpsys package com.tencent.mm | grep versionName",
                     "save_as": "pkg-version"},
                ],
                "checks": [{"source": "E1", "after": "PRE-2", "kind": "assert",
                            "mode": "vision-closed",
                            "question": "首页是否显示包含「开始游戏」字样的按钮?",
                            "region": [0.0, 0.7, 1.0, 1.0], "answer": "yes"}],
                "evidence": ["screenshot"],
            }
            cf = tmp_pre / "INV-PREBIND-C01.json"
            cf.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
            r.cmd("执行: after 指向前置的用例", 0, PY,
                  SKILL_DIR / "scripts" / "run_case.py", "--case", cf,
                  "--out", tmp_pre / "04-runs")
            runs2 = sorted(p for p in (tmp_pre / "04-runs").glob("run-*.json")
                           if "judge" not in p.name)
            payload2 = json.loads(runs2[-1].read_text(encoding="utf-8"))
            chk2 = payload2["cases"][0]["checks"][0]
            r.truth("after 指向前置条件时证据能绑上",
                    bool(chk2.get("evidence")) and "PRE-2" in chk2["evidence"][0],
                    str(chk2.get("evidence")))
            ev_idx = payload2["cases"][0].get("evidenceIndex") or []
            r.truth("adb_shell + save_as:输出登记成证据且命令带重定向",
                    any("pkg-version" in (e.get("path") or "") for e in ev_idx)
                    and any("dumpsys package" in c and ">" in c
                            for c in payload2["cases"][0]["plan"]["steps"]),
                    str(ev_idx))

        finally:
            shutil.rmtree(tmp_pre, ignore_errors=True)

        # ------------------------------------------------------ 6b 判定轮
        section("6b. 判定轮(独立调用)")
        bug_count_before = len(list((DEMO / "05-bugs").glob("BUG-*.md")))
        tmp_j = Path(tempfile.mkdtemp())
        try:
            copy_run = tmp_j / run_json.name
            shutil.copy(run_json, copy_run)
            # 预检:截图没落盘时必须提前报出来(退出码 1),而不是等判定完才发现
            r.cmd("判定轮预检:缺证据应报 1", 1, PY, SKILL_DIR / "scripts" / "judge.py",
                  "--run", copy_run, "--preflight")
            # plan-only 的 run:截图只有路径、文件没落盘。
            # 判定轮必须把全部视觉判定项记为 blocked,并且**退出码 1**
            # (一条结论都没产出来,这种事要被 CI 看见)。
            r.cmd("判定轮:截图未落盘 -> 全 blocked(退出码 1)", 1, PY,
                  SKILL_DIR / "scripts" / "judge.py", "--run", copy_run, "--quiet")
            judged = json.loads(copy_run.read_text(encoding="utf-8"))
            checks = [c for case in judged.get("cases") or []
                      for c in case.get("checks") or []]
            vision = [c for c in checks if c.get("mode") == "vision-closed"]
            never_pass = [f"{c.get('source')}={c.get('verdict')}" for c in vision
                          if c.get("verdict") != "blocked"]
            r.truth("无截图的视觉判定项一律 blocked(绝不伪装成 pass)",
                    not never_pass, "; ".join(never_pass))
            r.truth("判定轮不再留下 pending_judgment",
                    not [c for c in checks if c.get("verdict") == "pending_judgment"],
                    f"仍有 {sum(1 for c in checks if c.get('verdict') == 'pending_judgment')} 项")
            r.truth("run json 写入判定摘要", "judgment" in judged,
                    str(judged.get("judgment")))
            jlog = copy_run.with_suffix("").with_suffix(".judgment.jsonl")
            r.truth("判定日志写出并含原因",
                    jlog.exists() and "blocked" in jlog.read_text(encoding="utf-8"),
                    str(jlog))
            # 判定完再预检:已经没有待判项了,也要明确说出来(退出码 1)
            r.cmd("判定轮预检:无待判项应报 1", 1, PY, SKILL_DIR / "scripts" / "judge.py",
                  "--run", copy_run, "--preflight")

            # blocked 绝不开 bug:判定后重跑提 bug,单子数量不应变多
            r.cmd("判定后提 bug 仍成功", 0, PY, SKILL_DIR / "scripts" / "file_bugs.py",
                  "--run", copy_run, "--out", tmp_j / "bugs",
                  "--specs", EX / "specs", "--cases", DEMO / "03-cases",
                  "--requirements", DEMO / "01-requirements.json")
            after = len(list((tmp_j / "bugs").glob("BUG-*.md")))
            r.truth("blocked 不开 bug(单子数量与判定前一致)",
                    after == bug_count_before, f"判定前 {bug_count_before},判定后 {after}")

            # 人工复核(6-triage「复核失败的判定」):改成 blocked 能撤回一张单子
            r.cmd("人工复核: 把一条 fail 改成 blocked", 0, PY,
                  SKILL_DIR / "scripts" / "judge.py", "--run", copy_run,
                  "--override", "REQ-SETTLE-C01/E1", "--verdict", "blocked",
                  "--by", "selftest", "--reason", "判定证据不足(截图看不出结算页)", "--quiet")
            r.cmd("人工复核后重跑提 bug", 0, PY, SKILL_DIR / "scripts" / "file_bugs.py",
                  "--run", copy_run, "--out", tmp_j / "bugs2",
                  "--specs", EX / "specs", "--cases", DEMO / "03-cases",
                  "--requirements", DEMO / "01-requirements.json")
            n2 = len(list((tmp_j / "bugs2").glob("BUG-*.md")))
            titles2 = " ".join(f.read_text(encoding="utf-8")
                               for f in (tmp_j / "bugs2").glob("BUG-*.md"))
            r.truth("人工复核能撤回一张单子(且其它单子还在)",
                    n2 == after - 1 and "REQ-SETTLE-C01" not in titles2,
                    f"复核前 {after},复核后 {n2}")

            # 复核为 fail 时保留单子,并把复核痕迹写进 bug 单
            r.cmd("人工复核: 确认一条 fail 成立", 0, PY,
                  SKILL_DIR / "scripts" / "judge.py", "--run", copy_run,
                  "--override", "INV-IDEM-C01/E1", "--verdict", "fail",
                  "--by", "selftest", "--reason", "复核后仍成立", "--quiet")
            r.cmd("再提一次 bug", 0, PY, SKILL_DIR / "scripts" / "file_bugs.py",
                  "--run", copy_run, "--out", tmp_j / "bugs3",
                  "--specs", EX / "specs", "--cases", DEMO / "03-cases",
                  "--requirements", DEMO / "01-requirements.json")
            txt3 = " ".join(f.read_text(encoding="utf-8")
                            for f in (tmp_j / "bugs3").glob("BUG-*.md"))
            r.truth("bug 单写明人工复核(谁改的/原结论/原因)",
                    "判定复核" in txt3 and "selftest" in txt3
                    and "复核后仍成立" in txt3, txt3[-200:])
        finally:
            shutil.rmtree(tmp_j, ignore_errors=True)

        # ------------------------------------------------------ 6c 异常扫描
        section("6c. 异常扫描(确定性信号)")
        tmp_s = Path(tempfile.mkdtemp())
        try:
            srun = tmp_s / "04-runs" / run_json.name
            srun.parent.mkdir(parents=True, exist_ok=True)
            ev = srun.parent / "evidence"
            ev.mkdir(parents=True, exist_ok=True)
            shutil.copy(run_json, srun)

            # plan-only:截图只有路径没有文件 -> 只能报"无法判断",退出码 3
            r.cmd("异常扫描: 无证据应报 3(无法判断,不是『没有异常』)", 3, PY,
                  SKILL_DIR / "scripts" / "scan_anomalies.py", "--run", srun, "--quiet")
            report = srun.parent.parent / "anomalies.md"
            text = report.read_text(encoding="utf-8") if report.exists() else ""
            r.truth("异常扫描报告写明证据缺口(不是『无异常』)",
                    "证据缺口" in text and "无法判断" in text, text[:160])

            # 放入真证据:一份带崩溃的 logcat + 一张白屏截图
            import json as _json

            import coordmap as CM  # noqa: E402

            logcat = ev / "INV-COIN-C01_S3_logcat.txt"
            logcat.write_text(
                "09-18 10:00:00 I GameCore: start\n"
                "09-18 10:00:02 E AndroidRuntime: FATAL EXCEPTION: main\n"
                "09-18 10:00:03 I libc: Fatal signal 11 (SIGSEGV), code 1\n",
                encoding="utf-8")
            white = ev / "INV-COIN-C01_S4_white.png"
            white.write_bytes(CM.encode_png_8bit(32, 32, 3, bytes((255, 255, 255) * 1024)))
            data = _json.loads(srun.read_text(encoding="utf-8"))
            data["cases"][0]["evidenceIndex"] += [
                {"step": "S3", "name": "logcat", "path": str(logcat)},
                {"step": "S4", "name": "white", "path": str(white)},
            ]
            srun.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
            r.cmd("异常扫描: 有崩溃与白屏时退出码 1", 1, PY,
                  SKILL_DIR / "scripts" / "scan_anomalies.py", "--run", srun,
                  "--json", tmp_s / "anomalies.json")
            found = json.loads((tmp_s / "anomalies.json").read_text(encoding="utf-8"))
            types = {f["type"] for f in found["findings"]}
            r.truth("异常扫描: 扫出崩溃与白屏信号",
                    "crash" in types and "white_screen" in types, str(sorted(types)))
            r.truth("异常扫描: 崩溃发现带行号与原文(可复核)",
                    all(f.get("line") and f.get("raw")
                        for f in found["findings"] if f["type"] == "crash"),
                    str([f for f in found["findings"] if f["type"] == "crash"][:1]))
        finally:
            shutil.rmtree(tmp_s, ignore_errors=True)

        # ------------------------------------------------------ 6d 复现性(多轮)
        section("6d. 复现性(多轮)")
        tmp_m = Path(tempfile.mkdtemp())
        try:
            base = json.loads(run_json.read_text(encoding="utf-8"))
            r1 = json.loads(json.dumps(base))
            r1["runId"] = "run-r1"
            r2 = json.loads(json.dumps(base))
            r2["runId"] = "run-r2"
            flipped = None
            for case in r2["cases"]:
                for chk in case["checks"]:
                    if chk.get("verdict") == "fail" and flipped is None:
                        chk["verdict"] = "pass"
                        flipped = (case["id"], chk["source"])
            (tmp_m / "r1.json").write_text(json.dumps(r1, ensure_ascii=False),
                                           encoding="utf-8")
            (tmp_m / "r2.json").write_text(json.dumps(r2, ensure_ascii=False),
                                           encoding="utf-8")
            r.truth("多轮场景构造成功(第二轮翻掉一个 fail)", flipped is not None)
            common = (PY, SKILL_DIR / "scripts" / "file_bugs.py",
                      "--specs", EX / "specs", "--cases", DEMO / "03-cases",
                      "--requirements", DEMO / "01-requirements.json")

            r.cmd("多轮提 bug", 0, *common,
                  "--run", tmp_m / "r1.json", "--run", tmp_m / "r2.json",
                  "--out", tmp_m / "bugs_multi")
            multi = {p.name: p.read_text(encoding="utf-8")
                     for p in (tmp_m / "bugs_multi").glob("BUG-*.md")}
            r.truth("偶现判定项:bug 单写『偶现(1/2)』",
                    any("偶现(1/2)" in t and flipped[1] in t for t in multi.values()),
                    f"flipped={flipped} 未在 {len(multi)} 张单里体现")
            r.truth("稳定判定项:bug 单写『稳定(2/2)』",
                    any("稳定(2/2)" in t for t in multi.values()), str(len(multi)))
            r.truth("bug 单记录多轮 run id",
                    all("共 2 轮" in t for t in multi.values()), "缺少轮次记录")
            rep = (tmp_m / "report.md").read_text(encoding="utf-8")
            r.truth("报告有『稳定性(多轮)』并列出不一致项",
                    "## 稳定性" in rep and "不一致" in rep and flipped[0] in rep,
                    rep[:200])

            r.cmd("单轮提 bug", 0, *common,
                  "--run", tmp_m / "r1.json", "--out", tmp_m / "bugs_single")
            single = {p.name: p.read_text(encoding="utf-8")
                      for p in (tmp_m / "bugs_single").glob("BUG-*.md")}
            def repro_field(text: str) -> str:
                for line in text.splitlines():
                    if line.startswith("- **复现性**:"):
                        return line.split(":", 1)[1].strip()
                return ""

            r.truth("单轮不许冒充稳定:复现性字段写『未知』",
                    all(repro_field(t).startswith("未知") for t in single.values()),
                    str({k: repro_field(v) for k, v in list(single.items())[:2]}))
        finally:
            shutil.rmtree(tmp_m, ignore_errors=True)

    # ---------------------------------------------------------- 7 对账
    section("7. 文档-实现对账")
    docs_res = r.cmd("文档与实现一致", 0, PY, SKILL_DIR / "scripts" / "check_docs.py",
                     "--quiet")
    m_docs = re.search(r"通过\((\d+) 项检查\)", docs_res.stdout)
    docs_count = int(m_docs.group(1)) if m_docs else None

    # 用例 ID 前缀那道闸门必须真的会红 —— 拿改坏了的输入喂给它看
    import check_docs as CD  # noqa: E402  (scripts/ 已加进 sys.path)
    import validate_cases as VC  # noqa: E402

    cases_md = (SKILL_DIR / "references" / "3-cases.md").read_text(encoding="utf-8")
    schema = json.loads((SKILL_DIR / "assets" / "derived-schema.json")
                        .read_text(encoding="utf-8"))
    good_pattern = schema["properties"]["id"]["pattern"]
    OLD_PREFIXES = ("REQ", "INV", "EXP")                 # 本次修复前的代码
    OLD_PATTERN = "^(REQ|INV|EXP)-[A-Za-z0-9_.-]+$"      # 本次修复前的 schema

    def findings(text, prefixes, pattern, id_re):
        return CD.case_id_prefix_findings(text, prefixes, id_re, pattern)

    oks, bads = findings(cases_md, VC.CASE_ID_PREFIXES, good_pattern, VC.ID_RE)
    r.truth("ID 前缀: 真文档三处一致、示例都能过闸门",
            not bads and len(oks) == 3, f"bad={bads} oks={oks}")

    # 复现本次的原始缺陷:文档有 CONS-,代码与 schema 都只认 REQ/INV/EXP
    old_re = re.compile(OLD_PATTERN)
    _oks, bads_bug = findings(cases_md, OLD_PREFIXES, OLD_PATTERN, old_re)
    r.truth("ID 前缀: 文档领跑、闸门拒收 CONS- 的原始形状会被抓住",
            any("只在 3-cases.md" in b for b in bads_bug)
            and any("示例 ID 会被闸门拒掉" in b for b in bads_bug),
            f"bads={bads_bug}")

    # 原始缺陷真正发生的地方:正文里 `- ID: CONS-...` 这种示例(10-upstreams.md)
    _oks, bads_id = CD.id_example_findings(
        {"10-upstreams.md": "- ID: CONS-CFG-C01\n"},
        old_re, VC.ID_PREFIX_HINT)
    r.truth("ID 前缀: 正文 `ID:` 位置写了个闸门不收的示例会被抓住",
            any("CONS-CFG-C01" in b for b in bads_id), f"bads={bads_id}")

    docs_now = {p.name: p.read_text(encoding="utf-8")
                for p in [SKILL_DIR / "references" / "10-upstreams.md"]}
    _oks2, bads_id2 = CD.id_example_findings(docs_now, VC.ID_RE, VC.ID_PREFIX_HINT)
    r.truth("ID 前缀: 修复后同一份文档里的示例已能过闸门",
            not bads_id2, f"bads={bads_id2}")

    # 只落后一处:schema pattern 没跟上代码
    _oks, bads_schema = findings(cases_md, VC.CASE_ID_PREFIXES, OLD_PATTERN, VC.ID_RE)
    r.truth("ID 前缀: 只有 schema pattern 落后也会被抓住",
            len(bads_schema) == 1 and "schema" in bads_schema[0],
            f"bads={bads_schema}")

    # 只落后一处:文档表少一行
    _oks, bads_doc = findings(cases_md.replace("`CONS-<源对>-C<序>`", "`ZZZ-<源对>-C<序>`"),
                              VC.CASE_ID_PREFIXES, good_pattern, VC.ID_RE)
    r.truth("ID 前缀: 只有文档少一行也会被抓住",
            any("只在 3-cases.md" in b for b in bads_doc), f"bads={bads_doc}")

    r.truth("ID 前缀: 上述检查没有改动真实文档",
            (SKILL_DIR / "references" / "3-cases.md").read_text(encoding="utf-8")
            == cases_md)

    # 行尾:文档被"顺手写回"时不许换行尾。踩过:sync_doc_counts 原本用
    # newline="\n" 写回,全绿时把 HANDOFF.md 从 CRLF 悄悄改成 LF ——
    # 而 git(autocrlf) 既不报错也不显示,肉眼也看不出来。
    # 这棵树是**逐个文件跟它自己的历史**,所以判据是"写回不改变行尾",不是"跟邻居一样"。
    def endings(p: Path) -> str:
        b = p.read_bytes()
        lf, crlf = b.count(b"\n"), b.count(b"\r\n")
        if not lf:
            return "empty"
        if crlf == 0:
            return "lf"
        return "crlf" if lf == crlf else "mixed"

    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "probe.md"
        raw = "甲\r\n乙\r\n".encode("utf-8")
        probe.write_bytes(raw)
        with probe.open(encoding="utf-8", newline="") as fh:
            t = fh.read()
        with probe.open("w", encoding="utf-8", newline="") as fh:
            fh.write(t)
        r.truth("行尾: 文档写回原样保留 CRLF(不会被改成 LF)",
                probe.read_bytes() == raw, repr(probe.read_bytes()))

    r.truth("行尾: HANDOFF.md 不是混合行尾",
            endings(SKILL_DIR / "HANDOFF.md") in ("crlf", "lf"),
            endings(SKILL_DIR / "HANDOFF.md"))

    # ---------------------------------------------------------- 汇总
    section("汇总")
    print(f"通过 {r.passed},失败 {len(r.failed)}")
    if r.failed:
        print("失败项:")
        for n in r.failed:
            print(f"  - {n}")
        return 1
    print("全绿")
    # 全绿才同步文档里的项数(红的数字本身是信号,不该覆盖)
    for c in sync_doc_counts(r.passed, per_script, docs_count):
        print(f"  · 文档同步 {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
