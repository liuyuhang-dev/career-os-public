#!/usr/bin/env python3
"""testflow 复核单生成器 / 复核决定写回。

人只做复核与监督,所以复核必须便宜且可追溯。这份工具把
「需求原文 -> 用例 -> 判定项」三者并排摆出,并标注每条期望的来源,
让人一眼看出三件事:
  1. 哪条需求没被覆盖
  2. 哪条期望的来源不合法(把观察到的行为当成期望)
  3. 哪条期望无法判定

纯标准库,零依赖。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_cases import collect_case_files, collect_specs  # noqa: E402

SOURCE_LABEL = {
    "requirement": "需求(硬要求)",
    "invariant": "通用不变量(软要求)",
}


def load_cases(target: Path) -> list[tuple[Path, dict]]:
    out = []
    for f in collect_case_files(target):
        try:
            out.append((f, json.loads(f.read_text(encoding="utf-8"))))
        except json.JSONDecodeError as exc:
            print(f"警告: 跳过 {f}: {exc}", file=sys.stderr)
    return out


def describe_check(chk: dict) -> str:
    kind = chk.get("kind")
    if kind == "assert":
        mode = chk.get("mode")
        if mode == "vision-closed":
            return f"assert/视觉封闭 · {chk.get('question')}"
        if mode == "state-report":
            return (f"assert/状态上报 · {chk.get('path')} "
                    f"{chk.get('op')} {chk.get('value')}")
        if mode == "replay":
            return "assert/回放比对"
        return f"assert/{mode}"
    if kind == "judgment":
        return f"judgment · {chk.get('question')}"
    if kind == "manual":
        return f"manual · {chk.get('reason')}"
    return str(kind)


def build_sheet(requirements: dict | None, specs: dict, cases: list[tuple[Path, dict]],
                run_hint: str) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    by_state: dict[str, list[tuple[Path, dict]]] = {"draft": [], "approved": [], "rejected": []}
    for path, case in cases:
        state = ((case.get("review") or {}).get("state") or "draft")
        by_state.setdefault(state, []).append((path, case))

    lines: list[str] = []
    add = lines.append

    add("# 复核单")
    add("")
    add(f"- 生成时间: {now}")
    add(f"- 用例总数: {len(cases)}"
        f"(待复核 {len(by_state.get('draft', []))} / "
        f"已批准 {len(by_state.get('approved', []))} / "
        f"已驳回 {len(by_state.get('rejected', []))})")
    if requirements:
        add(f"- 需求子句: {len(requirements.get('clauses') or [])} 条")
    else:
        add("- 需求子句: 未提供需求文档(纯不变量模式)")
    add("")

    # ---------------- 待复核
    add("## 一、待复核用例")
    add("")
    pending = by_state.get("draft", [])
    if not pending:
        add("_(无待复核用例)_")
        add("")
    for _path, case in pending:
        cid = case.get("id", "?")
        es = case.get("expect_source", "?")
        add(f"### {cid}  {case.get('title', '')}")
        add("")
        add(f"- 状态: **draft**  来源: **{SOURCE_LABEL.get(es, es)}**")
        refs = case.get("req_refs") or []
        add(f"- 关联需求: {', '.join(refs) if refs else '—(不变量用例)'}")
        add(f"- 类型: {case.get('type')}  优先级: {case.get('priority')}  "
            f"判定模式: {case.get('judge')}")
        net = case.get("network") or {}
        if net:
            bits = [f"{k}={v}" for k, v in net.items() if v is not None]
            add(f"- 弱网: {', '.join(bits)}")
        det = case.get("determinism") or {}
        if det:
            bits = [f"{k}={v}" for k, v in det.items()]
            add(f"- 确定性: {', '.join(bits)}")
        add("")

        spec = specs.get(cid)
        items = spec["items"] if spec else {}
        add("| 编号 | 期望原文 | 判定项 |")
        add("|---|---|---|")
        checks_by_src: dict[str, list[dict]] = {}
        for chk in case.get("checks") or []:
            checks_by_src.setdefault(chk.get("source", "?"), []).append(chk)
        for src in sorted(checks_by_src, key=lambda s: (len(s), s)):
            text = items.get(src, "⚠ 在自然语言用例里找不到该编号")
            descs = " / ".join(describe_check(c) for c in checks_by_src[src])
            add(f"| {src} | {text} | {descs} |")
        if not spec:
            add("")
            add(f"> ⚠ 在 specs 里找不到 ID 为 {cid} 的自然语言用例,无法显示期望原文")
        add("")
        add("```bash")
        add(f"# 批准")
        add(f"python3 {run_hint}/review_sheet.py --cases <cases目录> "
            f"--approve {cid} --by <你的名字>")
        add(f"# 驳回")
        add(f"python3 {run_hint}/review_sheet.py --cases <cases目录> "
            f"--reject {cid} --by <你的名字> --note \"驳回原因\"")
        add("```")
        add("")

    # ---------------- 需求覆盖对照
    add("## 二、需求覆盖对照")
    add("")
    if not requirements:
        add("_(未提供需求文档,此项跳过)_")
        add("")
    else:
        ref_to_cases: dict[str, list[str]] = {}
        for _path, case in cases:
            for r in case.get("req_refs") or []:
                ref_to_cases.setdefault(r, []).append(case.get("id", "?"))
        add("| 需求子句 | 原文 | 对应用例 |")
        add("|---|---|---|")
        for clause in requirements.get("clauses") or []:
            cid = clause.get("id", "?")
            text = (clause.get("text") or "").replace("|", "\\|")
            mapped = ref_to_cases.get(cid)
            if mapped:
                add(f"| {cid} | {text} | {', '.join(mapped)} |")
            else:
                add(f"| {cid} | {text} | ⚠ **无对应用例** |")
        add("")

    # ---------------- 需人工关注
    add("## 三、需要人工重点关注")
    add("")
    concerns: list[str] = []

    for _path, case in cases:
        cid = case.get("id", "?")
        # 来源为不变量:软要求,需人确认确实应成立
        if case.get("expect_source") == "invariant":
            concerns.append(f"- `{cid}`: 期望来源于**通用不变量**而非需求。"
                            f"请确认这些性质确实应当在你们的游戏里成立")
        # 具体数值出现在不变量用例里:可疑
        for chk in case.get("checks") or []:
            if chk.get("kind") == "assert" and chk.get("mode") == "state-report":
                if chk.get("op") == "eq" and isinstance(chk.get("value"), (int, float)):
                    concerns.append(
                        f"- `{cid}` {chk.get('source')}: 精确等值断言 "
                        f"`{chk.get('path')} == {chk.get('value')}`。"
                        f"**请确认这个数值来自需求文本,而不是你看屏幕看到的**"
                    )
        if case.get("priority") in ("P0", "P1") and case.get("judge") == "explore":
            concerns.append(f"- `{cid}`: P0/P1 用例却用 explore 模式(不判通过与否)。"
                            f"探索模式不适合高优先级验收项")
        if not any(c.get("kind") == "assert" for c in (case.get("checks") or [])):
            concerns.append(f"- `{cid}`: 没有任何 assert 判定项,"
                            f"这条用例不会产生自动化结论")

    if concerns:
        # 去重保序
        seen = set()
        for c in concerns:
            if c not in seen:
                seen.add(c)
                add(c)
    else:
        add("_(无)_")
    add("")

    add("## 四、复核要点")
    add("")
    add("1. **有没有把现有行为写成期望** —— 看到具体数值先自问:这个数是需求里写的,"
        "还是我看屏幕看到的?后者无效")
    add("2. **有没有不可判定的期望** —— 出现「正常」「合理」「友好」这类词就是问题")
    add("3. **有没有漏覆盖的需求** —— 见第二节的 `⚠ 无对应用例`")
    add("4. **不变量用例的适用性** —— 见第三节")
    add("")

    return "\n".join(lines)


def apply_decision(cases_dir: Path, case_id: str, decision: str,
                   by: str, note: str | None) -> int:
    hits = 0
    for path, case in load_cases(cases_dir):
        if case.get("id") != case_id:
            continue
        review = case.get("review") or {}
        review["state"] = decision
        review["by"] = by
        review["at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        review["note"] = note
        case["review"] = review
        path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        print(f"{case_id}: review.state -> {decision} (by {by})  [{path.name}]")
        hits += 1
    if not hits:
        print(f"错误: 找不到用例 {case_id}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="testflow 复核单")
    ap.add_argument("--requirements", type=Path)
    ap.add_argument("--specs", type=Path)
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--out", type=Path, help="写出到文件;不给则打印到 stdout")
    ap.add_argument("--approve", metavar="CASE_ID")
    ap.add_argument("--reject", metavar="CASE_ID")
    ap.add_argument("--by", help="复核人")
    ap.add_argument("--note", help="复核备注 / 驳回原因")
    ap.add_argument("--script-dir", default="$(dirname \"$0\")",
                    help="复核单里提示命令用的脚本目录")
    args = ap.parse_args()

    if not args.cases.exists():
        print(f"错误: --cases 不存在: {args.cases}", file=sys.stderr)
        return 2

    if args.approve or args.reject:
        if not args.by:
            print("错误: 复核必须署名(--by)", file=sys.stderr)
            return 2
        if args.reject and not args.note:
            print("错误: 驳回必须给出原因(--note)", file=sys.stderr)
            return 2
        decision = "approved" if args.approve else "rejected"
        case_id = args.approve or args.reject
        return apply_decision(args.cases, case_id, decision, args.by, args.note)

    specs = collect_specs(args.specs) if args.specs else {}
    requirements = None
    if args.requirements:
        try:
            requirements = json.loads(args.requirements.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"错误: 需求文件 JSON 解析失败: {exc}", file=sys.stderr)
            return 2

    sheet = build_sheet(requirements, specs, load_cases(args.cases), args.script_dir)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(sheet, encoding="utf-8")
        print(f"复核单已写出: {args.out}")
    else:
        print(sheet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
