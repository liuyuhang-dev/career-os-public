#!/usr/bin/env python3
"""testflow 提 bug 与报告。

只对 **assert 判定失败** 开单。blocked / skipped / pending_manual /
pending_judgment 都不开单 —— 它们反映的是我们自己的能力缺口或待办,
不是产品缺陷,开成 bug 会污染缺陷库。

同时产出 report.md,其中"实际执行率"是必须报的指标:
通过率高但执行率低的报告是误导性的。

纯标准库,零依赖。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_cases import collect_specs  # noqa: E402

# 这些模块的缺陷严重级不低于 P1
CRITICAL_KEYWORDS = ("coin", "gold", "gem", "pay", "order", "settle", "save",
                     "coinDelta", "currency", "purchase", "reward")

SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def looks_critical(text: str) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in CRITICAL_KEYWORDS)


def severity_for(case: dict, chk: dict) -> str:
    base = case.get("priority") or "P2"
    probe = f"{chk.get('path', '')} {case.get('title', '')} {chk.get('source', '')}"
    if looks_critical(probe):
        # 金额/结算/存档类不低于 P1
        if SEVERITY_RANK.get(base, 2) > 1:
            return "P1"
    return base


def expectation_text(specs: dict, case_id: str, source: str, case: dict) -> str:
    spec = specs.get(case_id)
    if spec and source in spec["items"]:
        return spec["items"][source]
    chk_match = next((c for c in case.get("checks") or []
                      if c.get("source") == source), None)
    if chk_match and chk_match.get("question"):
        return chk_match["question"]
    return "(未找到期望原文 —— 请核对 specs)"


def label(key: str) -> str:
    """PRE1 -> PRE-1,E1 -> E1,S1 -> S1"""
    m = re.match(r"^(PRE|S|E)(\d+)$", key)
    return f"{m.group(1)}-{m.group(2)}" if m else key


def verdict_history(runs: list[dict]) -> dict[tuple, list[str | None]]:
    """(用例ID, 判定项) -> 每轮的 verdict(None = 那一轮没有这条判定项)。

    多轮跑同一批用例,才谈得上"稳定"还是"偶现"。单轮只能写未知。
    """
    hist: dict[tuple, list[str | None]] = {}
    for i, run in enumerate(runs):
        for case in run.get("cases") or []:
            for chk in case.get("checks") or []:
                key = (case.get("id"), chk.get("source"))
                hist.setdefault(key, [None] * len(runs))[i] = chk.get("verdict")
    return hist


def reproducibility(row: list[str | None]) -> tuple[str, bool]:
    """由多轮 verdict 得出复现性文本。返回 (文本, 是否偶现)。

    6-triage 要求 bug 单写明复现性,而"稳定"必须由多轮实际结果支撑 ——
    只跑一轮就写"稳定"是编的。
    """
    seen = [v for v in row if v is not None]
    fails = sum(1 for v in seen if v == "fail")
    if len(seen) <= 1:
        return f"未知(只跑了 {len(seen)} 轮 —— 判稳定/偶现需至少 2 轮)", False
    if fails == len(seen):
        return f"稳定({fails}/{len(seen)})", False
    if fails:
        return f"偶现({fails}/{len(seen)})", True
    return f"未复现(0/{len(seen)})", True


def repro_steps(specs: dict, case: dict) -> list[str]:
    lines: list[str] = []
    spec = specs.get(case.get("id", ""))
    if spec:
        for key in sorted((k for k in spec["items"] if k.startswith("PRE")),
                          key=lambda s: int(s[3:])):
            lines.append(f"前置 {label(key)}: {spec['items'][key]}")
        for key in sorted((k for k in spec["items"]
                           if k.startswith("S") and not k.startswith("PRE")),
                          key=lambda s: int(s[1:])):
            lines.append(f"步骤 {label(key)}: {spec['items'][key]}")
    if not lines:
        for act in case.get("preconditions") or []:
            lines.append(f"前置 {act.get('id', '?')}: {act.get('action')} {act.get('note', '')}")
        for act in case.get("steps") or []:
            lines.append(f"步骤 {act.get('id', '?')}: {act.get('action')} {act.get('note', '')}")
    return lines or ["(用例里没有可重建的步骤描述 —— 需要补)"]


def bundle_evidence(idx: int, evidence_paths: list[str], out_dir: Path,
                    bug_dir_name: str) -> tuple[list[str], dict]:
    """把一张 bug 单的证据拷到它自己的目录里,并写清单(来源/哈希/大小)。

    为什么要拷:bug 单原本引用的是 `04-runs/evidence/` 下的绝对路径 —— 单子发给研发
    或换台机器打开就断链了。拷贝到 `05-bugs/BUG-00N.evidence/` 后,单子与证据一起走。

    拷不了的(文件不在)如实记进清单,不静默丢。
    """
    dest = out_dir / bug_dir_name
    dest.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"files": [], "missing": [], "totalBytes": 0}
    shown: list[str] = []
    used: set[str] = set()
    for src in evidence_paths:
        src_path = Path(src)
        if not src_path.exists():
            manifest["missing"].append(src)
            shown.append(src)                      # 保留原路径,并标明缺失
            continue
        name = src_path.name
        if name in used:                            # 重名加前缀,别覆盖
            name = f"{src_path.parent.name}_{name}"
        used.add(name)
        target = dest / name
        try:
            data = src_path.read_bytes()
        except OSError as exc:
            manifest["missing"].append(f"{src}(读失败: {type(exc).__name__})")
            shown.append(src)
            continue
        target.write_bytes(data)
        manifest["files"].append({"file": name, "source": str(src_path),
                                  "bytes": len(data),
                                  "sha256": hashlib.sha256(data).hexdigest()})
        manifest["totalBytes"] += len(data)
        shown.append(str(out_dir / bug_dir_name / name))
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return shown, manifest


def render_bug(idx: int, case: dict, chk: dict, run: dict, specs: dict,
               evidence_paths: list[str], repro: str = "未知",
               run_ids: list[str] | None = None,
               shown_evidence: list[str] | None = None) -> str:
    cid = case.get("id", "?")
    src = chk.get("source", "?")
    title = f"{cid} / {src} 未满足:{case.get('title', '')}"
    sev = severity_for(case, chk)
    refs = case.get("req_refs") or []
    device = case.get("device") or {}
    net = case.get("network") or {}

    expect = chk.get("expect") or {}
    lines = [
        f"# BUG-{idx:03d} {title}",
        "",
        f"- **严重级**: {sev}",
        "- **状态**: open",
        f"- **关联用例**: {cid}",
        f"- **关联需求**: {', '.join(refs) if refs else '—(不变量用例)'}",
        f"- **判定项**: {src}",
        f"- **期望来源**: `{case.get('expect_source')}`"
        + ("(硬要求)" if case.get("expect_source") == "requirement" else "(软要求·通用不变量)"),
        f"- **判定模式**: `{chk.get('mode')}`",
        f"- **复现性**: {repro}",
        *([f"- **判定复核**: {jb['overriddenBy']} 把 "
           f"{jb.get('previousVerdict')} 改为 "
           f"{jb.get('appliedVerdict') or chk.get('verdict')}"
           f"({jb.get('overriddenAt', '')})—— {jb.get('overrideReason')}"]
          if (jb := (chk.get("judgedBy") or {})).get("overriddenBy") else []),
        f"- **测试机**: {device.get('profile', '?')} / {device.get('resolution', '?')}"
        f" / dpi {device.get('dpi', '?')}",
        f"- **宿主**: {device.get('host', '?')} / AppID {device.get('appid', '—')}",
        f"- **运行**: {run.get('runId')}"
        + (f"(共 {len(run_ids)} 轮: {', '.join(run_ids)})" if run_ids and len(run_ids) > 1 else ""),
        "",
        "## 期望",
        expectation_text(specs, cid, src, case),
        "",
        "## 实际",
        f"```json\n{json.dumps(chk.get('actual'), ensure_ascii=False, indent=2)}\n```",
        "",
        "## 复现步骤",
    ]
    for i, step in enumerate(repro_steps(specs, case), 1):
        lines.append(f"{i}. {step}")
    lines += ["", "## 证据",
              f"_绑定到判定项 `{src}`"
              + (f",观测步骤 `{chk.get('after')}`" if chk.get("after") else "")
              + f",判定模式 `{chk.get('mode')}`_"]
    shown = shown_evidence if shown_evidence is not None else evidence_paths
    if shown:
        for p in shown:
            missing = not Path(p).exists()
            lines.append(f"- {p}" + ("  ⚠ **文件当前不存在**" if missing else ""))
    else:
        lines.append("- ⚠ **无证据** —— 按规矩这种情形应判 blocked 而非 failed,"
                     "且不应开单。请复查判定流程")
    if net:
        bits = [f"{k}={v}" for k, v in net.items() if v is not None]
        lines += ["", "## 弱网条件", "```json",
                  json.dumps(net, ensure_ascii=False, indent=2), "```",
                  f"({', '.join(bits)})"]
    det = case.get("determinism") or {}
    if det:
        lines += ["", "## 确定性", "```json",
                  json.dumps(det, ensure_ascii=False, indent=2), "```"]
    lines += [
        "",
        "## 备注",
        "期望值来自用例的期望来源;实际值来自执行采集的原始观测值,非模型转述。",
        "",
    ]
    return "\n".join(lines)


def render_report(run: dict, cases: list[dict], bugs: list[str],
                  requirements: dict | None, coverage: dict,
                  anomalies: list[str] | None = None,
                  evidence_missing: list[str] | None = None,
                  flaky: list[str] | None = None,
                  run_ids: list[str] | None = None,
                  gaps: list[dict] | None = None,
                  anomalies_report: str | None = None) -> str:
    anomalies = anomalies or []
    evidence_missing = evidence_missing or []
    flaky = flaky or []
    gaps = gaps or []
    run_ids = run_ids or []      # 空 = 还没跑过,不要编一个假轮次
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    status_counter: Counter = Counter()
    verdict_counter: Counter = Counter()
    for c in cases:
        status_counter[c.get("status", "?")] += 1
        for chk in c.get("checks") or []:
            verdict_counter[chk.get("verdict", "?")] += 1

    total_checks = sum(verdict_counter.values())
    executed = verdict_counter.get("pass", 0) + verdict_counter.get("fail", 0)
    # 探索模式的判定项用 ok/anomaly,天然不计入通过率
    explored = verdict_counter.get("ok", 0) + verdict_counter.get("anomaly", 0)

    src_counter: Counter = Counter()
    for c in cases:
        for chk in c.get("checks") or []:
            if chk.get("kind") == "assert":
                src_counter[c.get("expect_source", "?")] += 1

    lines = [
        f"# 测试报告 {run.get('runId')}",
        "",
        f"- 生成时间: {now}",
        f"- 运行模式: `{run.get('mode') or '(还没有运行结果 —— 只有覆盖信息)'}`",
        (f"- 轮次: {len(run_ids)} 轮({', '.join(run_ids)})" if run_ids
         else "- 轮次: 0 轮(还没有执行过用例:断言失败、证据、复现性都无从谈起)"),
        f"- 用例总数: {len(cases)}",
        "",
        "## 结果",
        "",
        "| 用例状态 | 数量 |",
        "|---|---|",
    ]
    for k in sorted(status_counter):
        lines.append(f"| {k} | {status_counter[k]} |")
    lines += ["", "| 判定结果 | 数量 |", "|---|---|"]
    for k in sorted(verdict_counter):
        lines.append(f"| {k} | {verdict_counter[k]} |")

    lines += [
        "",
        "## 覆盖率(诚实指标)",
        "",
        f"- 需求子句覆盖: {coverage.get('req_covered', '?')}/{coverage.get('req_total', '?')}"
        + (f"(未覆盖 {', '.join(coverage['req_uncovered'])})"
           if coverage.get("req_uncovered") else ""),
        f"- 期望覆盖: {'100%(三道闸门已通过)' if coverage.get('gates_ok') else '见校验输出'}",
        f"- 判定项总数: {total_checks}",
        f"- 其中探索模式(ok/anomaly): {explored} 个 —— **不计入通过率**",
        f"- **实际执行率: {executed}/{total_checks - explored}"
        f" = {(executed / (total_checks - explored) * 100 if total_checks - explored else 0):.0f}%**"
        f"(passed + failed;blocked / skipped / pending_* / 探索项 不计入)",
        "",
        "> 通过率高但执行率低的报告是误导性的。执行率就是"
        "「这套测试实际覆盖了多少」的诚实指标。",
        "",
        "## 缺陷",
        "",
    ]
    if bugs:
        for b in bugs:
            lines.append(f"- {b}")
    else:
        lines.append("(无)")

    lines += ["", "## 稳定性(多轮)", ""]
    if not run_ids:
        lines.append("- 还没有跑过用例 —— 稳定性无从谈起(先有用例、再执行)")
    elif len(run_ids) <= 1:
        lines.append(f"- 只跑了 1 轮({run_ids[0]})—— **复现性只能写「未知」**。"
                     "判稳定/偶现至少跑 2 轮,把多个 `--run` 一起给 `file_bugs.py` 即可自动算。")
    else:
        lines.append(f"- {len(run_ids)} 轮: {', '.join(run_ids)}")
        if flaky:
            lines.append(f"- **多轮结果不一致的判定项 {len(flaky)} 条**(偶现候选,"
                         f"开单时必须如实标注,不要写成「稳定」):")
            for x in flaky:
                lines.append(f"  - {x}")
        else:
            lines.append("- 各判定项在多轮里结果一致(无偶现迹象)")

    lines += ["", "## 探索发现(异常候选,需人工确认)", ""]
    if anomalies:
        lines.append("探索模式的判定项不产出 pass/fail,也**不计入通过率、不开 bug 单**。")
        lines.append("下面是发现的可疑项,由人复核后决定是否升级为缺陷:")
        lines.append("")
        for a in anomalies:
            lines.append(f"- {a}")
    else:
        lines.append("(无)")

    lines += ["", "## ⚠ 证据问题", ""]
    if evidence_missing:
        lines.append("**这些判定项判为 fail,但没有绑定到任何证据,因此没有开单。**")
        lines.append("判定失败却拿不出证据,说明判定本身不成立 —— 这是流程问题,不是产品缺陷。")
        lines.append("处理方式:确认对应步骤是否真的采集了截图/上报,修好证据采集后重跑。")
        lines.append("")
        for m in evidence_missing:
            lines.append(f"- {m}")
    else:
        lines.append("(无 —— 每张 bug 单的证据都已绑定到它自己的判定项)")

    lines += ["", "## 证据缺口(不是「没有异常」,是「无法判断」)", ""]
    if gaps:
        lines.append("用例声明了要采的证据,但**没有任何采集动作或文件**。"
                     "缺口的后果不是「没发现问题」,而是这一类问题**没东西可判**:")
        lines.append("")
        for g in gaps:
            lines.append(f"- `{g.get('caseId')}` / {g.get('evidence')} "
                         f"({g.get('kind')}) — {g.get('note')}")
        lines.append("")
        lines.append("_处理方式:补对应的采集动作(`screenshot` / `logcat` / `perf`),"
                     "或把这类证据从用例声明里去掉 —— 别让它继续挂着。_")
    else:
        lines.append("(无 —— 声明的证据类型都有对应的采集动作)")

    if anomalies_report:
        lines += ["", f"异常扫描(崩溃/白屏黑屏/卡死/性能)单独一张报告: `{anomalies_report}`。"
                      "**该报告里的发现是候选,不开 bug 单**;"
                      "崩溃如需开单,请让用例里有一条对应的 `assert` 判定项。"]

    lines += [
        "",
        "## 期望来源分布(assert 判定项)",
        "",
        "| 来源 | 判定项数 |",
        "|---|---|",
    ]
    for k in sorted(src_counter):
        lines.append(f"| {k} | {src_counter[k]} |")
    lines += [
        f"| 观察行为派生的期望 | 0(合规;非 0 即为校验器应拦截的违规) |",
        "",
        "## 平台限制影响",
        "",
    ]
    nets = [c for c in cases if c.get("network")]
    if nets:
        ios = [c["id"] for c in nets
               if (c.get("device") or {}).get("profile", "").startswith(("iphone", "ipad"))]
        lines.append(f"- 含弱网条件的用例 {len(nets)} 条;其中 iOS {len(ios)} 条。"
                     "**iOS 侧只能靠代理近似限速,精度低于 Android**,"
                     "结论不可跨平台直接套用。")
    manual = [c["id"] for c in cases if c.get("status") == "pending_manual"]
    if manual:
        lines.append(f"- 需人工执行的用例 {len(manual)} 条: {', '.join(manual)}")
    skipped = [c["id"] for c in cases if c.get("status") == "skipped"]
    if skipped:
        lines.append(f"- 因执行器未实现而跳过的用例 {len(skipped)} 条: {', '.join(skipped)}")
    lines.append("- 帧级时序、多指手势、真实支付无法自动化(见 references/8-platform-limits.md)")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="testflow 提 bug 与报告")
    ap.add_argument("--run", type=Path, action="append",
                    help="run-*.json;可重复给多轮,用来自动算复现性(N/M)")
    ap.add_argument("--report-only", action="store_true",
                    help="还没有运行结果时也要出报告:只出 report.md(覆盖到哪/缺口在哪),"
                         "不扫断言、不开单 —— 早期阶段用它,别让'没跑'挡住可见性")
    ap.add_argument("--out", type=Path, required=True, help="bug 输出目录 05-bugs/")
    ap.add_argument("--specs", type=Path, help="自然语言用例,用于取期望原文与复现步骤")
    ap.add_argument("--cases", type=Path, help="派生用例目录,用于补充用例字段")
    ap.add_argument("--requirements", type=Path, help="用于计算需求覆盖率")
    ap.add_argument("--report", type=Path, help="report.md 输出路径;默认写到 --out 的上级")
    ap.add_argument("--bundle", action="store_true",
                    help="把每张 bug 单的证据拷进 <out>/BUG-00N.evidence/(含 manifest "
                         "与 sha256),单子发给研发时不至于断链")
    args = ap.parse_args()

    args.run = args.run or []
    if not args.run and not args.report_only:
        print("错误: 需要 --run(或用 --report-only 只出报告)", file=sys.stderr)
        return 2
    for rp in args.run:
        if not rp.exists():
            print(f"错误: 找不到 {rp}", file=sys.stderr)
            return 2

    all_runs: list[dict] = []
    for rp in args.run:
        try:
            all_runs.append(json.loads(rp.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            print(f"错误: {rp} 解析失败: {exc}", file=sys.stderr)
            return 2

    # 没有运行结果时(--report-only)也要能出报告:run 用空壳顶上,
    # 下面的开单逻辑天然不会触发(没有断言失败可开)。
    run = all_runs[0] if all_runs else {"runId": "(还没有运行结果)", "cases": []}
    run_ids = [r.get("runId") or f"run-{i}" for i, r in enumerate(all_runs)]
    history = verdict_history(all_runs)
    specs = collect_specs(args.specs) if args.specs else {}

    case_index: dict[str, dict] = {}
    if args.cases and args.cases.exists():
        files = sorted(args.cases.rglob("*.json")) if args.cases.is_dir() else [args.cases]
        for f in files:
            try:
                c = json.loads(f.read_text(encoding="utf-8"))
                case_index[c.get("id")] = c
            except json.JSONDecodeError:
                continue

    run_dir = args.run[0].parent if args.run else args.out.parent
    # 异常扫描报告与 run 结果同级(05-bugs/ 的上级)
    anomalies_path = args.out.parent / "anomalies.md"
    anomalies_report = str(anomalies_path) if anomalies_path.exists() else None

    args.out.mkdir(parents=True, exist_ok=True)
    bug_files: list[str] = []
    anomaly_list: list[str] = []
    evidence_missing: list[str] = []
    flaky: list[str] = []
    idx = 0
    skipped_non_assert: Counter = Counter()

    # 跨轮统计:同一 (用例, 判定项) 在多轮里出现过哪些 verdict。
    # 只跑一轮时复现性只能写"未知" —— 不许拿单轮结果冒充"稳定"。
    for key, row in sorted(history.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "")):
        seen = [v for v in row if v is not None]
        if len(set(seen)) > 1 and len(seen) > 1:
            flaky.append(f"{key[0]} / {key[1]} — 多轮结果不一致: "
                         f"{' → '.join(str(v) for v in seen)}")

    # 判定失败过(任意一轮)的判定项 -> 开单;证据与 actual 取首次失败那一轮
    fail_ref: dict[tuple, tuple[dict, dict, dict]] = {}
    for i, one in enumerate(all_runs):
        for case_run in one.get("cases") or []:
            for chk in case_run.get("checks") or []:
                key = (case_run.get("id"), chk.get("source"))
                if chk.get("verdict") == "fail" and key not in fail_ref:
                    fail_ref[key] = (case_run, chk, one)
    for key, (rc, chk, failed_run) in sorted(
            fail_ref.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "")):
        cid = rc.get("id", "?")
        case = case_index.get(cid) or {
            "id": cid,
            "title": rc.get("title"),
            "priority": rc.get("priority"),
            "expect_source": rc.get("expect_source"),
            "req_refs": rc.get("req_refs"),
            "device": rc.get("device"),
            "network": rc.get("network"),
            "determinism": rc.get("determinism"),
            "checks": rc.get("checks") or [],
        }
        if chk.get("kind") != "assert":
            # 非 assert 的失败不开单 —— 这类判定不该产出 fail
            skipped_non_assert[f"non_assert_fail:{chk.get('kind')}"] += 1
            continue

        # 证据必须已绑定到**这条判定项**上。绑定不到就不开单:
        # "判定失败但没有证据"说明判定本身不成立,此时开一张证据对不上的
        # 单子比不开更糟 —— 它会让证据链看起来完整。
        ev = chk.get("evidence") or []
        if not ev:
            evidence_missing.append(
                f"{cid} / {chk.get('source')} (mode={chk.get('mode')}, "
                f"after={chk.get('after')}) — 判定为 fail 但未绑定到任何证据")
            skipped_non_assert["fail_but_no_evidence"] += 1
            continue

        idx += 1
        repro, _ = reproducibility(history.get(key, []))
        shown_ev = ev
        if args.bundle:
            shown_ev, manifest = bundle_evidence(
                idx, ev, args.out, f"BUG-{idx:03d}.evidence")
            if manifest["missing"]:
                evidence_missing.append(
                    f"{cid} / {chk.get('source')} — 打包时找不到证据文件: "
                    f"{manifest['missing']}")
        body = render_bug(idx, case, chk, failed_run, specs, ev,
                          repro=repro, run_ids=run_ids, shown_evidence=shown_ev)
        path = args.out / f"BUG-{idx:03d}.md"
        path.write_text(body, encoding="utf-8")
        bug_files.append(f"BUG-{idx:03d} {case.get('title', '')} "
                         f"({chk.get('source')}, {case.get('priority')}, {repro})")

    # 非 fail 的判定结果(能力缺口/待办):按首轮统计,与 bug 判定分开
    for case_run in run.get("cases") or []:
        for chk in case_run.get("checks") or []:
            verdict = chk.get("verdict")
            if verdict == "fail":
                continue
            if verdict == "anomaly":
                anomaly_list.append(
                    f"{case_run.get('id')} / {chk.get('source')} — {case_run.get('title', '')}")
            skipped_non_assert[verdict] += 1

    # ---- 覆盖率
    coverage: dict = {"req_total": "—", "req_covered": "—", "req_uncovered": [],
                      "gates_ok": True}
    if args.requirements and args.requirements.exists():
        try:
            reqdata = json.loads(args.requirements.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            reqdata = None
        if reqdata:
            clause_ids = {c.get("id") for c in reqdata.get("clauses") or [] if c.get("id")}
            used = set()
            for c in list(case_index.values()) + [rc for rc in run.get("cases") or []]:
                used |= set(c.get("req_refs") or [])
            coverage["req_total"] = len(clause_ids)
            coverage["req_covered"] = len(clause_ids & used)
            coverage["req_uncovered"] = sorted(clause_ids - used)

    # ---- 证据缺口(采集动作缺失):把每个用例的 evidenceGaps 汇总进报告
    gaps: list[dict] = []
    for case_run in run.get("cases") or []:
        for g in case_run.get("evidenceGaps") or []:
            gaps.append({"caseId": case_run.get("id"), **g})

    report = render_report(run, run.get("cases") or [], bug_files,
                           None, coverage, anomaly_list, evidence_missing,
                           flaky=flaky, run_ids=run_ids, gaps=gaps,
                           anomalies_report=anomalies_report)
    report_path = args.report or (args.out.parent / "report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    print(f"缺陷单 {len(bug_files)} 张 → {args.out}")
    for b in bug_files:
        print(f"  · {b}")
    if anomaly_list:
        print()
        print(f"探索发现 {len(anomaly_list)} 项(异常候选,由人复核,不开 bug 单):")
        for a in anomaly_list:
            print(f"  ? {a}")
    if evidence_missing:
        print()
        print(f"⚠ 证据问题 {len(evidence_missing)} 项 —— 判定为 fail 但无证据,已拒绝开单:")
        for m in evidence_missing:
            print(f"  ! {m}")
    if skipped_non_assert:
        print()
        print("未开单的判定结果(按设计不开 bug):")
        for k in sorted(skipped_non_assert):
            print(f"  {k:24s} {skipped_non_assert[k]}")
        print("  → blocked / skipped / pending_* 反映能力缺口或待办,不是产品缺陷")
    print()
    print(f"报告已写出: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
