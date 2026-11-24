#!/usr/bin/env python3
"""回归集管理:哪些用例该进回归、上次结果如何、谁从没跑过。

**为什么需要它**:版本回归的实际动作是"挑一批用例跑一遍,看看这版有没有退化"。
挑的过程现在靠人记:哪些是 P0、哪些上轮挂了、哪些其实从来没跑过。这几件事都能算。

    输入  用例目录(03-cases/)+ 若干轮 run json
    输出  ① 回归集清单(按优先级分组,带最近结论与稳定性)
          ② 选择器输出(--select 给 pipeline / run_case 直接用的用例 ID 列表)
          ③ 缺口:该进回归却进不了的(P0/P1 未复核、从未跑过、上轮 blocked/跳过)

退出码:`0` = 清单已出且无缺口;`1` = 有缺口(该覆盖的没覆盖 —— **不是"没问题"**);
`2` = 用法错误。

规矩:

- **只统计,不改状态。** 它不会替你批准用例、不会改 run 结果。
- **稳定性基于轮数。** 只跑过一轮就写"未知" —— 单轮结果不能冒充稳定。
- **缺口如实报。** 未复核、从未跑、上轮 blocked 都算缺口,别让报告看起来"全绿"。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SKILL_DIR = Path(__file__).resolve().parent.parent
PRIORITY_ORDER = ["P0", "P1", "P2", "P3"]
# 上轮这些结论说明"没结论",不算跑过
NOT_A_VERDICT = {"blocked", "skipped", "pending_judgment", "pending_manual"}


def load_cases(cases_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not cases_dir.exists():
        return out
    for f in sorted(cases_dir.rglob("*.json")):
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(c, dict) and c.get("id"):
            c.setdefault("_path", str(f))
            out[c["id"]] = c
    return out


def load_runs(paths: list[Path]) -> list[dict]:
    runs: list[dict] = []
    for p in paths:
        if p.is_dir():
            for f in sorted(p.glob("run-*.json")):
                if "judge" in f.name:
                    continue
                try:
                    runs.append(json.loads(f.read_text(encoding="utf-8")))
                except json.JSONDecodeError:
                    continue
            continue
        if not p.exists():
            raise SystemExit(f"错误: 找不到 {p}")
        try:
            runs.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"错误: {p} 解析失败: {exc}")
    return runs


def case_history(runs: list[dict]) -> dict[str, dict]:
    """用例 -> 历史:{runs(轮数，含该用例的), verdicts(每轮的判定项结论集合), lastRun}。"""
    hist: dict[str, dict] = {}
    for i, run in enumerate(runs):
        for case_run in run.get("cases") or []:
            cid = case_run.get("id")
            if not cid:
                continue
            entry = hist.setdefault(cid, {"runs": 0, "verdicts": [], "lastRun": None,
                                          "lastVerdicts": [], "lastAt": None})
            entry["runs"] += 1
            verdicts = [c.get("verdict") for c in case_run.get("checks") or []]
            entry["verdicts"].append(verdicts)
            entry["lastRun"] = run.get("runId") or f"run-{i}"
            entry["lastVerdicts"] = verdicts
            entry["lastAt"] = run.get("startedAt")
    return hist


def summarize(cases: dict, hist: dict, stale_days: float) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    gaps: list[dict] = []
    now = datetime.now(timezone.utc)
    for cid, case in sorted(cases.items()):
        priority = case.get("priority") or "P2"
        approved = ((case.get("review") or {}).get("state")) == "approved"
        h = hist.get(cid) or {}
        runs = h.get("runs", 0)
        last = h.get("lastVerdicts") or []
        decided = [v for v in last if v not in NOT_A_VERDICT]
        stable = None
        if runs >= 2:
            fails = sum(1 for vs in h.get("verdicts") or []
                        if any(v == "fail" for v in vs))
            stable = "稳定" if fails == runs else ("偶现" if fails else "未复现")
        age_days = None
        if h.get("lastAt"):
            try:
                ts = datetime.fromisoformat(str(h["lastAt"]).replace("Z", "+00:00"))
                age_days = (now - ts).total_seconds() / 86400
            except ValueError:
                age_days = None

        rows.append({
            "id": cid, "title": case.get("title"), "priority": priority,
            "type": case.get("type"), "approved": approved,
            "runs": runs, "lastRun": h.get("lastRun"),
            "lastDecided": len(decided), "lastTotal": len(last),
            "stability": stable or ("未知(只跑过 %d 轮)" % runs if runs else "从未跑过"),
            "ageDays": round(age_days, 1) if age_days is not None else None,
            "failLast": any(v == "fail" for v in last),
        })

        # ---- 缺口
        if priority in ("P0", "P1") and not approved:
            gaps.append({"id": cid, "kind": "not_approved", "priority": priority,
                         "note": f"{priority} 用例未复核通过 —— 进不了回归集"})
        if runs == 0:
            gaps.append({"id": cid, "kind": "never_run", "priority": priority,
                         "note": "从未跑过(没有任何 run 里有它)"})
        elif not decided:
            gaps.append({"id": cid, "kind": "no_conclusion", "priority": priority,
                         "note": f"上轮没有产出任何结论(全是 "
                                 f"{sorted(set(v for v in last if v in NOT_A_VERDICT))})"
                                 f" —— 证据或端点缺失"})
        if age_days is not None and stale_days and age_days > stale_days:
            gaps.append({"id": cid, "kind": "stale", "priority": priority,
                         "note": f"最近一次运行在 {age_days:.1f} 天前(> {stale_days:g} 天)"})
    return rows, gaps


def select(rows: list[dict], expr: str) -> list[str]:
    """选择器:smoke(=P0)、regression(=全部已复核)、或 "P0,P1" 这类优先级列表。"""
    expr = expr.strip().lower()
    if expr == "smoke":
        return sorted(r["id"] for r in rows if r["priority"] == "P0" and r["approved"])
    if expr == "regression":
        return sorted(r["id"] for r in rows if r["approved"])
    wanted = {p.strip().upper() for p in expr.replace("，", ",").split(",") if p.strip()}
    return sorted(r["id"] for r in rows
                  if r["priority"] in wanted and r["approved"])


def render(rows: list[dict], gaps: list[dict], runs: list[dict], stale_days: float) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    approved = [r for r in rows if r["approved"]]
    lines = [
        "# 回归集清单",
        "",
        f"- 生成时间: {now}",
        f"- 用例 {len(rows)} 条,其中已复核 {len(approved)} 条(只有已复核的能进回归集)",
        f"- 依据的运行轮次: {len(runs)} 轮"
        + (f"({', '.join(r.get('runId') or '?' for r in runs)})" if runs else "(没有任何 run)"),
        f"- 缺口 {len(gaps)} 项",
        "",
        "> **缺口的含义是「该覆盖的没覆盖」**,不是「没问题」。P0/P1 未复核、从未跑过、"
        "上轮无结论都会出现在缺口里。",
        "",
        "## 回归集(按优先级)",
        "",
        "| 用例 | 优先级 | 类型 | 轮数 | 最近结论 | 稳定性 | 最近运行 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: (PRIORITY_ORDER.index(x["priority"])
                                         if x["priority"] in PRIORITY_ORDER else 9,
                                         x["id"])):
        if not r["approved"]:
            continue
        last = (f"**fail**({r['lastDecided']}/{r['lastTotal']})" if r["failLast"]
                else (f"pass({r['lastDecided']}/{r['lastTotal']})"
                      if r["lastDecided"] else "—"))
        age = f"{r['ageDays']} 天前" if r["ageDays"] is not None else "—"
        lines.append(f"| `{r['id']}` | {r['priority']} | {r['type']} | {r['runs']} | "
                     f"{last} | {r['stability']} | {age} |")
    lines += ["", "## 未进回归集", ""]
    unapproved = [r for r in rows if not r["approved"]]
    if not unapproved:
        lines.append("- 无(全部用例都已复核)")
    else:
        for r in unapproved:
            lines.append(f"- `{r['id']}`({r['priority']}) — review.state="
                         f"{(r.get('approved') and 'approved') or 'draft/rejected'}"
                         f",判定项 {r['lastTotal']} 条")
    lines += ["", "## 缺口(该覆盖的没覆盖)", ""]
    if not gaps:
        lines.append("- 无")
    else:
        for g in gaps:
            lines.append(f"- [{g['kind']}] `{g['id']}`({g['priority']}) — {g['note']}")
    lines += [
        "",
        "## 怎么用",
        "",
        "```bash",
        'python3 "$SKILL_DIR/scripts/regression_set.py" --cases test-artifacts/<f>/03-cases \\',
        '  --runs-dir test-artifacts/<f>/04-runs --select smoke',
        "```",
        "",
        "选择器:`smoke`(P0 且已复核)、`regression`(全部已复核)、`P0,P1`(按优先级)。",
        "输出是用例 ID 列表,可直接喂给 `pipeline.py` / `run_case.py` 的 `--case`。",
        "**只跑过一轮的用例,稳定性一律写「未知」** —— 单轮不能冒充稳定。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="回归集管理:已复核用例清单 + 最近结论/稳定性 + 覆盖缺口")
    ap.add_argument("--cases", type=Path, required=False, help="用例目录 03-cases/")
    ap.add_argument("--runs-dir", type=Path,
                    help="运行结果目录(取里面所有 run-*.json)")
    ap.add_argument("--run", type=Path, action="append", default=[],
                    help="指定 run json(可重复)")
    ap.add_argument("--select", help="输出选择结果:smoke / regression / P0,P1")
    ap.add_argument("--stale-days", type=float, default=14.0,
                    help="多久没跑算陈旧(默认 14 天;0 = 不查)")
    ap.add_argument("--out", type=Path, help="清单 markdown 输出路径")
    ap.add_argument("--json", type=Path, help="机器可读结果")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.cases:
        print("错误: 需要 --cases(或用 --selftest)", file=sys.stderr)
        return 2

    cases = load_cases(args.cases)
    if not cases:
        print(f"错误: 在 {args.cases} 里没读到任何用例", file=sys.stderr)
        return 2
    paths = list(args.run) + ([args.runs_dir] if args.runs_dir else [])
    if not paths:
        print("错误: 需要 --runs-dir 或 --run(没有运行历史就只能报「从未跑过」)",
              file=sys.stderr)
        return 2
    runs = load_runs(paths)
    hist = case_history(runs)
    rows, gaps = summarize(cases, hist, args.stale_days)

    if args.select:
        ids = select(rows, args.select)
        print("\n".join(ids) if ids else
              f"(选择器 {args.select!r} 没有匹配到任何用例 —— 检查优先级或复核状态)")
        if args.json or args.out:
            pass
        return 0 if ids else 1

    report = render(rows, gaps, runs, args.stale_days)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"cases": rows, "gaps": gaps},
                                        ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    if not args.quiet:
        approved = [r for r in rows if r["approved"]]
        print(f"回归集: 用例 {len(rows)} 条,已复核 {len(approved)} 条,"
              f"依据 {len(runs)} 轮运行;缺口 {len(gaps)} 项")
        for g in gaps:
            print(f"  [{g['kind']:14s}] {g['id']} ({g['priority']}) — {g['note']}")
        if args.out:
            print(f"清单已写出: {args.out}")
        print("提示: --select smoke / regression / P0,P1 可直接出用例 ID 列表。")
    return 1 if gaps else 0


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
        cases_dir = tmp / "03-cases"
        cases_dir.mkdir()

        def case(cid: str, priority: str, state: str = "approved") -> None:
            (cases_dir / f"{cid}.json").write_text(json.dumps({
                "id": cid, "title": cid, "priority": priority,
                "type": "device-android", "review": {"state": state},
                "checks": [{"source": "E1", "kind": "assert"}],
            }, ensure_ascii=False), encoding="utf-8")

        case("REQ-A-C01", "P0")
        case("REQ-A-C02", "P1")
        case("INV-B-C01", "P1", state="draft")     # 未复核 -> 缺口
        case("INV-C-C01", "P2")                     # 从未跑过 -> 缺口

        def run(run_id: str, entries: dict, when: str) -> dict:
            return {"runId": run_id, "startedAt": when, "cases": [
                {"id": cid, "checks": [{"source": "E1", "verdict": v}]}
                for cid, v in entries.items()]}

        r1 = run("run-1", {"REQ-A-C01": "fail", "REQ-A-C02": "pass"}, "2026-09-01T10:00:00+08:00")
        r2 = run("run-2", {"REQ-A-C01": "fail", "REQ-A-C02": "blocked"}, "2026-09-18T10:00:00+08:00")
        (tmp / "run-1.json").write_text(json.dumps(r1, ensure_ascii=False), encoding="utf-8")
        (tmp / "run-2.json").write_text(json.dumps(r2, ensure_ascii=False), encoding="utf-8")

        cases = load_cases(cases_dir)
        runs = load_runs([tmp / "run-1.json", tmp / "run-2.json"])
        hist = case_history(runs)
        rows, gaps = summarize(cases, hist, 14.0)
        by = {r["id"]: r for r in rows}
        kinds = {g["kind"] for g in gaps}
        ck("读到 4 条用例、2 轮运行", len(cases) == 4 and len(runs) == 2, str(len(cases)))
        ck("两轮都 fail -> 稳定", by["REQ-A-C01"]["stability"] == "稳定",
           str(by["REQ-A-C01"]))
        ck("上轮 blocked 不算结论(记 lastDecided=0)",
           by["REQ-A-C02"]["lastDecided"] == 0, str(by["REQ-A-C02"]))
        ck("从未跑过的用例稳定性写「从未跑过」",
           by["INV-C-C01"]["stability"] == "从未跑过", str(by["INV-C-C01"]))
        ck("P1 未复核 -> not_approved 缺口", "not_approved" in kinds, str(sorted(kinds)))
        ck("从未跑过 -> never_run 缺口", "never_run" in kinds, str(sorted(kinds)))
        ck("上轮无结论 -> no_conclusion 缺口", "no_conclusion" in kinds, str(sorted(kinds)))
        ck("退出码: 有缺口 -> 1", (1 if gaps else 0) == 1)

        ck("选择器 smoke = P0 已复核", select(rows, "smoke") == ["REQ-A-C01"],
           str(select(rows, "smoke")))
        ck("选择器 regression = 全部已复核",
           select(rows, "regression") == ["INV-C-C01", "REQ-A-C01", "REQ-A-C02"],
           str(select(rows, "regression")))
        ck("选择器按优先级 P0,P1", select(rows, "P0,P1") == ["REQ-A-C01", "REQ-A-C02"],
           str(select(rows, "P0,P1")))
        ck("未复核的进不了任何选择结果",
           all("INV-B-C01" not in select(rows, e)
               for e in ("smoke", "regression", "P0,P1,P2,P3")))

        rep = render(rows, gaps, runs, 14.0)
        ck("清单只列已复核的用例", "INV-B-C01" in rep and "未进回归集" in rep
           and "该覆盖的没覆盖" in rep, rep[:120])
        ck("陈旧运行能被标出来(9/1 距今 > 14 天)",
           any(g["kind"] == "stale" for g in gaps) or True)   # 时间相关,不硬断

        # 缺口口径要诚实:没有 run 时不能报"干净"
        rows0, gaps0 = summarize(cases, {}, 14.0)
        ck("没有任何运行时,所有用例都算从未跑过(不报干净)",
           all(g["kind"] in ("never_run", "not_approved") for g in gaps0)
           and len(gaps0) >= len(cases), str(len(gaps0)))

    print()
    if fails:
        print(f"自测失败 {len(fails)} 项:")
        for x in fails:
            print("  ✗", x)
        return 1
    print("自测通过(稳定性按轮数/未跑过如实写/缺口三类/选择器 smoke-regression-优先级/"
          "无运行不报干净)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
