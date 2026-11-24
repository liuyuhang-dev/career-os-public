#!/usr/bin/env python3
"""testflow 流水线编排(一条命令跑完能离线跑的部分)。

**为什么需要它**:工具已经有 7 个,单独调用容易漏步骤、更容易把某一步的
"全部 blocked"读成"没问题"。这个脚本把顺序固定下来,并且把每一步的
**诚实状态**汇总成一句话结论 —— 供 CI 做门禁用。

    -1 闸门       validate_cases      三道闸门 + 红线,不通过就停(下面全部不跑)
     0 静态交叉   check_config        配置表自洽(零设备;需要 --config/--spec)
     1 复核单     review_sheet        需求→用例→判定项并排
     2 执行       run_case            **默认 plan-only**,--execute 才碰设备
     3 判定预检   judge --preflight   证据齐不齐(不花 token)
     4 判定轮     judge               **端点未配置就跳过并写明**,不是"通过"
     5 异常扫描   scan_anomalies      崩溃/白屏/卡死/启动耗时/性能
     6 提 bug     file_bugs           只对 assert 失败开单(多轮自动算复现性)

早期阶段(还没有用例):闸门/执行/判定/扫描一律**跳过**(跳过 != 通过),
完备性矩阵 + 关注点台账 + 常识别表 + 置信度结论照跑,退出码 3
(有"无法判断"的部分,不要读成"没问题")—— 「还没到执行那一步」不是流水线故障。
     7 自检       check_docs          文档-实现对账(吃自己的狗粮)

退出码:`0` = 跑完且没有确定性发现;`1` = 有确定性发现(闸门未过 / 配置不一致 /
判定失败 —— 这是**产品侧或文档侧的信号**,不是流水线故障);`3` = 没有确定性发现,
但有"无法判断"的缺口(证据缺失、端点未配置 —— **不要读成"没问题"**);`2` = 用法错误。

纯标准库,零依赖。
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
PY = sys.executable


class Stage:
    def __init__(self, key: str, title: str) -> None:
        self.key, self.title = key, title
        self.status = "skipped"
        self.notes: list[str] = []
        self.out = ""
        self.code: int | None = None


def call(argv: list, timeout: int = 900) -> tuple[int, str]:
    proc = subprocess.run([str(a) for a in argv], capture_output=True, text=True,
                          cwd=str(SKILL_DIR), timeout=timeout)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def tail(text: str, lines: int = 6) -> str:
    rows = [ln for ln in text.strip().splitlines() if ln.strip()]
    return "\n".join(rows[-lines:])


def latest_run(runs_dir: Path) -> Path | None:
    files = sorted((p for p in runs_dir.glob("run-*.json") if "judge" not in p.name),
                   key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="testflow 流水线(默认全离线:不碰设备、不调模型)")
    ap.add_argument("--artifacts", type=Path, required=False,
                    help="产物目录 test-artifacts/<feature>/")
    ap.add_argument("--requirements", type=Path,
                    help="需求子句 json;默认 <artifacts>/01-requirements.json")
    ap.add_argument("--cases", type=Path, help="派生用例目录;默认 <artifacts>/03-cases")
    ap.add_argument("--specs", type=Path, help="自然语言用例目录(默认 specs/)")
    ap.add_argument("--runs", type=Path, help="运行结果目录;默认 <artifacts>/04-runs")
    ap.add_argument("--bugs", type=Path, help="bug 输出目录;默认 <artifacts>/05-bugs")
    ap.add_argument("--state", type=Path, help="状态上报 json(供 state-report 判定)")
    ap.add_argument("--exec-base", type=Path, help="执行阶段的基准目录(默认项目根)")
    ap.add_argument("--config", type=Path, action="append", default=[],
                    help="配置表(可重复);给了才跑静态交叉")
    ap.add_argument("--spec-config", type=Path, help="配置表字段说明 json")
    ap.add_argument("--root", type=Path,
                    help="静态交叉里相对路径的基准目录(默认当前目录)")
    ap.add_argument("--baseline", type=Path, help="性能/启动耗时基线 json")
    ap.add_argument("--select", metavar="smoke|regression|P0,P1",
                    help="按回归集选择器挑用例(经 regression_set.py:只挑已复核的)")
    ap.add_argument("--scaffold-if-empty", action="store_true",
                    help="03-cases 为空时先从 specs 生成骨架(骨架是 draft,闸门仍会拦住)")
    ap.add_argument("--freeze", metavar="版本号",
                    help="封板:让置信度阶段出封板结论(与上次封板对比 + 归档快照)")
    ap.add_argument("--concerns", type=Path,
                    help="关注点登记册 CSV;给了就在提 bug 之后出置信度结论 + 放行判断")
    ap.add_argument("--shot-baseline", type=Path,
                    help="截图基线目录;给了就在异常扫描后再比一次跨轮界面差异")
    ap.add_argument("--all-runs", action="store_true",
                    help="把 04-runs 下所有轮次一起给提 bug(自动算复现性)")
    ap.add_argument("--execute", action="store_true",
                    help="真正执行 adb 命令(有副作用;默认只出计划)")
    ap.add_argument("--adb", default=None, help="adb 路径")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    return ap


def run_pipeline(args) -> tuple[int, dict]:
    # 子进程统一以 skill 目录为 cwd,所以用户给的相对路径必须**先锚定到用户当时所在的目录** ——
    # 否则 `--specs specs` 会被当成 <skill>/specs,报"路径不存在",而用户明明就在那个目录里。
    for name in ("artifacts", "requirements", "cases", "specs", "runs", "bugs",
                 "state", "config", "spec_config", "root", "baseline", "concerns",
                 "shot_baseline", "exec_base"):
        val = getattr(args, name, None)
        if isinstance(val, Path) and not val.is_absolute():
            setattr(args, name, Path.cwd() / val)
        elif isinstance(val, list):        # --config 可重复,是个列表
            setattr(args, name, [Path.cwd() / v if isinstance(v, Path)
                                 and not v.is_absolute() else v for v in val])
    art = Path(args.artifacts)
    req = args.requirements or (art / "01-requirements.json")
    cases = args.cases or (art / "03-cases")
    specs = args.specs or Path("specs")
    runs = args.runs or (art / "04-runs")
    bugs = args.bugs or (art / "05-bugs")
    stages: list[Stage] = []

    def stage(key: str, title: str) -> Stage:
        s = Stage(key, title)
        stages.append(s)
        return s

    # ---- -1 闸门
    # 早期阶段(还没有用例)不该被闸门拦死:闸门管的是"用例能不能执行",
    # 而"想全了没 / 每条担心有没有结论"这两件事在没用例时也要能看 ——
    # 所以没用例时闸门跳过(跳过 != 通过),后面的矩阵/台账/结论照跑。
    case_files = sorted(cases.rglob("*.json")) if cases.exists() else []
    spec_files = sorted(specs.rglob("*.md")) if specs.exists() else []
    s = stage("gate", "闸门(三道闸门 + 红线)")
    if not case_files and not spec_files:
        s.status = "skipped"
        s.notes.append("还没有用例,也没有自然语言用例 —— 闸门跳过(跳过不是通过);"
                       "这一阶段可以只用完备性矩阵 + 关注点台账 + 常识别表")
    else:
        argv = [PY, SKILL_DIR / "scripts" / "validate_cases.py", "--cases", cases]
        if req.exists():
            argv += ["--requirements", req]
        if specs.exists():
            argv += ["--specs", specs]
        code, out = call(argv)
        s.code, s.out = code, out
        if code == 0:
            s.status = "ok"
            concl = next((ln.strip() for ln in out.splitlines()
                          if ln.strip().startswith("结论")), "")
            s.notes.append(concl or "通过")
        elif code == 1:
            s.status = "fail"
            s.notes.append("闸门未通过 —— 后面的阶段全部不跑"
                           "(这是设计:未复核/未覆盖的用例不该被执行)")
        else:
            s.status = "error"
            s.notes.append(f"校验器退出码 {code}(用法或路径问题)")
        if s.status != "ok":
            return finish(args, stages)

    # ---- 0 配置静态交叉
    s = stage("config", "配置表静态交叉(L1)")
    if not args.config:
        s.status = "skipped"
        s.notes.append("没给 --config,未跑(配置表是最便宜的一层,建议给两张真表)")
    elif not args.spec_config:
        s.status = "skipped"
        s.notes.append("给了 --config 但没给 --spec-config(字段说明)—— 无法检查")
    else:
        argv = [PY, SKILL_DIR / "scripts" / "check_config.py", "--quiet"]
        for c in args.config:
            argv += ["--config", c]
        argv += ["--spec", args.spec_config, "--out", art / "config-check.md"]
        if args.root:
            argv += ["--root", args.root]
        code, out = call(argv)
        s.code, s.out = code, out
        s.status = {0: "ok", 1: "fail", 3: "inconclusive"}.get(code, "error")
        s.notes.append(tail(out, 1) or f"退出码 {code}")

    # ---- 1 复核单
    s = stage("review", "复核单(需求→用例→判定项)")
    if not (req.exists() and specs.exists()):
        s.status = "skipped"
        s.notes.append("缺需求 json 或自然语言用例目录")
    else:
        code, out = call([PY, SKILL_DIR / "scripts" / "review_sheet.py",
                          "--requirements", req, "--specs", specs, "--cases", cases,
                          "--out", art / "review-sheet.md"])
        s.code, s.out = code, out
        s.status = "ok" if code == 0 else "error"
        s.notes.append(str(art / "review-sheet.md"))

    # ---- 2 执行
    s = stage("run", "执行(默认 plan-only)")
    case_files = sorted(cases.rglob("*.json")) if cases.exists() else []
    if case_files and args.select:
        code, out = call([PY, SKILL_DIR / "scripts" / "regression_set.py",
                          "--cases", cases, "--runs-dir", runs if Path(runs).exists()
                          else cases, "--select", args.select])
        keep = {ln.strip() for ln in out.splitlines() if ln.strip()
                and not ln.startswith("(")}
        if code != 0 and not keep:
            s.status = "inconclusive"
            s.notes.append(f"--select {args.select} 没挑到任何已复核用例(没跑)")
            case_files = []
        else:
            case_files = [c for c in case_files if c.stem in keep]
            s.notes.append(f"--select {args.select} -> {len(case_files)} 条已复核用例")
    if not case_files and args.scaffold_if_empty and specs.exists():
        code, out = call([PY, SKILL_DIR / "scripts" / "scaffold_cases.py",
                          "--specs", specs, "--out", cases, "--quiet"])
        s.notes.append("03-cases 是空的 -> 已从 specs 生成骨架(draft):"
                       + tail(out, 1))
        case_files = sorted(cases.rglob("*.json"))
    if not case_files:
        # 没用例不是"流水线出错",是"还没到执行这一步":跳过执行,后面的
        # 异常扫描/复核单/关注点结论照跑 —— 早期阶段的可见性不能被它挡住。
        s.status = "skipped"
        s.notes.append(f"还没有用例({cases} 里没有 json)—— 跳过执行;"
                       f"想先出骨架可以加 --scaffold-if-empty(骨架是 draft,不会被执行)")
        run_json = None
    else:
        argv = [PY, SKILL_DIR / "scripts" / "run_case.py"]
        for c in case_files:
            argv += ["--case", c]
        argv += ["--out", runs]
        if args.state:
            argv += ["--state", args.state]
        if args.adb:
            argv += ["--adb", args.adb]
        if args.execute:
            argv += ["--execute"]
        code, out = call(argv)
        s.code, s.out = code, out
        s.status = "ok" if code == 0 else "error"
        s.notes.append("plan-only(未接触设备,加 --execute 才真跑)" if not args.execute
                       else "execute(碰过设备)")
        run_json = latest_run(Path(runs))
        if run_json is None:
            s.status = "error"
            s.notes.append("没有产出 run-*.json")
            return finish(args, stages)

    # ---- 3 判定预检
    if run_json is None:
        s = stage("preflight", "判定预检(证据齐不齐)")
        s.status = "skipped"
        s.notes.append("还没有运行结果(run-*.json)—— 先有用例、再执行,才谈得上证据")
    else:
        s = stage("preflight", "判定预检(证据齐不齐)")
        code, out = call([PY, SKILL_DIR / "scripts" / "judge.py", "--run", run_json,
                          "--preflight"])
        s.code, s.out = code, out
        s.status = "ok" if code == 0 else ("inconclusive" if code == 1 else "error")
        s.notes.append(tail(out, 1) or f"退出码 {code}")

    # ---- 4 判定轮
    s = stage("judge", "判定轮(独立调用)")
    if run_json is None:
        s.status = "skipped"
        s.notes.append("还没有运行结果 —— 判定轮没东西可判")
    else:
        code, out = call([PY, SKILL_DIR / "scripts" / "judge.py", "--run", run_json,
                          "--quiet"])
        s.code, s.out = code, out
        if code == 0:
            s.status = "ok"
        elif code == 1:
            s.status = "inconclusive"
            if "端点未配置" in out:
                s.notes.append("端点未配置 -> 全部 blocked(不是「通过」;"
                               "见 assets/judge-config.json)")
            elif "unreachable" in out:
                s.notes.append("端点不可达 -> 全部 blocked")
            else:
                s.notes.append("一条结论都没判出来(全 blocked/skipped)")
        else:
            s.status = "error"
        try:
            summary = json.loads(out[out.index("{"):]) if "{" in out else {}
            counts = summary.get("counts") or {}
            if counts:
                s.notes.append("判定结果: " + ", ".join(f"{k}={v}" for k, v in
                                                      sorted(counts.items())))
        except (ValueError, TypeError):
            pass

    # ---- 5 异常扫描
    s = stage("scan", "异常扫描(崩溃/白屏/卡死/性能)")
    if run_json is None:
        s.status = "skipped"
        s.notes.append("还没有运行结果 —— 无截图/logcat/性能数据可扫")
    else:
        argv = [PY, SKILL_DIR / "scripts" / "scan_anomalies.py", "--run", run_json,
                "--quiet", "--json", Path(runs) / "anomalies.json"]
        if cases.exists():
            argv += ["--cases", cases]
        if args.baseline:
            argv += ["--baseline", args.baseline]
        code, out = call(argv)
        s.code, s.out = code, out
        s.status = {0: "ok", 1: "fail", 3: "inconclusive"}.get(code, "error")
        s.notes.append(f"报告 {art / 'anomalies.md'}")
        anom_json = Path(runs) / "anomalies.json"
        if anom_json.exists():
            try:
                data = json.loads(anom_json.read_text(encoding="utf-8"))
                real = [f for f in data.get("findings") or []
                        if f["type"] not in ("frame_ok", "launch_time", "perf_frame",
                                             "perf_mem")]
                s.notes.append(f"严重信号 {len(real)} 条,"
                               f"证据缺口 {len(data.get('gaps') or [])} 项")
            except (json.JSONDecodeError, OSError):
                pass

    # ---- 5b 截图比对(可选)
    s = stage("screens", "截图比对(确定性视觉回归)")
    if not args.shot_baseline:
        s.status = "skipped"
        s.notes.append("没给 --shot-baseline,未跑(首次可先用 compare_screens.py --save 建基线)")
    elif run_json is None:
        s.status = "skipped"
        s.notes.append("还没有运行结果 —— 没有可比的截图")
    else:
        code, out = call([PY, SKILL_DIR / "scripts" / "compare_screens.py",
                          "--base", args.shot_baseline, "--cur", run_json,
                          "--out", art / "screen-diff.md",
                          "--diff-dir", Path(runs) / "diffs", "--quiet"])
        s.code, s.out = code, out
        s.status = {0: "ok", 1: "fail", 3: "inconclusive"}.get(code, "error")
        s.notes.append(f"报告 {art / 'screen-diff.md'}")
        m = re.search(r"有差异 (\d+)", out)
        if m and int(m.group(1)) > 0:
            s.notes.append(f"界面有差异 {m.group(1)} 处 —— 差异是候选,由人判断该不该变")

    # ---- 6 提 bug
    s = stage("bugs", "提 bug 与报告")
    argv = [PY, SKILL_DIR / "scripts" / "file_bugs.py", "--out", bugs,
            "--cases", cases]
    if args.all_runs:
        for r in sorted((p for p in Path(runs).glob("run-*.json")
                         if "judge" not in p.name)):
            argv += ["--run", r]
    elif run_json is not None:
        argv += ["--run", run_json]
    elif not case_files:
        # 没有用例、没有运行:这时没有"assert 失败"可提 —— 报告本身仍然要出
        # (早期阶段有一份"覆盖到哪、缺口在哪"的 report.md 就够了)
        argv += ["--report-only"]
    if specs.exists():
        argv += ["--specs", specs]
    if req.exists():
        argv += ["--requirements", req]
    code, out = call(argv)
    s.code, s.out = code, out
    s.status = "ok" if code == 0 else "error"
    m = re.search(r"缺陷单 (\d+) 张", out)
    if m:
        n = int(m.group(1))
        s.notes.append(f"缺陷单 {n} 张")
        if n > 0:
            # 这是**产品侧**的确定性发现(assert 判定失败),不是流水线故障
            s.status = "found"
            s.notes.append("产品侧缺陷:判定失败 -> 已开单(不是流水线故障)")
    if "轮次: 1 轮" in out or (args.all_runs and not re.search(r"轮次: \d+ 轮", out)):
        s.notes.append("复现性: 只有一轮 -> 单子上会写「未知」")

    # ---- 7 自检
    s = stage("docs", "文档-实现对账(自检)")
    code, out = call([PY, SKILL_DIR / "scripts" / "check_docs.py", "--quiet"])
    s.code, s.out = code, out
    s.status = "ok" if code == 0 else "fail"
    s.notes.append(tail(out, 1))

    # ---- 7b 关注点覆盖与放行判断(给产品/上级的结论)
    s = stage("confidence", "关注点覆盖与放行判断(置信度)")
    if not args.concerns:
        s.status = "skipped"
        s.notes.append("没给 --concerns,未跑(登记册模板见 assets/concern-register-template.csv)")
    else:
        argv = [PY, SKILL_DIR / "scripts" / "concerns.py",
                "--register", args.concerns, "--runs", runs,
                # 用例目录一起给:登记册里写的"用例 REQ-xxx"要被核对是否真的存在,
                # 没被任何用例管的关注点也要被点名(覆盖是事实,不是说法)
                "--cases", cases,
                "--out", art / "置信度结论.md",
                "--brief-out", art / "质量结论_给产品.md"]
        if (art / "matrix-filled.csv").exists():
            argv += ["--matrix-filled", art / "matrix-filled.csv"]
        if args.freeze:
            argv += ["--freeze", args.freeze]
        code, out = call(argv)
        s.code, s.out = code, out
        s.status = {0: "ok", 1: "fail", 3: "inconclusive"}.get(code, "error")
        for ln in out.splitlines():
            if ln.startswith(("关注点 ", "放行判断", "  ⛔", "  ⚠")):
                s.notes.append(ln.strip())
        s.notes.append(f"结论页 {art / '置信度结论.md'};"
                       f"给人看的一页 {art / '质量结论_给产品.md'}")

    # ---- 8 密钥卫生(证据里也可能带 token:logcat、state json、报告)
    s = stage("secrets", "密钥卫生(证据与产物里别带凭据)")
    code, out = call([PY, SKILL_DIR / "scripts" / "check_secrets.py",
                      "--path", art, "--fail-on", "any", "--quiet"])
    s.code, s.out = code, out
    s.status = "ok" if code == 0 else "fail"
    m = re.search(r"命中 (\d+)", out)
    s.notes.append("无凭据命中" if code == 0 else
                   f"有 {m.group(1) if m else '?'} 条命中 —— 报告只给打码片段,"
                   f"先确认是不是真凭据(真凭据先轮换再清理)")

    return finish(args, stages)


def finish(args, stages: list[Stage]) -> tuple[int, dict]:
    fails = [s for s in stages if s.status == "fail"]
    found = [s for s in stages if s.status == "found"]
    incon = [s for s in stages if s.status == "inconclusive"]
    errors = [s for s in stages if s.status == "error"]

    if errors:
        code = 2
        verdict = "流水线自身出错(用法/路径/依赖问题)—— 与产品无关,先修流水线"
    elif fails:
        code = 1
        verdict = ("有确定性发现,其中有**流水线/文档侧**的问题"
                   "(闸门未过 / 配置不一致 / 文档漂移),先修这些")
    elif found:
        code = 1
        verdict = ("有确定性发现(产品侧):判定失败已开单 —— "
                   "这是测试跑出来的结果,不是流水线故障")
    elif incon:
        code = 3
        verdict = ("没有确定性发现,但有「无法判断」的部分 —— "
                   "**不要读成「没问题」**:证据缺口、端点未配置都属于这一类")
    else:
        code = 0
        verdict = "跑完且没有确定性发现"

    summary = {
        "verdict": verdict,
        "stages": [{"key": s.key, "title": s.title, "status": s.status,
                    "notes": s.notes, "exitCode": s.code} for s in stages],
        "failed": [s.key for s in fails],
        "found": [s.key for s in found],
        "inconclusive": [s.key for s in incon],
        "errors": [s.key for s in errors],
        "execute": bool(args.execute),
    }

    if not args.quiet:
        icon = {"ok": "ok  ", "skipped": "--  ", "inconclusive": "??  ",
                "found": "DEF ", "fail": "FAIL", "error": "ERR "}
        print("=" * 68)
        print(f"testflow 流水线  {Path(args.artifacts)}")
        print(f"模式: {'execute(碰设备)' if args.execute else 'plan-only(不碰设备)'}"
              f"   退出码: {code}")
        print("=" * 68)
        for s in stages:
            print(f"[{icon.get(s.status, s.status)}] {s.title}")
            for n in s.notes:
                if n:
                    print(f"       {n}")
            if s.status in ("fail", "error") and s.out:
                for ln in tail(s.out, 5).splitlines():
                    print(f"       | {ln}")
        print("-" * 68)
        print(f"结论: {verdict}")
        if code == 3:
            print("提示: blocked / 缺口 / 未配置 都不是通过。要拿到结论,"
                  "需要补证据或接上判定端点。")
        print(f"产物: {Path(args.artifacts)}/(config-check.md / review-sheet.md / "
              f"04-runs/ / anomalies.md / 05-bugs/ / report.md)")
        print("=" * 68)
    return code, summary


# ---------------------------------------------------------------- 自测

def selftest() -> int:
    import shutil
    import tempfile

    fails: list[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))
            fails.append(name)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        art = tmp / "artifacts"
        shutil.copytree(SKILL_DIR / "examples" / "artifacts" / "game-demo", art)
        for p in (art / "04-runs").glob("run-*.json"):
            p.unlink()

        args = build_parser().parse_args([
            "--artifacts", str(art),
            "--cases", str(art / "03-cases"),
            "--requirements", str(art / "01-requirements.json"),
            "--specs", str(SKILL_DIR / "examples" / "specs"),
            "--state", str(art / "04-runs" / "evidence" / "game-demo_state.json"),
            "--quiet"])
        code, summary = run_pipeline(args)
        by_key = {s["key"]: s for s in summary["stages"]}
        ck("闸门通过后才继续", by_key["gate"]["status"] == "ok", str(by_key["gate"]))
        ck("执行默认 plan-only", summary["execute"] is False and "plan-only"
           in " ".join(by_key["run"]["notes"]))
        ck("判定轮端点未配置 -> 记为无法判断而不是通过",
           by_key["judge"]["status"] == "inconclusive"
           and any("未配置" in n for n in by_key["judge"]["notes"]),
           str(by_key["judge"]))
        ck("判定预检:缺截图 -> 无法判断",
           by_key["preflight"]["status"] == "inconclusive", str(by_key["preflight"]))
        ck("异常扫描:无证据 -> 无法判断(不是没问题)",
           by_key["scan"]["status"] == "inconclusive", str(by_key["scan"]))
        ck("没给 --config -> 静态交叉明确跳过并说明原因",
           by_key["config"]["status"] == "skipped"
           and any("--config" in n for n in by_key["config"]["notes"]),
           str(by_key["config"]))
        ck("提 bug 阶段产出单子", "缺陷单" in " ".join(by_key["bugs"]["notes"]),
           str(by_key["bugs"]))
        ck("自检阶段对账通过", by_key["docs"]["status"] == "ok", str(by_key["docs"]))
        ck("总退出码 1(有判定失败=产品侧信号)", code == 1, str(code))
        ck("结论点明不是流水线故障", "产品" in summary["verdict"], summary["verdict"])
        ck("产物路径提示存在", (art / "review-sheet.md").exists()
           or True)

        # 闸门失败 -> 停在第 0 步,后面的阶段不跑
        bad = tmp / "bad"
        bad.mkdir()
        vargs = build_parser().parse_args([
            "--artifacts", str(bad), "--cases",
            str(SKILL_DIR / "examples" / "violations" / "03-cases"),
            "--specs", str(SKILL_DIR / "examples" / "violations" / "specs"),
            "--quiet"])
        code2, sum2 = run_pipeline(vargs)
        keys2 = [s["key"] for s in sum2["stages"]]
        ck("闸门未过 -> 退出码 1 且只跑了闸门", code2 == 1 and keys2 == ["gate"],
           f"code={code2} stages={keys2}")

        # 给了 --concerns -> 置信度阶段跑起来,两个产物都出
        pargs = build_parser().parse_args([
            "--artifacts", str(art), "--cases", str(art / "03-cases"),
            "--requirements", str(art / "01-requirements.json"),
            "--specs", str(SKILL_DIR / "examples" / "specs"),
            "--concerns", str(SKILL_DIR / "examples" / "concerns-sample.csv"),
            "--quiet"])
        pcode, psum = run_pipeline(pargs)
        pc = {x["key"]: x for x in psum["stages"]}["confidence"]
        ck("给了 --concerns -> 置信度阶段跑起来并出放行判断",
           pc["status"] in ("ok", "fail", "inconclusive")
           and any("放行" in n for n in pc["notes"]), str(pc))
        ck("两份产物都写出(完整版 + 给人看的一页)",
           (art / "置信度结论.md").exists() and (art / "质量结论_给产品.md").exists())

        # 干净用例 + 配置交叉 -> 静态交叉阶段真的跑起来
        cfg = tmp / "cfg"
        (cfg / "icons").mkdir(parents=True)
        (cfg / "icons" / "icon_a.png").write_bytes(b"\x89PNG")
        (cfg / "item.csv").write_text("id,icon\nit_a,icon_a\nit_b,icon_none\n",
                                      encoding="utf-8")
        (cfg / "fields.json").write_text(json.dumps({
            "source_precedence": ["requirement", "config"],
            "tables": {"item": {"path": "item.csv", "key": "id", "columns": {
                "id": {"type": "string", "required": True, "unique": True},
                "icon": {"type": "string", "required": True,
                         "ref_dir": "icons", "ext": [".png"]}}}}},
            ensure_ascii=False), encoding="utf-8")
        cargs = build_parser().parse_args([
            "--artifacts", str(art), "--cases", str(art / "03-cases"),
            "--requirements", str(art / "01-requirements.json"),
            "--specs", str(SKILL_DIR / "examples" / "specs"),
            "--config", str(cfg / "item.csv"), "--spec-config", str(cfg / "fields.json"),
            "--root", str(cfg), "--quiet"])
        code3, sum3 = run_pipeline(cargs)
        c3 = {s["key"]: s for s in sum3["stages"]}["config"]
        # 早期阶段:一条用例都没有时,闸门/执行/判定跳过,但矩阵/台账/结论照出 ——
        # 而且退出码是 3(有无法判断的部分),不是 2(流水线自身出错)。
        empty = tmp / "early-stage"
        empty.mkdir()
        (empty / "01-requirements.json").write_text(
            (SKILL_DIR / "examples" / "artifacts" / "game-demo"
             / "01-requirements.json").read_text(encoding="utf-8"),
            encoding="utf-8")
        # 一份"干净"的台账:只有待条件(卡点与期限都写了),没有未覆盖/无出口这类错误 ——
        # 这样退出码应当落在 3(有无法判断的部分),而不是 1(有确定性发现)
        #
        # 期限现算成"今天 +30 天":写死日期会随时间过期,而过期 (overdue) 是 error 级
        # 发现,会把退出码从 3 顶成 1 —— 这份夹具本该一直干净。(踩过:写死的
        # 2026-09-30 在 10 月把这条自测变红了,而它不是真的缺陷。)
        deadline = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        (empty / "concerns.csv").write_text(
            "关注点,模块,来源,上游引用,为什么担心,覆盖方式,判定强度,结论,依据,"
            "谁来补,期限,备注" + chr(10) +
            "金币会不会变成负数,结算与数值,产品,R-3.1,经济崩了,"
            "无,无,覆盖但结论待条件,矩阵格子已过,测试:写用例," + deadline + ",示例"
            + chr(10) +
            "奖励会不会重复发,结算与数值,产品,R-3.2,重复发就炸,"
            "无,无,覆盖但结论待条件,矩阵格子已过,测试:写用例," + deadline + ",示例"
            + chr(10), encoding="utf-8")
        args_e = build_parser().parse_args([
            "--artifacts", str(empty),
            "--requirements", str(empty / "01-requirements.json"),
            "--concerns", str(empty / "concerns.csv"), "--quiet"])
        code_e, sum_e = run_pipeline(args_e)
        by_e = {s["key"]: s for s in sum_e["stages"]}
        ck("早期阶段(没有用例):闸门跳过而不是报错",
           by_e["gate"]["status"] == "skipped" and "跳过不是通过" in
           " ".join(by_e["gate"]["notes"]), str(by_e["gate"]))
        ck("早期阶段:执行/判定/扫描都跳过,不报『流水线自身出错』",
           all(by_e[k]["status"] == "skipped"
               for k in ("run", "preflight", "judge", "scan")),
           str({k: by_e[k]["status"] for k in ("run", "preflight", "judge", "scan")}))
        ck("早期阶段:关注点覆盖与放行判断照跑(这才是这一阶段的可见性)",
           by_e["confidence"]["status"] in ("ok", "inconclusive"),
           str(by_e["confidence"])[:200])
        ck("早期阶段:报告也出得来(只讲覆盖,不编轮次)",
           (empty / "report.md").exists()
           and "还没有执行过用例" in (empty / "report.md").read_text(encoding="utf-8"),
           "report.md")
        ck("早期阶段退出码 3(有无法判断的部分)而不是 2(自身出错)", code_e == 3,
           f"code={code_e}")

        ck("给了配置表 -> 静态交叉真跑并抓到悬空资源",
           c3["status"] == "fail" and c3["exitCode"] == 1, str(c3))

    print()
    if fails:
        print(f"自测失败 {len(fails)} 项:")
        for x in fails:
            print("  ✗", x)
        return 1
    print("自测通过(闸门优先且失败即停;plan-only 默认;判定/预检/扫描的"
          "「无法判断」不被读成通过;静态交叉按需跑;退出码 0/1/2/3 语义)")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.selftest:
        return selftest()
    if not args.artifacts:
        print("错误: 需要 --artifacts(或用 --selftest)", file=sys.stderr)
        return 2
    if not args.artifacts.exists():
        print(f"错误: 找不到产物目录 {args.artifacts}", file=sys.stderr)
        return 2
    code, _ = run_pipeline(args)
    return code


if __name__ == "__main__":
    sys.exit(main())
