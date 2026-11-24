#!/usr/bin/env python3
"""测试主干导图:把"主干 — 叶子 — 验证路径"对成一套,并做叶子反推主干的检查。

**方法**:测试主干(一个游戏跑起来的必经环节)是基础;每条叶子写明它靠什么验证。
子节点的描述会反过来暴露主干缺什么 —— 比如"重复支付"这条叶子逼出了
"A 档能力"(故障注入 + 状态上报),而 `replay` 这条叶子会暴露"回放引擎没有"。

    --map assets/backbone-map.md   主干导图(XMind / 幕布 / 语雀导出的缩进 Markdown 也能直接喂)
    --out backbone.md              渲染出的 Mermaid 导图 + 缺口清单

检查项(全部机械可查):

1. **未标注的叶子** —— 这条主干的验证路径还没定
2. **标了不存在的验证路径** —— 引用了不存在的判定模式/证据类型/动作/脚本(主干缺能力)
3. **手动/不支持却没写原因** —— "做不到"必须写清为什么,否则以后会被当成"偷懒"
4. **空主干** —— 一个主干节点下面一条叶子都没有
5. **没被任何叶子用到的工具** —— 工具存在但主干里没有它的位置(主干该补节点)

退出码:`0` = 没有缺口;`1` = 有缺口(逐条列出 —— 这些就是"主干待优化项");
`2` = 用法/解析错误。

**渲染成图片**(导图交给人的时候用):

```bash
# 1) 先导出 mermaid 块(本脚本 --out 的产物里就有)
# 2) 用官方 CLI 渲染;配置里的两点是踩过的坑,别改:
#    htmlLabels: false  -> 否则中文在无头浏览器里渲染成豆腐块
#    fontFamily 指定系统里的中文字体(Windows: Microsoft YaHei)
npx -y @mermaid-js/mermaid-cli -c mm.json -i backbone.mmd -o backbone.png -w 2400 -b white
# mm.json: {"themeVariables": {"fontFamily": "Microsoft YaHei, SimSun, sans-serif"},
#           "flowchart": {"htmlLabels": false}}
```

**另一个坑**:mermaid 的 mindmap 语法把方括号当"形状",叶子名后面跟 `[验证路径]`
会把整段名字吃掉 —— 所以本脚本输出的是 `叶子名 · 验证路径`,不用方括号(自测里有断言守着)。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_cases import ACTIONS, EVIDENCE_TYPES, JUDGE_MODES  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_MAP = SKILL_DIR / "assets" / "backbone-map.md"

# 编排/自检类工具:它们不绑定某条叶子,主动排除在"未被引用"检查之外
META_TOOLS = {"selftest.py", "check_docs.py", "pipeline.py", "backbone_map.py",
              "coordmap.py", "validate_cases.py", "review_sheet.py", "judge.py",
              "run_case.py", "scaffold_cases.py", "judge_live.py",
              # 用例从哪来的入口(既有的用例表导进来),不占叶子位置
              "import_cases.py"}
# 已知未实现的能力:标了它不算语法错,但算缺口(主干缺能力,不是写错)
KNOWN_UNIMPLEMENTED = {
    "replay": "判定模式 replay 未实现:回放引擎没有,判定只会返回 blocked(见 HANDOFF §二)"}

ANNOTATION_RE = re.compile(
    r"·\s*(verify|manual|unsupported|judge_only)\s*=\s*([^·]*?)\s*$")
BULLET_RE = re.compile(r"^(?P<indent>\s*)[-*+]\s+(?P<text>.+?)\s*$")


class Node:
    def __init__(self, name: str, depth: int) -> None:
        self.name = name
        self.depth = depth
        self.children: list["Node"] = []
        self.annotation: str | None = None
        self.value: str | None = None

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def path(self) -> str:
        return self.name


def parse_outline(text: str) -> list[Node]:
    """解析缩进 Markdown 成树。返回顶层节点(主干)。

    从思维导图工具导出的缩进列表基本就是这个形状,所以不要求任何特殊格式。
    """
    roots: list[Node] = []
    stack: list[Node] = []
    for raw in text.splitlines():
        m = BULLET_RE.match(raw)
        if not m:
            continue
        indent = len(m.group("indent").replace("\t", "    "))
        text_part = m.group("text")
        ann = ANNOTATION_RE.search(text_part)
        name = text_part
        value = None
        kind = None
        if ann:
            kind, value = ann.group(1), ann.group(2).strip()
            name = text_part[: ann.start()].strip()
        node = Node(name, indent)
        node.annotation, node.value = kind, value
        while stack and stack[-1].depth >= indent:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)
    return roots


def leaves(nodes: list[Node], prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Node]]:
    out: list[tuple[tuple[str, ...], Node]] = []
    for n in nodes:
        path = prefix + (n.name,)
        if n.is_leaf:
            out.append((path, n))
        else:
            out.extend(leaves(n.children, path))
    return out


def check_map(roots: list[Node]) -> tuple[list[dict], list[str]]:
    """叶子反推主干:返回 (缺口清单, 被引用到的工具名)。"""
    gaps: list[dict] = []
    used_tools: set[str] = set()

    def add(kind: str, where: str, note: str) -> None:
        gaps.append({"kind": kind, "where": where, "note": note})

    # 顶层节点按位置就是主干:没有子节点的,是"空主干"(而不是"一条没标注的叶子")
    checked_leaves: list[tuple[tuple[str, ...], Node]] = []
    for trunk in roots:
        if not trunk.children:
            add("empty_trunk", trunk.name, "主干节点下面一条叶子都没有 —— 要么补叶子,"
                                            "要么它不该出现在主干里")
            continue
        checked_leaves.extend(leaves([trunk]))

    for path, node in checked_leaves:
        where = " / ".join(path)
        if not node.annotation:
            add("unannotated_leaf", where,
                "没标「靠什么验证」—— 这条主干的验证路径还没定")
            continue
        if node.annotation == "verify":
            spec = node.value or ""
            if not spec.strip():
                add("bad_annotation", where, "verify= 后面没写值(应为 "
                                             "judge:/evidence:/action:/tool:)")
                continue
            kind, _, val = spec.partition(":")
            kind, val = kind.strip(), val.strip()
            if kind == "judge":
                if val not in JUDGE_MODES:
                    add("unknown_path", where, f"判定模式 {val!r} 不存在")
                elif val in KNOWN_UNIMPLEMENTED:
                    add("unimplemented", where, KNOWN_UNIMPLEMENTED[val])
            elif kind == "evidence":
                if val not in EVIDENCE_TYPES:
                    add("unknown_path", where, f"证据类型 {val!r} 不存在")
            elif kind == "action":
                if val not in ACTIONS:
                    add("unknown_path", where, f"动作 {val!r} 不在动作词汇表里")
            elif kind == "tool":
                script = SKILL_DIR / "scripts" / val
                if not script.exists():
                    add("unknown_path", where, f"脚本 scripts/{val} 不存在(主干缺工具)")
                else:
                    used_tools.add(val)
            else:
                add("bad_annotation", where, f"verify 的取值 {spec!r} 认不出来"
                                             f"(应为 judge:/evidence:/action:/tool:)")
        else:
            if not (node.value or "").strip():
                add("no_reason", where,
                    f"{node.annotation} 必须写原因 —— 「做不到」不写清为什么,"
                    f"以后会被当成偷懒")

    return gaps, used_tools


def unused_tools(used: set[str]) -> list[str]:
    """有工具,但主干里没有它的位置 —— 主干该补节点(叶子反推主干)。"""
    out = []
    for p in sorted((SKILL_DIR / "scripts").glob("*.py")):
        if p.name in used or p.name in META_TOOLS:
            continue
        out.append(p.name)
    return out


def render(roots: list[Node], gaps: list[dict], unused: list[str], src: Path) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    n_leaves = len(leaves(roots))
    auto = 0
    for _, node in leaves(roots):
        if node.annotation == "verify":
            auto += 1
    lines = [
        "# 测试主干导图",
        "",
        f"- 生成时间: {now}",
        f"- 来源: `{src}`",
        f"- 主干节点 {len(roots)} 个,叶子 {n_leaves} 条(其中 {auto} 条有自动化验证路径)",
        f"- 缺口 {len(gaps)} 项、主干未安置的工具 {len(unused)} 个",
        "",
        "> 主干是基础,叶子写清「靠什么验证」。**缺口 = 主干待优化项** ——",
        "> 叶子暴露出来的能力缺失,回头补到主干上(比如「重复支付」逼出了故障注入动作,",
        "> 「回放回归」暴露了回放引擎没实现)。",
        "",
        "```mermaid",
        "mindmap",
        "  root((测试主干))",
    ]

    def tag(node: Node) -> str:
        if node.children:
            return ""                     # 主干与中间层不标验证路径,只有叶子标
        if node.annotation == "verify":
            return (node.value or "").replace(":", " ")[:24]
        return {"manual": "人工", "unsupported": "平台不支持",
                "judge_only": "只可看"}.get(node.annotation or "", "未标注")

    def emit(nodes: list[Node], indent: int) -> None:
        for n in nodes:
            # 标签里不能出现 ()[]{},更不能把验证路径用方括号括起来 ——
            # mindmap 语法把方括号当"形状",会把叶子名整段吃掉(实测踩过)。
            label = re.sub(r"[()\[\]{}]", " ", n.name).strip()
            t = tag(n).replace(":", " ")
            lines.append("  " * indent + (f"{label} · {t}" if t else label))
            emit(n.children, indent + 1)

    emit(roots, 2)
    lines += ["```", "", "## 主干与叶子", ""]
    for trunk in roots:
        lines.append(f"### {trunk.name}")
        lv = leaves([trunk])
        if not lv:
            lines.append("- (空主干)")
        for path, node in lv:
            branch = " / ".join(path[1:-1])
            ann = (f"`{node.value}`" if node.annotation == "verify"
                   else f"**{node.annotation}** —— {node.value}")
            lines.append(f"- {node.name} — {ann}" + (f"({branch})" if branch else ""))
        lines.append("")

    lines += ["## 缺口(主干待优化项)", ""]
    if not gaps:
        lines.append("- 无")
    else:
        for g in gaps:
            lines.append(f"- [{g['kind']}] {g['where']} — {g['note']}")
    lines += ["", "## 主干未安置的工具(叶子反推主干)", ""]
    if not unused:
        lines.append("- 无")
    else:
        for name in unused:
            lines.append(f"- `{name}` 存在但没有任何叶子用它 —— "
                         f"要么主干缺一个节点,要么它该进 META 名单")
    lines += [
        "",
        "## 怎么迭代",
        "",
        "1. 改叶子(新发现的可测点/新暴露的能力缺失) → 跑一遍本工具",
        "2. 看缺口:未标注、路径不存在、做不到没写原因 —— 逐条给结论",
        "3. 缺口里凡是「没有能力支撑」的,回到主干补节点或补工具(而不是把叶子删掉)",
        "4. 交接前再跑:主干与叶子必须对得上,否则导图就是一张画",
        "",
    ]
    return "\n".join(lines)


def load_map(path: Path) -> list[Node]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        def build(items: list[dict], depth: int) -> list[Node]:
            out = []
            for it in items:
                n = Node(str(it.get("name") or "?"), depth)
                raw = str(it.get("verify") or "")
                if raw:
                    n.annotation, n.value = "verify", raw
                elif it.get("manual"):
                    n.annotation, n.value = "manual", str(it["manual"])
                elif it.get("unsupported"):
                    n.annotation, n.value = "unsupported", str(it["unsupported"])
                n.children = build(it.get("children") or [], depth + 2)
                out.append(n)
            return out
        return build(data.get("trunk") or data if isinstance(data, list)
                     else data.get("trunk") or [], 0)
    return parse_outline(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="测试主干导图:校验「主干—叶子—验证路径」是否对得上,并做叶子反推主干")
    ap.add_argument("--map", type=Path, default=DEFAULT_MAP,
                    help=f"导图文件(缩进 Markdown 或 JSON);默认 {DEFAULT_MAP}")
    ap.add_argument("--out", type=Path, help="渲染结果(Mermaid + 缺口清单)输出路径")
    ap.add_argument("--json", type=Path, help="机器可读结果")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not args.map.exists():
        print(f"错误: 找不到导图 {args.map}", file=sys.stderr)
        return 2
    try:
        roots = load_map(args.map)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"错误: 导图解析失败: {exc}", file=sys.stderr)
        return 2
    if not roots:
        print(f"错误: {args.map} 里没解析出任何节点(需要缩进的无序列表)", file=sys.stderr)
        return 2

    gaps, used = check_map(roots)
    unused = unused_tools(used)
    report = render(roots, gaps, unused, args.map)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"trunk": len(roots), "leaves": len(leaves(roots)),
             "gaps": gaps, "unusedTools": unused},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.quiet:
        print(f"主干导图: 主干 {len(roots)} 个、叶子 {len(leaves(roots))} 条;"
              f"缺口 {len(gaps)} 项、未安置工具 {len(unused)} 个")
        for g in gaps:
            print(f"  [{g['kind']:14s}] {g['where']} — {g['note']}")
        for name in unused:
            print(f"  [unused_tool   ] scripts/{name} — 主干里没有它的位置")
        if args.out:
            print(f"渲染已写出: {args.out}")
        if not gaps and not unused:
            print("主干与叶子对得上:每条叶子都有可执行的验证路径。")
    return 1 if (gaps or unused) else 0


# ---------------------------------------------------------------- 自测

def selftest() -> int:
    import tempfile

    fails: list[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))
            fails.append(name)

    clean = """# 标题行(不算节点)
- 启动
  - 冷启动耗时 · verify=evidence:logcat
  - 崩溃 · verify=evidence:logcat
- 结算
  - 数值正确 · verify=evidence:state_report
  - 手感 · manual=主观体验只能人工
  - 多指操作 · unsupported=adb 不支持多指
"""
    roots = parse_outline(clean)
    gaps, used = check_map(roots)
    ck("解析缩进列表:2 个主干、5 条叶子",
       len(roots) == 2 and len(leaves(roots)) == 5, f"{len(roots)}/{len(leaves(roots))}")
    ck("干净导图无缺口", not gaps, str(gaps))
    ck("引用到的工具被记下来(evidence 不算工具)", used == set(), str(used))

    messy = """- A 主干
  - 叶子一
  - 叶子二 · verify=judge:telepathy
  - 叶子三 · verify=tool:not_exist.py
  - 叶子四 · verify=judge:replay
  - 叶子五 · verify=magic:xxx
  - 叶子六 · manual=
  - 叶子七 · verify=action:tap
- B 空主干
- D 主干
  - C 中间层
"""
    roots2 = parse_outline(messy)
    gaps2, _ = check_map(roots2)
    kinds = {g["kind"] for g in gaps2}
    ck("未标注的叶子被报出来", "unannotated_leaf" in kinds, str(sorted(kinds)))
    ck("不存在的判定模式被报出来", "unknown_path" in kinds)
    ck("replay 算「主干缺能力」不是语法错",
       any(g["kind"] == "unimplemented" and "replay" in g["where"] + g["note"]
           for g in gaps2), str([g for g in gaps2 if g["kind"] == "unimplemented"]))
    ck("manual 没写原因被报出来(空值也算,不是「未标注」)",
       "no_reason" in kinds
       and any(g["kind"] == "no_reason" and "叶子六" in g["where"] for g in gaps2),
       str([g for g in gaps2 if g["kind"] == "no_reason"]))
    ck("认不出的标注被报出来", "bad_annotation" in kinds)
    ck("空主干被报出来(节点下面一条叶子都没有)",
       "empty_trunk" in kinds
       and any("B 空主干" in g["where"] for g in gaps2), str(sorted(kinds)))
    ck("合法的 action 引用不报错",
       not any("叶子七" in g["where"] for g in gaps2),
       str([g for g in gaps2 if "叶子七" in g["where"]]))

    # 未安置工具:造一个没人引用的脚本名来验证逻辑(不真建文件,直接查函数)
    unused = unused_tools({"check_config.py", "scan_anomalies.py"})
    ck("未被引用的工具能被列出来(且排除编排/自检类)",
       "check_config.py" not in unused and "selftest.py" not in unused
       and "scan_idempotency.py" in unused, str(unused))

    rep = render(roots, [], [], Path("x.md"))
    ck("渲染含 mermaid mindmap 与迭代说明",
       "```mermaid" in rep and "mindmap" in rep and "怎么迭代" in rep)
    mm = rep.split("```mermaid")[1].split("```")[0]
    ck("mermaid 块里不含方括号(mindmap 会把 [..] 当形状,吞掉叶子名)",
       "[" not in mm and "]" not in mm, str([ln for ln in mm.splitlines() if "[" in ln][:2]))
    ck("叶子名与验证路径都出现在图里",
       "冷启动耗时" in mm and "logcat" in mm, mm[:120])

    with tempfile.TemporaryDirectory() as td:
        json_map = Path(td) / "m.json"
        json_map.write_text(json.dumps({"trunk": [
            {"name": "启动", "children": [
                {"name": "耗时", "verify": "evidence:logcat"},
                {"name": "手感", "manual": "人工"}]}]}, ensure_ascii=False),
            encoding="utf-8")
        roots3 = load_map(json_map)
        g3, _ = check_map(roots3)
        ck("JSON 格式的导图也能读", len(leaves(roots3)) == 2 and not g3, str(g3))

    print()
    if fails:
        print(f"自测失败 {len(fails)} 项:")
        for x in fails:
            print("  ✗", x)
        return 1
    print("自测通过(缩进/JSON 解析;未标注/未知路径/manual 缺原因/空主干/未安置工具;"
          "replay 记为缺能力;Mermaid 渲染)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
