#!/usr/bin/env python3
"""把团队既有的测试用例表(CSV)导入 testflow:自然语言用例 + 派生用例骨架。

为什么要有这一步:测试同学手里已经有的资产是**用例表**(Excel/WPS/CSV),
不是 `specs/*.md`。让工具把表读进来,他们就不用为了自动化再手抄一遍;而且转换是
机械的、可核对的:

  · 前置条件 / 执行步骤 / 预期结果 的编号行(`1.` `2.` …)拆成 `PRE-N` / `S-N` / `E-N`
  · `测试方法` 一列映射到判定通道(type / judge / 证据)—— 不让"手动测试"一列糊住所有通道
  · 每条 `E-N` 都生成一条判定项占位(最难验的那条不会被悄悄漏掉)
  · 备注里的 `需求 M-05` / `关注点 C44` 被解析成 `req_refs` 与 `concerns`,
    关注点台账才能机械核对"我提的每一条都有用例在管"

产出仍然是 **draft 骨架**:断言值、region、坐标这些必须由人/AI 补,闸门会拦住它进入执行阶段。

```bash
python3 "$SKILL_DIR/scripts/import_cases.py" --csv 用例表.csv \\
  --out-specs specs/ --out-cases 03-cases/ \\
  --requirements 01-requirements.json --feature 合成系统
```

映射表(测试方法 -> 通道):

| 用例表里的写法 | 类型 | 判定模式 | 判定项 | 证据 |
| --- | --- | --- | --- | --- |
| 手动测试 | manual | (inert) | manual(必须写 reason) | 截图/录屏 |
| 视觉判定 | device-android | vision-closed | judgment(要 region + 是/否) | screenshot |
| 打点测试 | device-android | state-report | judgment(要 path/op/value) | logcat + state_report |
| 配置检查 | manual | (inert) | manual(check_config 工具输出即证据) | — |
| 确定性信号 | device-android | vision-closed | judgment(崩溃/白屏/卡死看扫描) | logcat + perf + screenshot |

判据强度对应到关注点登记册的「判定强度」:打点=代码判定、视觉=视觉判定、
配置=工具扫描、确定性=工具扫描、手动=人工。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scaffold_cases import DEFAULT_DEVICE, build_case  # noqa: E402
from validate_cases import CASE_ID_PREFIXES  # noqa: E402

# 已经是带前缀的正式 ID 就原样保留,不要再按模块重编。前缀集合只认 validate_cases 那一处。
KNOWN_ID_RE = re.compile(rf"^({'|'.join(CASE_ID_PREFIXES)})-")

SKILL_DIR = Path(__file__).resolve().parent.parent

# 表头别名:各家用例表的列名不完全一样,但语义是固定的
COLUMN_ALIASES = {
    "用例编号": ["用例编号", "编号", "用例ID", "用例id", "ID", "id", "CaseID"],
    "模块": ["模块", "所属模块", "功能模块", "功能点", "父节点"],
    "测试项": ["测试项", "用例标题", "标题", "测试点", "用例名称"],
    "前置条件": ["前置条件", "预置条件", "前置", "Preconditions"],
    "执行步骤": ["执行步骤", "操作步骤", "步骤", "Steps"],
    "预期结果": ["预期结果", "期望结果", "预期", "Expected"],
    "测试方法": ["测试方法", "测试方式", "测试类型", "方法", "Method"],
    "优先级": ["优先级", "等级", "Priority"],
    "用例备注": ["用例备注", "备注", "说明", "Note"],
}
NUMBERED_RE = re.compile(r"^\s*(?:[-*·]|\d+\s*[.、)）]|\d+\))\s*(.+?)\s*$")
# 用例表里写「故障注入:kill_after_step」就生成一条真的 fault 动作。
# 为什么较真:重复提交/幂等这类缺陷,真实场景来自响应丢失重试、进程被杀重启、
# 回调重投;用"连续点 N 次"模拟是伪覆盖(validate_cases 会报 PSEUDO_DUPLICATE_COVERAGE)。
FAULT_RE = re.compile(r"故障注入\s*[:：]\s*([a-z_]+)")
FAULT_DEFAULTS: dict[str, dict] = {
    "drop_response": {"window_ms": 5000},
    "delay_response": {"delay_ms": 30000},
    "kill_after_step": {},
    "offline_during_step": {"duration_ms": 5000},
    "replay_callback": {"target": "service", "times": 3},
    "duplicate_request": {"times": 3},
}

CHANNELS: dict[str, dict] = {
    "手动测试": {"type": "manual", "judge": "vision-closed", "kind": "manual",
                 "strength": "人工", "evidence": ["screenshot"],
                 "reason": "用例表标为手动测试:真机表现/手感/多机型确认必须人工执行与判断"},
    "视觉判定": {"type": "device-android", "judge": "vision-closed", "kind": "judgment",
                 "strength": "视觉判定", "evidence": ["screenshot"],
                 "hint": "把这条期望改写成是/否封闭问题,并给出 region(0..1 归一化)"},
    "打点测试": {"type": "device-android", "judge": "state-report", "kind": "judgment",
                 "strength": "代码判定", "evidence": ["logcat", "state_report"],
                 "hint": "给出 path / op / value(例如 mergeOp.count eq 1);字段名以物品系统上报为准"},
    "配置检查": {"type": "manual", "judge": "vision-closed", "kind": "manual",
                 "strength": "工具扫描", "evidence": [],
                 "reason": "配置表静态交叉:check_config.py 的工具输出即证据,人复核时确认结论"},
    "确定性信号": {"type": "device-android", "judge": "vision-closed", "kind": "judgment",
                   "strength": "工具扫描", "evidence": ["logcat", "perf", "screenshot"],
                   "hint": "崩溃/白屏/卡死由 scan_anomalies.py 扫描;把期望写成是/否问题"},
}
# 从 CD 判定通道反查人话,给报告用
CHANNEL_NAMES = {"打点测试": "代码判定(state-report)", "视觉判定": "视觉判定(vision-closed)",
                 "配置检查": "工具扫描(check_config)", "确定性信号": "工具扫描(scan_anomalies)",
                 "手动测试": "人工(pending_manual)"}


def _pick(header: list[str], name: str) -> int | None:
    for alias in COLUMN_ALIASES.get(name, [name]):
        for i, h in enumerate(header):
            if h.strip().lstrip("\ufeff") == alias:
                return i
    return None


def split_items(text: str) -> list[str]:
    """把"1. …\\n2. …"这类单元格拆成条目;没有编号就按行拆。"""
    out: list[str] = []
    for ln in (text or "").replace("\r\n", "\n").split("\n"):
        if not ln.strip():
            continue
        m = NUMBERED_RE.match(ln)
        out.append((m.group(1) if m else ln.strip()).strip())
    return [x for x in out if x]


def parse_links(note: str) -> tuple[list[str], list[str], str]:
    """从备注里解析 `需求 M-01,M-02` 与 `关注点 C01、C14`,其余文字留作说明。"""
    # 需求编号的前缀由各家的需求文档决定(M-01 / B-03 / T-02 / R-3.1 …),
    # 所以这里只认「字母开头 + 数字」的形态,不写死成某一份文档的前缀。
    req_refs: list[str] = []
    for m in re.findall(r"需求\s*([A-Za-z][A-Za-z0-9_.\-]*(?:,[A-Za-z][A-Za-z0-9_.\-]*)*)",
                        note or ""):
        req_refs += [x.strip() for x in m.split(",") if x.strip()]
    concerns: list[str] = []
    for m in re.findall(r"关注点\s*([C\d、,\s]+)", note or ""):
        concerns += [x.strip() for x in re.split(r"[、,\s]+", m) if x.strip()]
    free = re.sub(r"需求\s*[A-Za-z][A-Za-z0-9_.\-,]*|关注点\s*[C\d、,\s]+"
                  r"|维度\s*[D\d,、\s]+|", "",
                  note or "").strip("；;。 ")
    return req_refs, concerns, free


def case_id_of(raw: str, module: str, prefix: str, invariant_modules: set[str]) -> str:
    """用例表里的编号 -> testflow 用例 id。已经是 REQ-/INV-/EXP-/CONS- 的原样保留。"""
    raw = (raw or "").strip()
    if KNOWN_ID_RE.match(raw):
        return raw
    pfx = "INV" if module in invariant_modules else prefix
    return f"{pfx}-{re.sub(r'[^A-Za-z0-9_.-]', '-', raw)}"


def read_table(path: Path) -> tuple[list[dict], list[str]]:
    """读用例表:认表头、继承合并单元格的模块名、拆编号行。"""
    problems: list[str] = []
    text = None
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return [], [f"{path} 编码认不出来(试过 utf-8-sig / gbk)"]
    # 用例表里常有"一格多行"的前置/步骤/期望 —— 必须整段交给 csv 解析,
    # 按行切开会把引号里的换行当成记录边界,解析出来全是碎的。
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return [], ["用例表是空的"]
    header = [c.strip() for c in rows[0]]
    idx = {name: _pick(header, name) for name in COLUMN_ALIASES}
    missing = [n for n in ("用例编号", "前置条件", "执行步骤", "预期结果") if idx[n] is None]
    if missing:
        return [], [f"表头认不出这几列:{missing}(表头={header[:12]})"]
    out: list[dict] = []
    module = ""
    for i, raw in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in raw):
            continue
        def cell(name: str) -> str:
            at = idx.get(name)
            return (raw[at].strip() if at is not None and at < len(raw) else "")
        if cell("模块"):
            module = cell("模块")                     # 合并单元格:空值继承上一条
        cid = cell("用例编号")
        if not cid:
            problems.append(f"第 {i} 行没有用例编号,已跳过")
            continue
        out.append({
            "line": i, "rawId": cid, "module": module, "title": cell("测试项") or cid,
            "pre": split_items(cell("前置条件")), "steps": split_items(cell("执行步骤")),
            "expects": split_items(cell("预期结果")), "method": cell("测试方法") or "手动测试",
            "priority": cell("优先级") or "P2", "note": cell("用例备注"),
        })
    return out, problems


def spec_text(row: dict, case_id: str, chan: dict, req_refs: list[str],
              concerns: list[str]) -> str:
    lines = [f"# 用例: {row['title']}", "",
             f"- **ID**: {case_id}",
             f"- **来源**: {'invariant' if case_id.startswith('INV-') else 'requirement'}",
             f"- **关联需求**: {','.join(req_refs) if req_refs else '—'}",
             f"- **关联关注点**: {','.join(concerns) if concerns else '—'}",
             f"- **用例表编号**: {row['rawId']}(来自 {row['module'] or '未归类'})",
             f"- **测试方法**: {row['method']} → {CHANNEL_NAMES.get(row['method'], '?')}",
             f"- **类型**: {chan['type']}",
             f"- **优先级**: {row['priority']}",
             f"- **判定模式**: {chan['judge']}", "",
             "## 前置条件", ""]
    for i, t in enumerate(row["pre"], 1):
        lines.append(f"- PRE-{i}: {t}")
    lines += ["", "## 操作步骤", ""]
    for i, t in enumerate(row["steps"], 1):
        lines.append(f"- S{i}: {t}")
    lines += ["", "## 期望结果", ""]
    for i, t in enumerate(row["expects"], 1):
        lines.append(f"- E{i}: {t}")
    if row["note"]:
        lines += ["", "## 用例表备注", "", f"- {row['note']}"]
    lines += ["", "---", "",
              "> 由 `import_cases.py` 从用例表机械转换:步骤/期望逐条对应原表编号行。",
              "> 判定项是骨架 —— 断言值、region、字段路径必须补完之后才允许执行。", ""]
    return "\n".join(lines)


def import_table(csv_path: Path, out_specs: Path, out_cases: Path,
                 prefix: str = "REQ", invariant_modules: set[str] | None = None,
                 device: dict | None = None, keep_existing: bool = True) -> dict:
    invariant_modules = invariant_modules or {"常识不变量"}
    rows, problems = read_table(csv_path)
    device = device or dict(DEFAULT_DEVICE)
    stats = {"rows": len(rows), "specs": 0, "cases": 0, "skipped": 0,
             "byMethod": {}, "concerns": {}, "reqRefs": {}, "problems": list(problems)}
    out_specs.mkdir(parents=True, exist_ok=True)
    out_cases.mkdir(parents=True, exist_ok=True)
    for row in rows:
        chan = CHANNELS.get(row["method"])
        if not chan:
            stats["problems"].append(
                f"{row['rawId']}: 测试方法 {row['method']!r} 认不出来,"
                f"可用值 {sorted(CHANNELS)}")
            continue
        case_id = case_id_of(row["rawId"], row["module"], prefix, invariant_modules)
        req_refs, concerns, _free = parse_links(row["note"])
        stats["byMethod"][row["method"]] = stats["byMethod"].get(row["method"], 0) + 1
        for c in concerns:
            stats["concerns"].setdefault(c, []).append(case_id)
        for r in req_refs:
            stats["reqRefs"].setdefault(r, []).append(case_id)

        spec_path = out_specs / f"{case_id}.md"
        case_path = out_cases / f"{case_id}.json"
        if keep_existing and (spec_path.exists() or case_path.exists()):
            stats["skipped"] += 1
            continue
        spec_path.write_text(spec_text(row, case_id, chan, req_refs, concerns),
                             encoding="utf-8")
        stats["specs"] += 1

        is_inv = case_id.startswith("INV-")
        spec_like = {"title": row["title"], "path": str(spec_path),
                     "meta": {"ID": case_id,
                              "来源": "invariant" if is_inv else "requirement",
                              # 不变量用例的期望来自常识,不是来自需求子句:
                              # 引用需求只能当参考,当成来源就会被校验器拦(SPURIOUS_REFS)
                              "关联需求": "" if is_inv else ",".join(req_refs),
                              "参考需求": ",".join(req_refs) if is_inv else "",
                              "类型": chan["type"], "优先级": row["priority"],
                              "判定模式": chan["judge"]},
                     "items": {f"PRE-{i}": t for i, t in enumerate(row["pre"], 1)}
                              | {f"S{i}": t for i, t in enumerate(row["steps"], 1)}
                              | {f"E{i}": t for i, t in enumerate(row["expects"], 1)}}
        case = build_case(spec_like, case_id, device)
        case["judge"] = chan["judge"]
        case["type"] = chan["type"]
        case["evidence"] = list(chan["evidence"])
        case["concerns"] = concerns
        case["method"] = row["method"]
        case["judgeStrength"] = chan["strength"]
        if is_inv:
            case.pop("req_refs", None)
        # 步骤里写了「故障注入:xxx」的,换成真的 fault 动作 —— 真机执行器会真去注入
        for i, text in enumerate(row["steps"]):
            m = FAULT_RE.search(text)
            if not m or m.group(1) not in FAULT_DEFAULTS:
                continue
            kind = m.group(1)
            prev = f"S{i}" if i else "S1"
            step = {"id": f"S{i + 1}", "action": "fault", "kind": kind,
                    "note": text, "_todo": "确认注入参数与目标(默认值只是占位)"}
            if kind in ("kill_after_step", "drop_response", "delay_response"):
                step["after_step"] = prev
            elif kind == "offline_during_step":
                step["around_step"] = prev
            elif kind == "duplicate_request":
                step["target_step"] = prev
            step.update(FAULT_DEFAULTS[kind])
            case["steps"][i] = {k: v for k, v in step.items() if v != ""}
        for chk in case["checks"]:
            chk["kind"] = chan["kind"]
            if chan["kind"] == "manual":
                chk.pop("question", None)
                chk["reason"] = chan["reason"]
            else:
                chk["_hint"] = chan["hint"]
                if chan["judge"] == "vision-closed":
                    chk["region"] = [0, 0, 1, 1]      # 全屏兜底,人/AI 复核时收紧
                    chk["answer"] = "yes"
        case["_todo"] = ([f"1) 判定项改成 {chan['kind']}:{chan.get('hint') or chan.get('reason')}"]
                         + case.get("_todo", [])[1:])
        case_path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        stats["cases"] += 1
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把既有测试用例表(CSV)导入 testflow:自然语言用例 + 派生用例骨架")
    ap.add_argument("--csv", type=Path, required=False, help="用例表 CSV")
    ap.add_argument("--out-specs", type=Path, help="自然语言用例输出目录(通常是 specs/)")
    ap.add_argument("--out-cases", type=Path, help="派生用例输出目录(通常是 03-cases/)")
    ap.add_argument("--prefix", default="REQ",
                    help=f"用例编号前缀(默认 REQ;表里已是 "
                         f"{'/'.join(p + '-' for p in CASE_ID_PREFIXES)} 的原样保留)")
    ap.add_argument("--invariant-modules", default="常识不变量,不变量,常识",
                    help="这些模块的用例按 INV- 前缀与来源 invariant 处理(逗号分隔)")
    ap.add_argument("--profile", default=DEFAULT_DEVICE["profile"], help="默认机型档案")
    ap.add_argument("--host", default=DEFAULT_DEVICE["host"], help="宿主(wechat / douyin)")
    ap.add_argument("--link-out", type=Path,
                    help="把「关注点 -> 用例」的对应关系写成 json(供登记册对账)")
    ap.add_argument("--replace", action="store_true",
                    help="已存在的用例文件也覆盖(默认不覆盖:人填过的比重新生成的值钱)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.csv or not args.out_specs or not args.out_cases:
        print("错误: 需要 --csv / --out-specs / --out-cases(或用 --selftest)",
              file=sys.stderr)
        return 2
    if not args.csv.exists():
        print(f"错误: 找不到 {args.csv}", file=sys.stderr)
        return 2

    inv = {m.strip() for m in args.invariant_modules.split(",") if m.strip()}
    device = dict(DEFAULT_DEVICE, profile=args.profile, host=args.host)
    stats = import_table(args.csv, args.out_specs, args.out_cases, args.prefix, inv,
                         device, keep_existing=not args.replace)
    for p in stats["problems"]:
        print(f"警告: {p}", file=sys.stderr)
    if not args.quiet:
        print(f"导入: {stats['rows']} 行 -> 自然语言用例 {stats['specs']} 份、"
              f"派生骨架 {stats['cases']} 份"
              + (f"(已存在跳过 {stats['skipped']} 份)" if stats["skipped"] else ""))
        print("  测试方法分布: " + "、".join(f"{k} {v}" for k, v in
                                            sorted(stats["byMethod"].items(),
                                                   key=lambda kv: -kv[1])))
        print(f"  挂到需求子句: {len(stats['reqRefs'])} 条;"
              f"挂到关注点: {len(stats['concerns'])} 条")
        print()
        print("**骨架不是用例**:每条判定项还要补断言值/region/字段路径,复核后把")
        print("review.state 改成 approved —— 否则执行阶段一律 blocked。")
        print(f"校验: python3 scripts/validate_cases.py --cases {args.out_cases} "
              f"--specs {args.out_specs} --requirements <01-requirements.json>")
    if args.link_out:
        args.link_out.parent.mkdir(parents=True, exist_ok=True)
        args.link_out.write_text(json.dumps(
            {"byConcern": stats["concerns"], "byRequirement": stats["reqRefs"],
             "byMethod": stats["byMethod"]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"关注点↔用例对应关系已写出: {args.link_out}")
    return 0 if not stats["problems"] else 1


# ---------------------------------------------------------------- 自测

SAMPLE = '''用例编号,模块,测试项,前置条件,执行步骤,预期结果,测试方法,优先级,用例备注,测试结果1
T-001,合成与成本,体力为 0 时合成成功,"1. 体力设为 0
2. 两件同物种同等级物品","1. 拖拽合成
2. 读体力","1. 合成成功
2. 体力仍为 0",打点测试,P0,"需求 M-04；维度 D2；关注点 C35,C83",
T-002,,非法操作不扣费,"1. 体力=0","1. 尝试异种合成","1. 回弹
2. 无资产事务",打点测试,P0,"需求 B-01；维度 D3；关注点 C38",
T-003,常识不变量,资产不得为负,"1. 金币=0","1. 合成一次
2. 读金币","1. 金币 >= 0",打点测试,P0,"需求 M-04；维度 D2；关注点 C83",
T-004,配置检查,merge_cost 必须为 0,"1. 有配置表","1. 跑 check_config","1. 全部为 0",配置检查,P0,"需求 M-04；维度 D10；关注点 C41",
'''



def selftest() -> int:
    import tempfile

    fails: list[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))
            fails.append(name)

    ck("拆条目: 编号行拆成条目,行内不编号也能拆",
       split_items("1. a\n2) b\n- c\n裸行") == ["a", "b", "c", "裸行"],
       str(split_items("1. a\n2) b\n- c\n裸行")))
    refs, conc, _ = parse_links("需求 M-04,M-05；维度 D2；关注点 C35、C83；风险:高")
    ck("解析备注: 需求与关注点都被抽出来",
       refs == ["M-04", "M-05"] and conc == ["C35", "C83"], f"{refs} {conc}")
    ck("编号映射: 表里的编号 + 前缀 -> testflow id;inV 模块走 INV-",
       case_id_of("T-001", "合成与成本", "REQ", {"常识不变量"}) == "REQ-T-001"
       and case_id_of("T-003", "常识不变量", "REQ", {"常识不变量"}) == "INV-T-003")

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        csv_path = td / "cases.csv"
        csv_path.write_text("\ufeff" + SAMPLE, encoding="utf-8")
        specs, cases = td / "specs", td / "03-cases"
        stats = import_table(csv_path, specs, cases, "REQ", {"常识不变量"},
                            dict(DEFAULT_DEVICE), keep_existing=False)
        ck("导入: 4 行 -> 4 份自然语言用例 + 4 份骨架",
           stats["specs"] == 4 and stats["cases"] == 4, str(stats["specs"]))
        spec = (specs / "REQ-T-001.md").read_text(encoding="utf-8")
        ck("自然语言用例: PRE/S/E 逐条来自用例表的编号行",
           "- PRE-1: 体力设为 0" in spec and "- S2: 读体力" in spec
           and "- E2: 体力仍为 0" in spec, spec[:400])
        ck("自然语言用例: 写清了测试方法映射到哪个通道",
           "打点测试 → 代码判定(state-report)" in spec)
        ck("模块继承: 合并单元格的空模块沿用上一条",
           "来自 合成与成本" in (specs / "REQ-T-002.md").read_text(encoding="utf-8"))
        case = json.loads((cases / "REQ-T-001.json").read_text(encoding="utf-8"))
        ck("派生用例: 判定模式与证据按通道填好(打点 -> state-report + logcat)",
           case["judge"] == "state-report" and "logcat" in case["evidence"]
           and case["type"] == "device-android", f"{case['judge']} {case['evidence']}")
        ck("派生用例: 仍是 draft + skeleton(闸门会拦住)",
           case["review"]["state"] == "draft" and case.get("skeleton") is True)
        ck("派生用例: req_refs 与 concerns 都进了机器可读字段",
           case["req_refs"] == ["M-04"] and case["concerns"] == ["C35", "C83"],
           f"{case['req_refs']} {case['concerns']}")
        case_b = json.loads((cases / "REQ-T-002.json").read_text(encoding="utf-8"))
        ck("需求编号不写死前缀: B-01 / M-04 / R-3.1 这类都能解析",
           case_b["req_refs"] == ["B-01"], str(case_b["req_refs"]))
        ck("派生用例: 每条 E 都有判定项占位(不会漏掉最难验的)",
           [c["source"] for c in case["checks"]] == ["E1", "E2"],
           str([c["source"] for c in case["checks"]]))
        manual = json.loads((cases / "INV-T-003.json").read_text(encoding="utf-8"))
        ck("不变量模块: 走 INV- 前缀,判定项仍是草稿",
           manual["id"] == "INV-T-003" and manual["review"]["state"] == "draft")
        cfg = json.loads((cases / "REQ-T-004.json").read_text(encoding="utf-8"))
        ck("配置检查: 判定项是 manual 并写明为什么必须人工/工具",
           all(c["kind"] == "manual" and c.get("reason") for c in cfg["checks"]),
           str(cfg["checks"]))

        # 不覆盖已有用例
        (cases / "REQ-T-001.json").write_text('{"id": "REQ-T-001", "human": true}',
                                             encoding="utf-8")
        stats2 = import_table(csv_path, specs, cases, "REQ", {"常识不变量"},
                             dict(DEFAULT_DEVICE), keep_existing=True)
        ck("已存在不覆盖(人补过的断言比骨架值钱)",
           "human" in (cases / "REQ-T-001.json").read_text(encoding="utf-8"))

        # 转换结果必须能过「期望覆盖率」这一关(骨架唯一的硬门槛)
        import subprocess
        r = subprocess.run([sys.executable, str(SKILL_DIR / "scripts" / "validate_cases.py"),
                            "--specs", str(specs), "--cases", str(cases)],
                           capture_output=True, text=True)
        ck("骨架能过期望覆盖率(只有待填内容会报错)",
           "MISSING_EXPECTATION_COVERAGE" not in r.stdout, r.stdout[-400:])
        ck("骨架仍被闸门拦住(未复核不许执行)",
           r.returncode == 1 and "未通过" in r.stdout, r.stdout[-200:])

    print()
    if fails:
        print(f"自测失败 {len(fails)} 项:")
        for x in fails:
            print("  ✗", x)
        return 1
    print("自测通过(编号行拆分/备注解析/编号映射/通道映射/模块继承/E 占位/"
          "draft 骨架/不覆盖已有/过期望覆盖率闸门)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
